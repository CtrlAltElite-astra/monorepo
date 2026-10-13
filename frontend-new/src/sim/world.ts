// The in-browser simulator of three SyncShield nodes (MOCK MODE). It is a fake BACKEND: it speaks the same REST
// paths as the real regions (see sim/api.ts), so the SDK and the UI run unchanged against it. Everything on
// screen in mock mode carries a "demo data" chip.
import type { EventEnvelope, Level, Power, RegionId } from '../api/types'
import { canonical, clamp } from '../lib/util'
import { eventHash, sign, verify } from './crypto'
import { derive, order, type Derived } from './derive'

export const REGION_NAMES: Record<RegionId, string> = { mum: 'Mumbai', pun: 'Pune', del: 'Delhi', blr: 'Bengaluru' }
/** every region the simulator knows how to run; Bengaluru is configured but only joins when someone adds it (contract 8.2) */
export const ALL_REGIONS: RegionId[] = ['mum', 'pun', 'del', 'blr']
/** the regions a fresh mesh starts with */
export const CORE_REGIONS: RegionId[] = ['mum', 'pun', 'del']

/** one-way delivery latency per pair, ms (geography: Mumbai-Pune close, Delhi far, Bengaluru in the south) */
const LATENCY: Record<string, number> = { 'mum-pun': 450, 'pun-del': 700, 'del-mum': 1100, 'blr-mum': 650, 'blr-pun': 600, 'blr-del': 1250 }
export const latencyOf = (a: string, b: string) => LATENCY[[a, b].sort().join('-')] ?? LATENCY[[b, a].join('-')] ?? 600

// ---- power and the commit queue (contract 8.1, 8.4) --------------------------------------------------------------
/** writes saved per batch for each power preset */
export const POWER_BATCH: Record<Power, number> = { low: 1, medium: 8, high: 32 }
/** One commit costs a fixed amount (the fsync) plus a little per write, so a bigger batch is cheaper per write but never free:
 *  throughput climbs steeply from low to medium and then flattens. These are simulated costs, not measurements. */
export const COMMIT_FIXED_MS = 12
export const COMMIT_PER_WRITE_MS = 1.5
/** time the commit loop spends per write when batches are full */
export const serviceMs = (batch: number) => (COMMIT_FIXED_MS + COMMIT_PER_WRITE_MS * batch) / batch
/** A region that turns a write away asks the caller to leave it alone for this long (the Retry-After of a 503). */
export const REGION_RETRY_AFTER_S = 2
/** retry_after_ms of a bucket that never refills: effectively never, one hour */
export const NEVER_MS = 3_600_000

export interface TokenDecision { allowed: boolean; limit: number; remaining: number; retryAfterMs: number }

export interface SimOptions {
  /** nodes that are part of the mesh at the start; the others can join later (Delhi joins empty) */
  joined?: RegionId[]
  tickMs?: number
  pullMs?: number
  /** a peer silent for this long makes the node ISOLATED (the real backend uses 5 s; shorter keeps the demo snappy) */
  isolateAfterMs?: number
}

export type LinkMode = 'up' | 'down' | 'slow'
export interface LinkState { mode: LinkMode; delay_ms: number; rate_bps: number }

interface QueueEntry { event: EventEnvelope; readyAt: number; size: number }
interface PeerHealth { lastOkAt: number | null; fails: number; unreachableSince: number | null }

export interface HealSummary { isolatedMs: number; eventsReceived: number; conflicts: number; reviews: number; at: number }

/** Hybrid logical clock merge on receive: never goes backwards, always after both clocks. */
export function hlcReceive(local: [number, number], remote: [number, number], now: number): [number, number] {
  const l = Math.max(local[0], remote[0], now)
  if (l === local[0] && l === remote[0]) return [l, Math.max(local[1], remote[1]) + 1]
  if (l === local[0]) return [l, local[1] + 1]
  if (l === remote[0]) return [l, remote[1] + 1]
  return [l, 0]
}

export class SimNode {
  up = true
  booting = false
  seq = 0
  hlc: [number, number] = [0, 0]
  events = new Map<string, EventEnvelope>()
  byOp = new Map<string, string>()
  contiguous: Record<string, number> = {}
  peerView: Record<string, Record<string, number>> = {}
  health: Record<string, PeerHealth> = {}
  links: Record<string, LinkState> = {}
  queue: Record<string, QueueEntry[]> = {}
  budget: Record<string, number> = {}
  quarantine: { event_id: string | null; reason: string; at: number }[] = []
  carried = new Set<string>()
  carry = { received: 0, new: 0, dup: 0, rejected: 0 }
  bytesSent: Record<string, number> = {}
  bytesReceived: Record<string, number> = {}
  duplicates = 0
  commitMs: number[] = []
  recovering = false
  recoveryStart = 0
  isolatedAt: number | null = null
  eventsAtRecoveryStart = 0
  lastHeal: HealSummary | null = null
  lastCatchUpMs: number | null = null

  // ---- scaling and limits (all off or "medium" until someone changes them, like the real settings) ------------
  power: Power = 'medium'
  /** per-actor token bucket: `actorBurst` tokens, `actorRefill` more per second; 0 burst = off */
  actorBurst = 0
  actorRefill = 1
  /** writes this region accepts per second from clients; 0 = off */
  regionLimit = 0
  refusedActor = 0
  refusedRegion = 0
  private buckets = new Map<string, { tokens: number; at: number }>()
  private acceptedAt: number[] = []
  private busyUntil = 0
  private pending: number[] = []

  // the log is only ever appended to, so everything derived from it can be cached by how much of it there is
  private cache = new Map<string, { n: number; view: Derived }>()
  private byReport = new Map<string, EventEnvelope[]>()
  private reportVersion = new Map<string, number>()
  private memos = new Map<string, { size: number; value: unknown }>()

  constructor(public id: RegionId) {}

  /** A value derived from the whole log (digest, state hash), recomputed only after the log has grown. */
  memo<T>(key: string, compute: () => T): T {
    const hit = this.memos.get(key)
    if (hit && hit.size === this.events.size) return hit.value as T
    const value = compute()
    this.memos.set(key, { size: this.events.size, value })
    return value
  }

  // ---- the per-actor limit: the same bucket as region/api/ratelimit.py -------------------------------------------
  private tokensOf(actor: string, now: number): number {
    const b = this.buckets.get(actor)
    return b ? Math.min(this.actorBurst, b.tokens + Math.max(0, now - b.at) / 1000 * this.actorRefill) : this.actorBurst
  }

  /** Spend one token of `actor`. null while the limit is off. */
  takeToken(actor: string, now: number): TokenDecision | null {
    if (this.actorBurst <= 0) { this.buckets.clear(); return null }
    let tokens = this.tokensOf(actor, now)
    if (tokens >= 1 - 1e-9) {
      tokens = Math.max(0, tokens - 1)
      this.buckets.set(actor, { tokens, at: now })
      return { allowed: true, limit: this.actorBurst, remaining: Math.floor(tokens + 1e-9), retryAfterMs: 0 }
    }
    this.buckets.set(actor, { tokens, at: now })
    this.refusedActor++
    return { allowed: false, limit: this.actorBurst, remaining: 0, retryAfterMs: this.actorRefill > 0 ? Math.max(1, Math.ceil((1 - tokens) / this.actorRefill * 1000)) : NEVER_MS }
  }

  /** The actor's allowance without spending anything (a replayed write carries it). */
  peekToken(actor: string, now: number): TokenDecision | null {
    if (this.actorBurst <= 0) return null
    return { allowed: true, limit: this.actorBurst, remaining: Math.floor(this.tokensOf(actor, now) + 1e-9), retryAfterMs: 0 }
  }

  // ---- the region limit: writes accepted from clients in the last second -----------------------------------------
  /** The Retry-After (whole seconds) when the region is at its limit; 0 when it can take the write. */
  regionRetryS(now: number): number {
    if (this.regionLimit <= 0) return 0
    const recent = this.acceptedAt.filter((t) => now - t < 1000)
    return recent.length < this.regionLimit ? 0 : REGION_RETRY_AFTER_S
  }

  noteAccepted(now: number) {
    this.acceptedAt.push(now)
    if (this.acceptedAt.length > 4000 || (this.acceptedAt[0] ?? now) < now - 5000) this.acceptedAt = this.acceptedAt.filter((t) => now - t < 3000)
  }

  // ---- the commit queue: how power changes what a burst feels like -----------------------------------------------
  /** Queue one write for commit; returns how long its acknowledgement is held back (ms). An idle region costs one whole
   *  commit, whatever the power; a busy one makes each write wait for the ones ahead of it. */
  commit(now: number): number {
    const batch = POWER_BATCH[this.power]
    const svc = serviceMs(batch)
    const start = Math.max(now, this.busyUntil)
    const wait = start - now
    this.busyUntil = start + svc
    this.pending.push(this.busyUntil)
    return wait + svc + (wait === 0 ? COMMIT_FIXED_MS * (1 - 1 / batch) : 0)
  }

  queueDepth(now: number): number {
    if (this.pending.length && this.pending[0] <= now) this.pending = this.pending.filter((t) => t > now)
    return this.pending.length
  }

  /** accepted client writes per second over the last two seconds */
  writesPerS(now: number): number {
    return Math.round(this.acceptedAt.filter((t) => now - t < 2000).length / 2 * 10) / 10
  }

  view(reportId: string): Derived {
    const version = this.reportVersion.get(reportId) ?? 0
    const hit = this.cache.get(reportId)
    if (hit && hit.n === version) return hit.view
    const view = derive(this.byReport.get(reportId) ?? [])
    this.cache.set(reportId, { n: version, view })
    return view
  }

  /** a copy: callers sort it */
  eventsOf(reportId: string): EventEnvelope[] {
    return [...(this.byReport.get(reportId) ?? [])]
  }

  reportIds(): string[] {
    return [...this.byReport.keys()]
  }

  /** newest CREATE first; reports still waiting for their CREATE are hidden */
  listIds(): string[] {
    const created = [...this.events.values()].filter((e) => e.type === 'CREATE').sort((a, b) => order(b, a))
    return created.map((e) => e.report_id)
  }

  head(origin: string): number { return origin === this.id ? this.seq : this.contiguous[origin] ?? 0 }

  heads(origins: string[]): Record<string, number> {
    const out: Record<string, number> = {}
    for (const o of origins) out[o] = this.head(o)
    return out
  }

  private advance(origin: string) {
    let n = this.contiguous[origin] ?? 0
    while (this.events.has(`${origin}:${n + 1}`)) n++
    this.contiguous[origin] = n
  }

  insert(ev: EventEnvelope): void {
    if (!this.events.has(ev.event_id)) {
      const list = this.byReport.get(ev.report_id)
      if (list) list.push(ev); else this.byReport.set(ev.report_id, [ev])
    }
    this.events.set(ev.event_id, ev)
    this.reportVersion.set(ev.report_id, (this.reportVersion.get(ev.report_id) ?? 0) + 1)
    if (ev.op_id && !this.byOp.has(ev.op_id)) this.byOp.set(ev.op_id, ev.event_id)
    if (ev.origin === this.id) this.seq = Math.max(this.seq, ev.seq)
    this.advance(ev.origin)
  }

  link(peer: string): LinkState { return (this.links[peer] ||= { mode: 'up', delay_ms: 0, rate_bps: 0 }) }

  /** events of THIS node that no peer has acknowledged: what a crash right now would lose */
  unreplicated(peers: string[]): EventEnvelope[] {
    const acked = Math.max(0, ...peers.map((p) => this.peerView[p]?.[this.id] ?? 0))
    return [...this.events.values()].filter((e) => e.origin === this.id && e.seq > acked)
  }
}

export class SimWorld {
  nodes = new Map<RegionId, SimNode>()
  joined: RegionId[]
  tickMs: number
  pullMs: number
  isolateAfterMs: number
  clockSkew = 0
  private timer: ReturnType<typeof setInterval> | null = null
  private lastPull = 0
  private acks: { at: number; from: RegionId; to: RegionId; origin: string; seq: number }[] = []
  private listeners = new Set<() => void>()

  constructor(opts: SimOptions = {}) {
    this.joined = [...(opts.joined ?? CORE_REGIONS)]
    this.tickMs = opts.tickMs ?? 100
    this.pullMs = opts.pullMs ?? 1000
    this.isolateAfterMs = opts.isolateAfterMs ?? 1500
    for (const id of ALL_REGIONS) this.nodes.set(id, new SimNode(id))
  }

  node(id: RegionId): SimNode { return this.nodes.get(id)! }
  peersOf(id: RegionId): RegionId[] { return this.joined.filter((r) => r !== id) }
  isJoined(id: RegionId) { return this.joined.includes(id) }
  onChange(fn: () => void) { this.listeners.add(fn); return () => this.listeners.delete(fn) }
  private emit() { this.listeners.forEach((fn) => fn()) }

  start() { if (!this.timer) this.timer = setInterval(() => this.tick(), this.tickMs) }
  stop() { if (this.timer) clearInterval(this.timer); this.timer = null }

  // ---- membership and process control -------------------------------------------------------------
  /** A new region (Delhi, or Bengaluru) joins as an EMPTY node and catches up by replication (A10, contract 8.2). */
  join(id: RegionId) {
    if (this.joined.includes(id)) return
    this.joined.push(id)
    const n = this.node(id)
    n.up = false
    n.booting = true
    setTimeout(() => { n.booting = false; n.up = true; this.beginRecovery(n) }, 800)
    this.emit()
  }

  crash(id: RegionId) {
    const n = this.node(id)
    n.up = false
    n.booting = false
    for (const p of Object.keys(n.queue)) n.queue[p] = [] // the in-memory push queue dies with the process
    this.acks = this.acks.filter((a) => a.from !== id && a.to !== id)
    this.emit()
  }

  restart(id: RegionId) {
    const n = this.node(id)
    if (n.up || n.booting) return
    n.booting = true
    setTimeout(() => { n.booting = false; n.up = true; n.health = {}; this.beginRecovery(n); this.emit() }, 900) // replays its log
    this.emit()
  }

  private beginRecovery(n: SimNode) {
    n.recovering = true
    n.recoveryStart = Date.now()
    n.eventsAtRecoveryStart = n.events.size
  }

  reachable(a: RegionId, b: RegionId): boolean {
    const na = this.node(a), nb = this.node(b)
    if (!na.up || !nb.up) return false
    return na.link(b).mode !== 'down' && nb.link(a).mode !== 'down'
  }

  /** the slower of the two ends decides a slow link */
  effectiveLink(a: RegionId, b: RegionId): LinkState {
    const x = this.node(a).link(b), y = this.node(b).link(a)
    if (x.mode === 'slow' || y.mode === 'slow') {
      const rates = [x, y].filter((l) => l.mode === 'slow' && l.rate_bps > 0).map((l) => l.rate_bps)
      return { mode: 'slow', delay_ms: Math.max(x.delay_ms, y.delay_ms), rate_bps: rates.length ? Math.min(...rates) : 2000 }
    }
    return { mode: 'up', delay_ms: 0, rate_bps: 0 }
  }

  // ---- writes ---------------------------------------------------------------------------------------
  /** Build, hash, sign and store one event at `id`, then queue it for every peer (the push fast path). */
  append(id: RegionId, partial: Omit<EventEnvelope, 'v' | 'event_id' | 'origin' | 'seq' | 'hlc' | 'hash' | 'sig' | 'wall' | 'priority'>, priority: 0 | 1 | 2): EventEnvelope {
    const n = this.node(id)
    const t0 = performance.now()
    n.seq += 1
    const now = Date.now() + this.clockSkew
    const [l, c] = n.hlc
    n.hlc = now > l ? [now, 0] : [l, c + 1]
    const ev: EventEnvelope = {
      v: 1, event_id: `${id}:${n.seq}`, origin: id, seq: n.seq, hlc: [...n.hlc], priority,
      wall: new Date(now).toISOString(), hash: '', sig: null, ...partial,
    }
    ev.hash = eventHash(ev)
    ev.sig = sign(id, ev.hash)
    n.insert(ev)
    n.commitMs.push(2 + (performance.now() - t0) + (n.seq % 5))
    if (n.commitMs.length > 200) n.commitMs.shift()
    this.enqueueFor(n, ev)
    return ev
  }

  private enqueueFor(n: SimNode, ev: EventEnvelope) {
    for (const p of this.peersOf(n.id)) this.enqueue(n, p, ev)
  }

  private enqueue(n: SimNode, peer: RegionId, ev: EventEnvelope) {
    const q = (n.queue[peer] ||= [])
    if (q.some((x) => x.event.event_id === ev.event_id)) return
    q.push({ event: ev, readyAt: Date.now() + latencyOf(n.id, peer), size: canonical(ev).length })
    // dependency promotion: a critical event drags its CREATE along at the same priority, or the peer would hide the report
    if (ev.priority === 0 && ev.type !== 'CREATE') {
      const create = n.eventsOf(ev.report_id).find((e) => e.type === 'CREATE')
      if (create && !q.some((x) => x.event.event_id === create.event_id)) q.push({ event: { ...create, priority: 0 }, readyAt: Date.now() + latencyOf(n.id, peer), size: canonical(create).length })
    }
  }

  // ---- receiving (push, pull and carry all come through here) --------------------------------------
  ingest(id: RegionId, ev: EventEnvelope, source: 'push' | 'pull' | 'carry'): 'new' | 'dup' | 'bad_shape' | 'bad_hash' | 'bad_sig' | 'unknown_origin' | 'id_reused' {
    const n = this.node(id)
    if (!ev || typeof ev !== 'object' || !ev.event_id || ev.event_id !== `${ev.origin}:${ev.seq}` || !Array.isArray(ev.supersedes) || !ev.hlc || !ev.report_id) {
      n.quarantine.push({ event_id: ev?.event_id ?? null, reason: 'bad_shape', at: Date.now() })
      return 'bad_shape'
    }
    if (eventHash(ev) !== ev.hash) { n.quarantine.push({ event_id: ev.event_id, reason: 'bad_hash', at: Date.now() }); return 'bad_hash' }
    if (!this.joined.includes(ev.origin as RegionId)) { n.quarantine.push({ event_id: ev.event_id, reason: 'unknown_origin', at: Date.now() }); return 'unknown_origin' }
    if (!verify(this.joined, ev.origin, ev.hash, ev.sig)) { n.quarantine.push({ event_id: ev.event_id, reason: 'bad_sig', at: Date.now() }); return 'bad_sig' }
    const have = n.events.get(ev.event_id)
    if (have) {
      if (have.hash !== ev.hash) { n.quarantine.push({ event_id: ev.event_id, reason: 'id_reused', at: Date.now() }); return 'id_reused' }
      n.duplicates++
      return 'dup'
    }
    if (ev.origin === id) { n.duplicates++; return 'dup' } // never import our own history from outside (seq reuse guard)
    n.insert({ ...ev, sig_status: undefined, carried: undefined })
    n.hlc = hlcReceive(n.hlc, ev.hlc, Date.now() + this.clockSkew)
    if (source === 'carry') n.carried.add(ev.event_id)
    return 'new'
  }

  // ---- time: delivery, anti-entropy, health, ladder ---------------------------------------------
  tick() {
    const now = Date.now()
    this.processAcks(now)
    const doPull = now - this.lastPull >= this.pullMs
    if (doPull) this.lastPull = now
    for (const id of this.joined) {
      const n = this.node(id)
      if (!n.up) continue
      for (const p of this.peersOf(id)) {
        const h = (n.health[p] ||= { lastOkAt: null, fails: 0, unreachableSince: null })
        if (this.reachable(id, p)) {
          if (h.unreachableSince !== null && now - h.unreachableSince >= this.isolateAfterMs) this.onHeal(n, h, now)
          h.lastOkAt = now; h.fails = 0; h.unreachableSince = null
          if (doPull) this.summaryExchange(n, this.node(p))
        } else {
          h.fails += 1
          h.unreachableSince ??= now
          if (n.isolatedAt === null && now - h.unreachableSince >= this.isolateAfterMs) n.isolatedAt = h.unreachableSince
        }
      }
      for (const p of this.peersOf(id)) this.drain(n, p, now)
      this.checkRecovered(n, now)
    }
    this.emit()
  }

  private onHeal(n: SimNode, h: PeerHealth, now: number) {
    n.recovering = true
    n.recoveryStart = now
    n.eventsAtRecoveryStart = n.events.size
    h.unreachableSince = null
  }

  /** The pull side of anti-entropy: both nodes learn each other's heads and queue whatever the other lacks. */
  private summaryExchange(a: SimNode, b: SimNode) {
    const origins = this.joined as string[]
    a.peerView[b.id] = { ...a.peerView[b.id], ...b.heads(origins) }
    for (const o of origins) {
      const theirs = b.head(o)
      // queue (in seq order) every event of origin o that b lacks, drawn from whatever a holds, relay included
      for (let s = theirs + 1; s <= a.head(o); s++) {
        const ev = a.events.get(`${o}:${s}`)
        if (ev) this.enqueue(a, b.id as RegionId, ev)
      }
    }
  }

  private drain(n: SimNode, peer: RegionId, now: number) {
    const q = n.queue[peer]
    if (!q?.length || !this.reachable(n.id, peer)) return
    const link = this.effectiveLink(n.id, peer)
    // priority first (0 critical, 1 normal, 2 bulk), then clock order; ties never starve because pull refills in seq order
    q.sort((x, y) => x.event.priority - y.event.priority || order(x.event, y.event))
    let budget = Infinity
    if (link.mode === 'slow') {
      n.budget[peer] = Math.min((n.budget[peer] ?? 0) + (link.rate_bps * this.tickMs) / 1000, Math.max(link.rate_bps, 1200))
      budget = n.budget[peer]
    }
    const to = this.node(peer)
    const remaining: QueueEntry[] = []
    let blocked = false
    for (const entry of q) {
      const ready = now >= entry.readyAt + link.delay_ms
      // Strict priority: once the best ready event does not fit the byte budget, nothing behind it may jump ahead.
      if (blocked || !ready) { remaining.push(entry); continue }
      if (entry.size > budget) { blocked = true; remaining.push(entry); continue }
      if (link.mode === 'slow') { budget -= entry.size; n.budget[peer] -= entry.size }
      n.bytesSent[peer] = (n.bytesSent[peer] ?? 0) + entry.size
      to.bytesReceived[n.id] = (to.bytesReceived[n.id] ?? 0) + entry.size
      this.ingest(peer, entry.event, 'push')
      this.acks.push({ at: now + latencyOf(n.id, peer), from: n.id, to: peer, origin: entry.event.origin, seq: entry.event.seq })
    }
    n.queue[peer] = remaining
  }

  /** a delivery becomes known to the sender one latency later (the push response carries the receiver's heads) */
  private processAcks(now: number) {
    const due = this.acks.filter((a) => a.at <= now)
    if (!due.length) return
    this.acks = this.acks.filter((a) => a.at > now)
    for (const a of due) {
      const from = this.node(a.from)
      if (!from.up || !this.node(a.to).up) continue
      from.peerView[a.to] = { ...from.peerView[a.to], ...this.node(a.to).heads(this.joined as string[]) }
    }
  }

  private checkRecovered(n: SimNode, now: number) {
    if (!n.recovering) return
    const peers = this.peersOf(n.id).filter((p) => this.reachable(n.id, p))
    const origins = this.joined as string[]
    const same = peers.every((p) => origins.every((o) => n.head(o) === this.node(p).head(o)) && !(n.queue[p]?.length))
    const unreachableLeft = this.peersOf(n.id).some((p) => !this.reachable(n.id, p))
    if (peers.length && same && !unreachableLeft && now - n.recoveryStart >= 600) {
      n.recovering = false
      n.lastCatchUpMs = now - n.recoveryStart
      const reviews = n.reportIds().filter((r) => n.view(r).conflicts.some((c) => c.needs_review)).length
      n.lastHeal = {
        isolatedMs: n.isolatedAt ? now - n.isolatedAt : now - n.recoveryStart, eventsReceived: n.events.size - n.eventsAtRecoveryStart,
        conflicts: n.reportIds().reduce((sum, r) => sum + n.view(r).conflicts.length, 0), reviews, at: now,
      }
      n.isolatedAt = null
    }
  }

  // ---- derived node facts ------------------------------------------------------------------------------
  level(id: RegionId): Level {
    const n = this.node(id)
    const now = Date.now()
    const peers = this.peersOf(id)
    if (peers.some((p) => { const h = n.health[p]; return !this.reachable(id, p) && h?.unreachableSince != null && now - h.unreachableSince >= this.isolateAfterMs })) return 'ISOLATED'
    if (n.recovering) return 'RECOVERING'
    if (peers.some((p) => this.reachable(id, p) && this.effectiveLink(id, p).mode === 'slow')) return 'CONSTRAINED'
    const oldest = n.unreplicated(peers.filter((p) => this.reachable(id, p)))[0]
    if (peers.length && oldest && now - new Date(oldest.wall).getTime() > 2500 && peers.some((p) => this.reachable(id, p))) return 'SYNCING'
    return 'HEALTHY'
  }

  /** Deliver everything now and mark it acknowledged (used to seed the demo so no screen starts mid-sync). */
  settleNow() {
    const far = Date.now() + 1e8
    for (let round = 0; round < 6; round++) {
      for (const id of this.joined) {
        const n = this.node(id)
        if (!n.up) continue
        for (const p of this.peersOf(id)) {
          if (!this.reachable(id, p)) continue
          this.summaryExchange(n, this.node(p))
          this.drain(n, p, far)
        }
      }
    }
    this.acks = []
    for (const id of this.joined) {
      const n = this.node(id)
      for (const p of this.peersOf(id)) n.peerView[p] = this.node(p).heads(this.joined as string[])
      n.health = {}
    }
  }

  /** events still waiting in this node's push queues, per priority lane */
  pendingByLane(id: RegionId): { P0: number; P1: number; P2: number } {
    const out = { P0: 0, P1: 0, P2: 0 }
    const seen = new Set<string>()
    for (const q of Object.values(this.node(id).queue)) for (const e of q) if (!seen.has(e.event.event_id)) { seen.add(e.event.event_id); out[`P${e.event.priority}` as 'P0'] += 1 }
    return out
  }

  /** Clamp helper kept here so the API layer stays free of tuning constants. */
  clampRate(rate: number) { return clamp(rate, 200, 100000) }
}
