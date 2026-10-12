// The browser SDK (plan section 6.3). Real code in BOTH modes: only the Transport underneath differs.
//
// Rule: never show an error to the shopper while any node answers. A write that cannot reach its node is retried
// on the next node with the SAME op_id (so it can never double), and before the first request on a new node the
// SDK carries the signed events it still holds, so an acknowledged write survives the death of the node that
// acknowledged it.
import type { CarryResult, EventEnvelope, HttpResult, Level, RegionId, Role, Status, Transport } from '../api/types'
import { NetworkError } from '../api/types'
import { median, sleep, uuid } from '../lib/util'

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

export interface RegionHealth {
  id: RegionId
  up: boolean
  ms: number | null
  goodProbes: number
  level: Level | null
  probedAt: number | null
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
}

export interface Counters {
  requests: number          // calls made by the storefront
  errors: number            // errors SHOWN to the shopper (the number that must stay 0)
  failovers: number         // calls that needed a second node, or went to the next one because a probe found the last one down
  acknowledged: number      // distinct events a node acknowledged (201 / replayed 200)
  carried: number           // events handed to a node by POST /v1/carry
  carryRejected: number
  queued: number            // actions kept on the device while every node was down
  expired: number           // outbox events dropped by the TTL (should stay 0)
  overflowed: number
}

export interface CarryNote { at: number; to: RegionId; sent: number; results: CarryResult[]; unsupported?: boolean }

export interface SdkState {
  regions: RegionHealth[]
  serving: RegionId | null
  routePreferred: boolean
  outbox: OutboxEntry[]
  timeline: TimelineEntry[]
  counters: Counters
  deviceQueue: number
  carrySupported: boolean
  lastCarry: CarryNote | null
}

interface QueuedOp { method: string; path: string; body: any; as?: Role; label: string }

const blank = (): Counters => ({ requests: 0, errors: 0, failovers: 0, acknowledged: 0, carried: 0, carryRejected: 0, queued: 0, expired: 0, overflowed: 0 })

export class SyncShieldClient {
  readonly opts: SdkOptions
  private health = new Map<RegionId, RegionHealth & { samples: number[] }>()
  private lastRegion: RegionId | null = null
  private routePreferred = true
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
    for (const r of t.regions) this.health.set(r.id, { id: r.id, up: true, ms: null, goodProbes: 0, level: null, probedAt: null, samples: [] })
  }

  // ---- observation ------------------------------------------------------------------------------------
  subscribe(fn: () => void) { this.listeners.add(fn); return () => this.listeners.delete(fn) }
  onCarry(fn: (c: CarryNote) => void) { this.carryListeners.add(fn); return () => this.carryListeners.delete(fn) }
  private emit() { this.listeners.forEach((fn) => fn()) }

  getState(): SdkState {
    return {
      regions: [...this.health.values()].map(({ samples: _s, ...h }) => ({ ...h })),
      serving: this.lastRegion, routePreferred: this.routePreferred,
      outbox: this.outbox.map((o) => ({ ...o })), timeline: [...this.timeline], counters: { ...this.counters },
      deviceQueue: this.queue.length, carrySupported: this.carrySupported, lastCarry: this.lastCarry,
    }
  }

  /** every distinct event id this client had acknowledged (for the "present on survivors" counter) */
  acknowledgedIds(): string[] { return [...this.ackedIds] }

  shopperError() { this.counters.errors++; this.emit() }
  resetStats() { this.counters = blank(); this.ackedIds.clear(); this.timeline = []; this.emit() }

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
    await Promise.all(this.t.regions.map(async ({ id }) => {
      const h = this.health.get(id)!
      try {
        let res = await this.t.request<{ ok: boolean; level: Level }>(id, 'GET', '/healthz', { timeoutMs: this.opts.probeTimeoutMs })
        // a node that has no /healthz yet still answers /v1/health: use it rather than calling a live node down
        if (res.status === 404) res = await this.t.request<{ ok: boolean; level: Level }>(id, 'GET', '/v1/health', { timeoutMs: this.opts.probeTimeoutMs })
        if (res.status === 200 && res.body?.ok) {
          h.up = true; h.goodProbes++; h.level = res.body.level ?? null; h.probedAt = Date.now()
          h.samples = [...h.samples.slice(-4), res.ms]; h.ms = median(h.samples)
          if (id === this.opts.preferred && !this.routePreferred && h.goodProbes >= this.opts.failbackGoodProbes) this.routePreferred = true // fail back
          return
        }
      } catch (e) {
        if (!(e instanceof NetworkError)) throw e
      }
      h.up = false; h.goodProbes = 0; h.level = null; h.probedAt = Date.now()
      if (id === this.opts.preferred) this.routePreferred = false
    }))
    this.emit()
    if (this.queue.length && [...this.health.values()].some((h) => h.up)) void this.flushQueue()
  }

  /** The regions that answer, in the configured failover order (Mumbai, then Pune, then Delhi), then the silent ones as a
   *  last resort. Speed no longer decides between survivors: on one machine their latencies differ by noise, which made the
   *  failover target a coin toss. The preferred region is first once it is trusted; just back from the dead, it waits
   *  until it has answered `failbackGoodProbes` probes in a row. */
  private candidates(): RegionId[] {
    const all = [...this.health.values()]
    const rank = (h: RegionHealth) => { const i = this.opts.order.indexOf(h.id); return i < 0 ? this.opts.order.length : i }
    const inOrder = (a: RegionHealth, b: RegionHealth) => rank(a) - rank(b) || (a.ms ?? 1e9) - (b.ms ?? 1e9) // latency only breaks ties
    const ups = all.filter((h) => h.up).sort(inOrder)
    const pref = ups.find((h) => h.id === this.opts.preferred)
    const ordered = !pref ? ups : this.routePreferred ? [pref, ...ups.filter((h) => h !== pref)] : [...ups.filter((h) => h !== pref), pref]
    return [...ordered, ...all.filter((h) => !h.up).sort(inOrder)].map((h) => h.id)
  }

  private markDown(id: RegionId) {
    const h = this.health.get(id)!
    h.up = false; h.goodProbes = 0
    if (id === this.opts.preferred) this.routePreferred = false
  }

  // ---- carry ---------------------------------------------------------------------------------------------
  /** POST everything still in the outbox (oldest first, in order, so a CREATE precedes its SETs) to `region`. */
  async carry(region: RegionId): Promise<boolean> {
    if (!this.carrySupported || !this.outbox.length) return true
    const events = this.outbox.slice(0, 200).map((o) => o.event)
    try {
      const res = await this.t.request<{ results: CarryResult[] }>(region, 'POST', '/v1/carry', { body: { events }, timeoutMs: this.opts.requestTimeoutMs })
      if (res.status === 404 || res.status === 405 || res.status === 501) { // this node has no carry endpoint: say so, do not retry
        this.carrySupported = false
        this.noteCarry({ at: Date.now(), to: region, sent: 0, results: [], unsupported: true })
        return true
      }
      if (res.status !== 200) return true
      for (const r of res.body.results) {
        const entry = this.outbox.find((o) => o.event.event_id === r.event_id)
        if (entry && r.result !== 'rejected') entry.carriedTo.push(region)
        if (r.result === 'new') this.counters.carried++
        if (r.result === 'rejected') this.counters.carryRejected++
      }
      this.noteCarry({ at: Date.now(), to: region, sent: events.length, results: res.body.results })
      return true
    } catch (e) {
      if (e instanceof NetworkError) return false
      throw e
    }
  }

  private noteCarry(c: CarryNote) { this.lastCarry = c; this.carryListeners.forEach((fn) => fn(c)); this.emit() }

  // ---- the call -----------------------------------------------------------------------------------------------
  async call<T = any>(method: string, path: string, body?: any, opts: { as?: Role; label?: string } = {}): Promise<HttpResult<T>> {
    this.counters.requests++
    const write = method !== 'GET'
    const op_id: string = (write && body?.op_id) || uuid()
    const payload = write && body !== undefined ? { ...body, op_id } : body
    const started = Date.now()
    const failedOn: RegionId[] = []
    for (const region of this.candidates()) {
      if (this.lastRegion && region !== this.lastRegion && write) {
        const carried = await this.carry(region)
        if (!carried) { this.markDown(region); failedOn.push(region); continue }
      }
      try {
        const res = await this.t.request<T>(region, method, path, { body: payload, as: opts.as, timeoutMs: this.opts.requestTimeoutMs })
        if (res.status === 503) {
          const wait = Math.min(1000, (Number(res.headers['retry-after']) || 1) * 1000)
          await sleep(wait)
          failedOn.push(region)
          continue
        }
        if (res.status >= 500) { this.markDown(region); failedOn.push(region); continue }
        this.health.get(region)!.up = true
        const ev = (res.body as any)?.event as EventEnvelope | undefined
        if (res.status >= 200 && res.status < 300 && ev) {
          this.outboxAdd(ev)
          if (!this.ackedIds.has(ev.event_id)) { this.ackedIds.add(ev.event_id); this.counters.acknowledged++ }
        }
        const movedOn = !!this.lastRegion && region !== this.lastRegion && !this.health.get(this.lastRegion)?.up // the probe saw the old node die first
        if (failedOn.length || movedOn) this.counters.failovers++
        this.lastRegion = region
        this.pushTimeline({ op_id, label: opts.label ?? `${method} ${path}`, region, ms: Date.now() - started, tried: failedOn.length, failedOn, status: res.status, queued: false })
        return res
      } catch (e) {
        if (!(e instanceof NetworkError)) throw e
        this.markDown(region)
        failedOn.push(region)
      }
    }
    // every node failed: the shopper's action is kept on the device and retried when any node answers
    if (write) { this.queue.push({ method, path, body: payload, as: opts.as, label: opts.label ?? `${method} ${path}` }); this.counters.queued++ }
    this.pushTimeline({ op_id, label: opts.label ?? `${method} ${path}`, region: null, ms: Date.now() - started, tried: failedOn.length, failedOn, status: 0, queued: true })
    return { status: 202, headers: { 'x-queued-on-device': '1' }, body: { queued: true, op_id } as any, ms: Date.now() - started }
  }

  private async flushQueue() {
    if (this.flushing) return
    this.flushing = true
    try {
      while (this.queue.length) {
        const op = this.queue[0]
        const res = await this.call(op.method, op.path, op.body, { as: op.as, label: op.label + ' (retried)' })
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
    for (const r of this.t.regions) {
      if (!this.health.get(r.id)!.up) continue
      try {
        const missing = new Set<string>()
        for (let i = 0; i < ids.length; i += 500) {
          const res = await this.t.request<{ missing: string[] }>(r.id, 'POST', '/v1/events/has', { body: { ids: ids.slice(i, i + 500) }, timeoutMs: 1500 })
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
