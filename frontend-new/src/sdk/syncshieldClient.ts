// The browser SDK (plan section 6.3). Real code in BOTH modes: only the Transport underneath differs.
//
// Rule: never show an error to the shopper while any node answers. A write that cannot reach its node is retried
// on the next node with the SAME op_id (so it can never double), and before a write goes to a node that may not hold what
// it depends on, the SDK carries the signed events it still holds, so an acknowledged write survives the death of the node
// that acknowledged it.
//
// Scaling (contract v1.2): a caller may name a HOME region, its first choice while that region is up, trusted and not busy;
// the fixed order is the fallback. A 503 `overloaded` is not a failure: the region is marked busy until its Retry-After has
// passed, the write goes to the next region at once, and the busy region is avoided meanwhile. A 429 `rate_limited` is an
// answer: it is returned as it is and never retried elsewhere (that would just dodge the limit).
import type { CarryResult, EventEnvelope, HttpResult, Level, RegionId, Role, Status, Transport } from '../api/types'
import { NetworkError } from '../api/types'
import { median, uuid } from '../lib/util'

export interface SdkOptions {
  probeMs: number
  probeTimeoutMs: number
  requestTimeoutMs: number
  maxOutbox: number
  outboxTtlMin: number
  failbackGoodProbes: number
  statusPollMs: number
  preferred: RegionId
  /** failover order: the first region that answers, in this order, serves. Latency only breaks ties for regions not listed. */
  order: RegionId[]
}

export const DEFAULTS: SdkOptions = {
  probeMs: 500, probeTimeoutMs: 400, requestTimeoutMs: 600, maxOutbox: 500, outboxTtlMin: 10,
  failbackGoodProbes: 3, statusPollMs: 1000, preferred: 'mum', order: ['mum', 'pun', 'del'],
}

/** Regions that are configured but not part of a default mesh. They count only once the console says they joined (setMembers). */
export const OPTIONAL_REGIONS: RegionId[] = ['blr']

export interface RegionHealth {
  id: RegionId
  up: boolean
  ms: number | null
  goodProbes: number
  level: Level | null
  probedAt: number | null
  /** serves ahead of untrusted regions: true at the start, lost when the region stops answering, regained after `failbackGoodProbes` good probes */
  trusted: boolean
  /** the region answered 503 `overloaded`: avoided until this time */
  busyUntil: number | null
}

export interface OutboxEntry { event: EventEnvelope; addedAt: number; carriedTo: RegionId[] }

export interface TimelineEntry {
  n: number
  at: number
  op_id: string
  label: string
  region: RegionId | null   // who finally answered; null when queued on the device
  ms: number
  tried: number             // nodes that failed before one answered
  failedOn: RegionId[]
  status: number
  queued: boolean
  /** the region answered 429: the shopper's own limit, an answer rather than a failure */
  refused?: boolean
}

export interface Counters {
  requests: number          // calls made by the storefront
  errors: number            // errors SHOWN to the shopper (the number that must stay 0)
  failovers: number         // calls that needed a second node, or went to the next one because a probe found the last one down
  acknowledged: number      // distinct events a node acknowledged (201 / replayed 200)
  carried: number           // events handed to a node by POST /v1/carry and accepted as new
  recovered: number         // ...of which their origin node had stopped answering: a real recovery
  relayed: number           // ...of which their origin was fine, the target just did not hold them yet (a busy or home-region move)
  carryRejected: number
  queued: number            // actions kept on the device while every node was down
  expired: number           // outbox events dropped by the TTL (should stay 0)
  overflowed: number
  refused: number           // writes the per-shopper limit answered 429 (not errors)
}

export interface CarryNote { at: number; to: RegionId; sent: number; results: CarryResult[]; recovered?: number; relayed?: number; unsupported?: boolean }

/** Where the next write of a caller goes, and why it is not at its first choice (if it is not). */
export interface Route {
  region: RegionId | null
  /** the caller's first choice: its home region, or the preferred region */
  ideal: RegionId
  state: 'ok' | 'down' | 'recovering' | 'busy'
  busyForMs: number
}

export interface SdkState {
  regions: RegionHealth[]
  members: RegionId[]
  serving: RegionId | null
  routePreferred: boolean
  outbox: OutboxEntry[]
  timeline: TimelineEntry[]
  counters: Counters
  deviceQueue: number
  carrySupported: boolean
  lastCarry: CarryNote | null
}

export interface CallOptions {
  as?: Role
  label?: string
  /** this caller's first-choice region (a shopper's home region); the preferred region when not given */
  home?: RegionId
  /** whose allowance a write spends (a shopper's name) */
  actor?: string
}

interface QueuedOp extends CallOptions { method: string; path: string; body: any; label: string }

const blank = (): Counters => ({ requests: 0, errors: 0, failovers: 0, acknowledged: 0, carried: 0, recovered: 0, relayed: 0, carryRejected: 0, queued: 0, expired: 0, overflowed: 0, refused: 0 })

/** The cart a request is about: from the path, or from the body of a create. */
const reportIdOf = (path: string, body: any): string | null =>
  path.match(/^\/v1\/reports\/([^/?]+)/)?.[1] ?? (typeof body?.report_id === 'string' ? body.report_id : null)

type Tracked = RegionHealth & { samples: number[]; busyStreak: number }

export class SyncShieldClient {
  readonly opts: SdkOptions
  private health = new Map<RegionId, Tracked>()
  private members: RegionId[] = []
  private lastRegion: RegionId | null = null
  /** where the last request of each first-choice region was served: a move away from a node that has since died is a failover */
  private lastFor = new Map<RegionId, RegionId>()
  private outbox: OutboxEntry[] = []
  private timeline: TimelineEntry[] = []
  private counters = blank()
  private ackedIds = new Set<string>()
  private queue: QueuedOp[] = []
  private carrySupported = true
  private lastCarry: CarryNote | null = null
  private listeners = new Set<() => void>()
  private carryListeners = new Set<(c: CarryNote) => void>()
  private timers: ReturnType<typeof setInterval>[] = []
  private flushing = false
  private n = 0

  constructor(private t: Transport, options: Partial<SdkOptions> = {}) {
    this.opts = { ...DEFAULTS, ...options }
    this.members = t.regions.map((r) => r.id).filter((id) => !OPTIONAL_REGIONS.includes(id))
    for (const id of this.members) this.health.set(id, this.fresh(id, true))
  }

  private fresh(id: RegionId, trusted: boolean): Tracked {
    return { id, up: trusted, ms: null, goodProbes: 0, level: null, probedAt: null, samples: [], trusted, busyUntil: null, busyStreak: 0 }
  }

  // ---- observation ------------------------------------------------------------------------------------
  subscribe(fn: () => void) { this.listeners.add(fn); return () => this.listeners.delete(fn) }
  onCarry(fn: (c: CarryNote) => void) { this.carryListeners.add(fn); return () => this.carryListeners.delete(fn) }
  private emit() { this.listeners.forEach((fn) => fn()) }

  getState(): SdkState {
    return {
      regions: this.members.map((id) => { const { samples: _s, busyStreak: _b, ...h } = this.health.get(id)!; return { ...h } }),
      members: [...this.members],
      serving: this.lastRegion, routePreferred: this.health.get(this.opts.preferred)?.trusted ?? true,
      outbox: this.outbox.map((o) => ({ ...o })), timeline: [...this.timeline], counters: { ...this.counters },
      deviceQueue: this.queue.length, carrySupported: this.carrySupported, lastCarry: this.lastCarry,
    }
  }

  /** every distinct event id this client had acknowledged (for the "present on survivors" counter) */
  acknowledgedIds(): string[] { return [...this.ackedIds] }

  shopperError() { this.counters.errors++; this.emit() }
  resetStats() { this.counters = blank(); this.ackedIds.clear(); this.timeline = []; this.emit() }

  /** The regions that are part of the mesh right now. A region that is added (Bengaluru) starts out down and untrusted: it has
   *  to answer `failbackGoodProbes` probes in a row, an empty node catching up included, before it serves anyone. */
  setMembers(ids: RegionId[]) {
    const known = new Set(this.t.regions.map((r) => r.id))
    const next = ids.filter((id) => known.has(id))
    if (next.length === this.members.length && next.every((id, i) => id === this.members[i])) return
    this.members = next
    for (const id of next) if (!this.health.has(id)) this.health.set(id, this.fresh(id, false))
    this.emit()
  }

  // ---- lifecycle ----------------------------------------------------------------------------------------
  start() {
    if (this.timers.length) return
    void this.probeOnce()
    void this.pollStatus()
    this.timers.push(setInterval(() => void this.probeOnce(), this.opts.probeMs))
    this.timers.push(setInterval(() => void this.pollStatus(), this.opts.statusPollMs))
  }

  stop() { this.timers.forEach(clearInterval); this.timers = [] }

  // ---- health probes ---------------------------------------------------------------------------------
  async probeOnce() {
    await Promise.all(this.members.map(async (id) => {
      const h = this.health.get(id)!
      try {
        let res = await this.t.request<{ ok: boolean; level: Level }>(id, 'GET', '/healthz', { timeoutMs: this.opts.probeTimeoutMs })
        // a node that has no /healthz yet still answers /v1/health: use it rather than calling a live node down
        if (res.status === 404) res = await this.t.request<{ ok: boolean; level: Level }>(id, 'GET', '/v1/health', { timeoutMs: this.opts.probeTimeoutMs })
        if (res.status === 200 && res.body?.ok) {
          h.up = true; h.goodProbes++; h.level = res.body.level ?? null; h.probedAt = Date.now()
          h.samples = [...h.samples.slice(-4), res.ms]; h.ms = median(h.samples)
          if (!h.trusted && h.goodProbes >= this.opts.failbackGoodProbes) h.trusted = true // fail back
          return
        }
      } catch (e) {
        if (!(e instanceof NetworkError)) throw e
      }
      h.up = false; h.goodProbes = 0; h.level = null; h.probedAt = Date.now(); h.trusted = false
    }))
    this.emit()
    if (this.queue.length && this.members.some((id) => this.health.get(id)!.up)) void this.flushQueue()
  }

  private isBusy(h: RegionHealth, now = Date.now()) { return !!h.busyUntil && h.busyUntil > now }

  /** The regions that answer, best first: the caller's first choice (its home region, else the preferred one) when it is up,
   *  trusted and not busy; then the others in the configured failover order (Mumbai, Pune, Delhi, ...); then regions that
   *  answer but are not trusted yet; then busy ones (avoided until their Retry-After passes); then the silent ones as a last
   *  resort. Speed no longer decides between survivors: on one machine their latencies differ by noise, which made the failover
   *  target a coin toss. */
  private candidates(home?: RegionId): RegionId[] {
    const now = Date.now()
    const ideal = home ?? this.opts.preferred
    const all = this.members.map((id) => this.health.get(id)!)
    const rank = (h: RegionHealth) => { const i = this.opts.order.indexOf(h.id); return i < 0 ? this.opts.order.length : i }
    const inOrder = (a: RegionHealth, b: RegionHealth) => rank(a) - rank(b) || (a.ms ?? 1e9) - (b.ms ?? 1e9) // latency only breaks ties
    const ups = all.filter((h) => h.up).sort(inOrder)
    const good = ups.filter((h) => h.trusted && !this.isBusy(h, now))
    const first = good.find((h) => h.id === ideal)
    const ordered = [
      ...(first ? [first] : []),
      ...good.filter((h) => h !== first),
      ...ups.filter((h) => !h.trusted && !this.isBusy(h, now)),
      ...ups.filter((h) => this.isBusy(h, now)),
    ]
    return [...ordered, ...all.filter((h) => !h.up).sort(inOrder)].map((h) => h.id)
  }

  /** Where a caller's next write would go, and why it is not at its first choice. For the route line in the console. */
  routeFor(home?: RegionId): Route {
    const now = Date.now()
    const ideal = home ?? this.opts.preferred
    const first = this.health.get(ideal)
    const region = this.candidates(home).find((id) => this.health.get(id)!.up) ?? null
    let state: Route['state'] = 'ok'
    if (region !== ideal) {
      if (!first || !first.up) state = 'down'
      else if (this.isBusy(first, now)) state = 'busy'
      else if (!first.trusted) state = 'recovering'
    }
    return { region, ideal, state, busyForMs: first && this.isBusy(first, now) ? first.busyUntil! - now : 0 }
  }

  private markDown(id: RegionId) {
    const h = this.health.get(id)!
    h.up = false; h.goodProbes = 0; h.trusted = false
  }

  // ---- carry ---------------------------------------------------------------------------------------------
  /** POST signed events from the outbox (oldest first, in order, so a CREATE precedes its SETs) to `region`. */
  async carry(region: RegionId, entries: OutboxEntry[] = this.outbox): Promise<boolean> {
    if (!this.carrySupported || !entries.length) return true
    const sent = entries.slice(0, 200)
    const events = sent.map((o) => o.event)
    try {
      const res = await this.t.request<{ results: CarryResult[] }>(region, 'POST', '/v1/carry', { body: { events }, timeoutMs: this.opts.requestTimeoutMs })
      if (res.status === 404 || res.status === 405 || res.status === 501) { // this node has no carry endpoint: say so, do not retry
        this.carrySupported = false
        this.noteCarry({ at: Date.now(), to: region, sent: 0, results: [], unsupported: true })
        return true
      }
      if (res.status !== 200) return true
      let recovered = 0, relayed = 0
      for (const r of res.body.results) {
        const entry = sent.find((o) => o.event.event_id === r.event_id)
        if (entry && r.result !== 'rejected') entry.carriedTo.push(region)
        if (r.result === 'new') {
          this.counters.carried++
          // a real recovery only when the node that wrote the event had stopped answering; otherwise the target just did not hold it yet
          if (entry && this.health.get(entry.event.origin as RegionId)?.up === false) { this.counters.recovered++; recovered++ } else { this.counters.relayed++; relayed++ }
        }
        if (r.result === 'rejected') this.counters.carryRejected++
      }
      this.noteCarry({ at: Date.now(), to: region, sent: events.length, results: res.body.results, recovered, relayed })
      return true
    } catch (e) {
      if (e instanceof NetworkError) return false
      throw e
    }
  }

  /** Before a write goes to `region`, hand it what it may be missing: the events of the cart being written (so the write finds
   *  its cart and its history there) and every event whose origin has stopped answering (so none of them depends on a dead node).
   *  Nothing to hand over means no request at all. */
  private async carryTo(region: RegionId, reportId: string | null): Promise<boolean> {
    if (!this.carrySupported || !this.outbox.length) return true
    const need = this.outbox.filter((o) =>
      o.event.origin !== region && !o.carriedTo.includes(region) &&
      ((reportId !== null && o.event.report_id === reportId) || this.health.get(o.event.origin as RegionId)?.up === false))
    return need.length ? this.carry(region, need) : true
  }

  private noteCarry(c: CarryNote) { this.lastCarry = c; this.carryListeners.forEach((fn) => fn(c)); this.emit() }

  // ---- the call -----------------------------------------------------------------------------------------------
  async call<T = any>(method: string, path: string, body?: any, opts: CallOptions = {}): Promise<HttpResult<T>> {
    this.counters.requests++
    const write = method !== 'GET'
    const op_id: string = (write && body?.op_id) || uuid()
    const payload = write && body !== undefined ? { ...body, op_id } : body
    const started = Date.now()
    const failedOn: RegionId[] = []
    const ideal = opts.home ?? this.opts.preferred
    const reportId = reportIdOf(path, payload)
    for (const region of this.candidates(opts.home)) {
      if (write) {
        const carried = await this.carryTo(region, reportId)
        if (!carried) { this.markDown(region); failedOn.push(region); continue }
      }
      try {
        const res = await this.t.request<T>(region, method, path, { body: payload, as: opts.as, actor: opts.actor, timeoutMs: this.opts.requestTimeoutMs })
        if (res.status === 503) { // overloaded: not a failure. Avoid this region until Retry-After has passed and use the next one now.
          const h = this.health.get(region)!
          const now = Date.now()
          // refusals that follow one another (the limit is still on) back off: Retry-After, twice that, four times that, never more than 5 s
          h.busyStreak = h.busyUntil && now - h.busyUntil < 10_000 ? Math.min(h.busyStreak + 1, 4) : 1
          h.busyUntil = now + Math.min(5, Math.max(1, Number(res.headers['retry-after']) || 1) * 2 ** (h.busyStreak - 1)) * 1000
          failedOn.push(region)
          continue
        }
        if (res.status >= 500) { this.markDown(region); failedOn.push(region); continue }
        const h = this.health.get(region)!
        h.up = true; h.busyUntil = null; h.busyStreak = 0
        const ev = (res.body as any)?.event as EventEnvelope | undefined
        if (res.status >= 200 && res.status < 300 && ev) {
          this.outboxAdd(ev)
          if (!this.ackedIds.has(ev.event_id)) { this.ackedIds.add(ev.event_id); this.counters.acknowledged++ }
        }
        if (res.status === 429) this.counters.refused++ // the shopper's own limit: an answer, shown calmly, never an error
        const before = this.lastFor.get(ideal)
        const movedOn = !!before && region !== before && !this.health.get(before)?.up // the probe saw the old node die first
        if (failedOn.length || movedOn) this.counters.failovers++
        this.lastFor.set(ideal, region)
        this.lastRegion = region
        this.pushTimeline({ op_id, label: opts.label ?? `${method} ${path}`, region, ms: Date.now() - started, tried: failedOn.length, failedOn, status: res.status, queued: false, refused: res.status === 429 })
        return res
      } catch (e) {
        if (!(e instanceof NetworkError)) throw e
        this.markDown(region)
        failedOn.push(region)
      }
    }
    // every node failed: the shopper's action is kept on the device and retried when any node answers
    if (write) { this.queue.push({ method, path, body: payload, as: opts.as, home: opts.home, actor: opts.actor, label: opts.label ?? `${method} ${path}` }); this.counters.queued++ }
    this.pushTimeline({ op_id, label: opts.label ?? `${method} ${path}`, region: null, ms: Date.now() - started, tried: failedOn.length, failedOn, status: 0, queued: true })
    return { status: 202, headers: { 'x-queued-on-device': '1' }, body: { queued: true, op_id } as any, ms: Date.now() - started }
  }

  private async flushQueue() {
    if (this.flushing) return
    const now = Date.now()
    if (!this.members.some((id) => { const h = this.health.get(id)!; return h.up && !this.isBusy(h, now) })) return // every region that answers is busy: wait
    this.flushing = true
    try {
      while (this.queue.length) {
        const op = this.queue[0]
        const res = await this.call(op.method, op.path, op.body, { as: op.as, home: op.home, actor: op.actor, label: op.label + ' (retried)' })
        if (res.headers['x-queued-on-device']) { this.queue.shift(); break } // call() re-queued it: every node is still down
        this.queue.shift()
      }
    } finally { this.flushing = false; this.emit() }
  }

  // ---- the outbox ----------------------------------------------------------------------------------------------
  private outboxAdd(ev: EventEnvelope) {
    if (this.outbox.some((o) => o.event.event_id === ev.event_id)) return
    this.outbox.push({ event: ev, addedAt: Date.now(), carriedTo: [] })
    while (this.outbox.length > this.opts.maxOutbox) { this.outbox.shift(); this.counters.overflowed++ }
    this.emit()
  }

  /** Drop an event once a node other than its origin holds it, or any peer has acknowledged it. */
  async pollStatus() {
    const serving = this.lastRegion ?? this.candidates()[0]
    if (!serving) return
    try {
      const res = await this.t.request<Status>(serving, 'GET', '/v1/status', { timeoutMs: 1500 })
      if (res.status !== 200) return
      this.prune(serving, res.body)
    } catch (e) {
      if (!(e instanceof NetworkError)) throw e
    }
  }

  prune(serving: RegionId, s: Status) {
    const ttl = this.opts.outboxTtlMin * 60_000
    const now = Date.now()
    const before = this.outbox.length
    this.outbox = this.outbox.filter((o) => {
      const e = o.event
      const heldElsewhere = serving !== e.origin && (s.heads?.[e.origin] ?? 0) >= e.seq
      const peerAcked = Object.values(s.peers ?? {}).some((p) => ((p.acked ?? p.their_heads)?.[e.origin] ?? 0) >= e.seq)
      if (heldElsewhere || peerAcked) return false
      if (now - o.addedAt > ttl) { this.counters.expired++; return false }
      return true
    })
    if (this.outbox.length !== before) this.emit()
  }

  private pushTimeline(e: Omit<TimelineEntry, 'n' | 'at'>) {
    this.timeline = [...this.timeline.slice(-59), { n: ++this.n, at: Date.now(), ...e }]
    this.emit()
  }

  // ---- "acknowledged = present on survivors" -------------------------------------------------------------
  /** Ask every node that answers which acknowledged events it lacks. An id is present if ANY answering node holds it. */
  async presentOnSurvivors(ids: string[] = this.acknowledgedIds()): Promise<{ present: number; checked: number; answered: number } | null> {
    if (!ids.length) return { present: 0, checked: 0, answered: 0 }
    const missingSets: Set<string>[] = []
    for (const id of this.members) {
      if (!this.health.get(id)!.up) continue
      try {
        const missing = new Set<string>()
        for (let i = 0; i < ids.length; i += 500) {
          const res = await this.t.request<{ missing: string[] }>(id, 'POST', '/v1/events/has', { body: { ids: ids.slice(i, i + 500) }, timeoutMs: 1500 })
          if (res.status !== 200) throw new NetworkError('has unavailable', 'refused')
          res.body.missing.forEach((m) => missing.add(m))
        }
        missingSets.push(missing)
      } catch (e) {
        if (!(e instanceof NetworkError)) throw e
      }
    }
    if (!missingSets.length) return null
    const present = ids.filter((id) => missingSets.some((m) => !m.has(id))).length
    return { present, checked: ids.length, answered: missingSets.length }
  }
}
