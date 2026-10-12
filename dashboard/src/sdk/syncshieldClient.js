// SyncShield browser SDK (owner: Miti). Plan v1.1 section 6.3.
//
// Framework-free ES module, no imports, so it moves unchanged into web/src/sdk.
//
// What it does for the shopper:
//   * probes every node, sends each call to the best node that is up;
//   * on a timeout / network error / 5xx retries on the next node with the SAME op_id;
//   * keeps an outbox of the signed events the nodes returned, and before the first request
//     on a different node posts that outbox to POST /v1/carry, so an event no peer had yet
//     is not lost with its node;
//   * never shows an error while any node answers.
//
// It degrades honestly when the backend is older than contract v1.1:
//   /healthz missing  -> falls back to /v1/health
//   /v1/carry missing -> carry is skipped and reported as unsupported (counters say so)
//   /v1/events/has missing -> "present on survivors" is estimated from /v1/status heads
//   no signed `event` in 2xx body -> only the event id is tracked (cannot be carried)

export const DEFAULTS = {
  PROBE_MS: 500,
  PROBE_TIMEOUT_MS: 400,
  REQUEST_TIMEOUT_MS: 600,
  MAX_OUTBOX: 500,
  OUTBOX_TTL_MIN: 10,
  FAILBACK_GOOD_PROBES: 3,
  STATUS_POLL_MS: 1000,
  MAX_CARRY: 200,
}

export function uuid() {
  const c = globalThis.crypto
  if (c && c.randomUUID) return c.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0
    return (ch === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function withTimeout(promise, ms) {
  let t
  const timeout = new Promise((_, rej) => {
    t = setTimeout(() => rej(Object.assign(new Error('timeout'), { name: 'TimeoutError' })), ms)
  })
  return Promise.race([promise, timeout]).finally(() => clearTimeout(t))
}

export class SyncShieldClient {
  /**
   * @param {{id:string,url:string}[]} regions ordered by preference (first = preferred node)
   * @param {{apiKey?:string, fetch?:Function, now?:Function, config?:object}} opts
   */
  constructor(regions, opts = {}) {
    this.cfg = { ...DEFAULTS, ...(opts.config || {}) }
    this.apiKey = opts.apiKey || 'dev-officer'
    this.fetch = opts.fetch || ((...a) => globalThis.fetch(...a))
    this.now = opts.now || (() => Date.now())
    this.regions = regions.map((r) => ({ id: r.id, url: r.url.replace(/\/$/, '') }))
    this.preferred = this.regions[0].id
    this.nodes = Object.fromEntries(
      this.regions.map((r) => [r.id, {
        id: r.id, url: r.url.replace(/\/$/, ''), up: true, routable: true, goodProbes: 0,
        latencyMs: null, level: null, carrySupported: null, hasSupported: null, lastError: null,
      }]),
    )
    this.outbox = []            // [{event, addedAt}] signed envelopes awaiting confirmation
    this.acked = new Map()      // event_id -> {origin, seq, at, node}
    this.pending = []           // operations kept on the device because every node was down
    this.timeline = []          // newest last, capped
    this.lastRegion = null
    this.lastStatus = {}        // node id -> last /v1/status body
    this.counters = {
      requests: 0, ok: 0, failovers: 0, retries: 0, errorsShown: 0, answers4xx: 0, queued: 0,
      acknowledged: 0, carriedNew: 0, carriedDup: 0, carriedRejected: 0, carryCalls: 0,
      outboxEvicted: 0, outboxConfirmed: 0,
    }
    this.listeners = new Set()
    this._timers = []
  }

  // ---- observation ------------------------------------------------------
  subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn) }
  _emit() { for (const fn of this.listeners) { try { fn(this.snapshot()) } catch { /* UI errors never break the SDK */ } } }

  snapshot() {
    return {
      nodes: Object.values(this.nodes).map((n) => ({ ...n })),
      preferred: this.preferred,
      serving: this.lastRegion,
      outbox: this.outbox.map((o) => ({ ...o.event, _addedAt: o.addedAt })),
      counters: { ...this.counters },
      timeline: this.timeline.slice(),
      pending: this.pending.length,
    }
  }

  // ---- low-level http ---------------------------------------------------
  async _http(node, method, path, body, timeoutMs, extraHeaders) {
    const headers = { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey, ...(extraHeaders || {}) }
    const ctl = typeof AbortController !== 'undefined' ? new AbortController() : null
    const p = this.fetch(node.url + path, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: ctl ? ctl.signal : undefined,
    })
    let res
    try {
      res = await withTimeout(p, timeoutMs)
    } catch (e) {
      if (ctl) ctl.abort()
      throw e
    }
    let json = null
    try { json = await res.json() } catch { /* empty or non-json body */ }
    return { status: res.status, ok: res.status >= 200 && res.status < 300, body: json, headers: res.headers }
  }

  // ---- health probing and routing ---------------------------------------
  async probe(node) {
    const t0 = this.now()
    try {
      let r = await this._http(node, 'GET', '/healthz', undefined, this.cfg.PROBE_TIMEOUT_MS)
      if (r.status === 404) r = await this._http(node, 'GET', '/v1/health', undefined, this.cfg.PROBE_TIMEOUT_MS)
      if (!r.ok) throw new Error('http ' + r.status)
      node.latencyMs = this.now() - t0
      node.level = (r.body && r.body.level) || node.level
      node.lastError = null
      if (!node.up) { node.up = true; node.goodProbes = 0 }
      node.goodProbes += 1
      // a node that came back must prove itself for FAILBACK_GOOD_PROBES before it is routed to again
      if (!node.routable && node.goodProbes >= this.cfg.FAILBACK_GOOD_PROBES) node.routable = true
    } catch (e) {
      this._markDown(node, e)
    }
  }

  async probeAll() {
    await Promise.all(Object.values(this.nodes).map((n) => this.probe(n)))
    this._emit()
  }

  _markDown(node, err) {
    node.up = false
    node.routable = false
    node.goodProbes = 0
    node.level = null
    node.lastError = err ? String(err.name === 'TimeoutError' ? 'timeout' : err.message || err) : null
  }

  // Nodes to try, best first: routable (preferred first), then any other that is up, then the rest as last resort.
  order() {
    const all = this.regions.map((r) => this.nodes[r.id])
    const rank = (n) => (n.routable && n.up ? 0 : n.up ? 1 : 2)
    return all
      .map((n, i) => ({ n, i }))
      .sort((a, b) => rank(a.n) - rank(b.n)
        || (a.n.id === this.preferred ? -1 : 0) - (b.n.id === this.preferred ? -1 : 0)
        || a.i - b.i)
      .map((x) => x.n)
  }

  // ---- the failover call -------------------------------------------------
  /**
   * @returns {{ok:boolean,status:number,body:any,node:string|null,tried:number,ms:number,queued?:boolean,opId:string}}
   */
  async call(method, path, body = {}) {
    const opId = (body && body.op_id) || uuid()
    const payload = method === 'GET' ? undefined : { ...body, op_id: opId }
    const started = this.now()
    let tried = 0
    this.counters.requests += 1

    for (const node of this.order()) {
      if (node.id !== this.lastRegion && this.outbox.length) await this.carry(node)
      const t0 = this.now()
      let res
      try {
        res = await this._http(node, method, path, payload, this.cfg.REQUEST_TIMEOUT_MS)
      } catch (e) {
        this._markDown(node, e)
        tried += 1
        continue
      }
      if (res.status >= 500 && res.status !== 503) { this._markDown(node, new Error('http ' + res.status)); tried += 1; continue }
      if (res.status === 503) {
        const ra = Number((res.headers && res.headers.get && res.headers.get('Retry-After')) || 0)
        tried += 1
        if (ra > 0 && ra <= 1) await sleep(ra * 1000)
        continue
      }
      // 2xx, 4xx: an answer. Only network-level trouble triggers failover.
      const ms = this.now() - t0
      const failover = this.lastRegion !== null && this.lastRegion !== node.id
      if (failover) this.counters.failovers += 1
      if (tried) this.counters.retries += tried
      this.lastRegion = node.id
      if (res.ok) {
        this.counters.ok += 1
        if (method !== 'GET') this._recordAck(res.body, node.id)
      } else {
        this.counters.answers4xx += 1
      }
      this._timeline({ t: this.now(), node: node.id, ms, totalMs: this.now() - started, tried, failover, method, path, status: res.status, opId })
      this._emit()
      return { ok: res.ok, status: res.status, body: res.body, node: node.id, tried, ms, opId }
    }

    // every node failed: keep the shopper's action on the device and retry later
    this.counters.queued += 1
    this.counters.errorsShown += 1   // the only case the shopper can see a problem: no node answered
    if (method !== 'GET') this.pending.push({ method, path, body: payload, opId, at: this.now() })
    this._timeline({ t: this.now(), node: null, ms: this.now() - started, totalMs: this.now() - started, tried, failover: false, method, path, status: 0, opId, queued: true })
    this._emit()
    return { ok: false, status: 0, body: null, node: null, tried, ms: this.now() - started, queued: true, opId }
  }

  _timeline(entry) {
    this.timeline.push(entry)
    if (this.timeline.length > 200) this.timeline.shift()
  }

  _recordAck(body, nodeId) {
    if (!body) return
    const ev = body.event || null
    const id = (ev && ev.event_id) || body.event_id
    if (!id) return
    const [origin, seq] = [ev ? ev.origin : String(id).split(':')[0], ev ? ev.seq : Number(String(id).split(':')[1])]
    if (!this.acked.has(id)) this.counters.acknowledged += 1
    this.acked.set(id, { origin, seq, at: this.now(), node: nodeId })
    if (ev && ev.hash) {
      if (!this.outbox.some((o) => o.event.event_id === id)) this.outbox.push({ event: ev, addedAt: this.now() })
      while (this.outbox.length > this.cfg.MAX_OUTBOX) { this.outbox.shift(); this.counters.outboxEvicted += 1 }
    }
  }

  // Retry operations that were queued while every node was down.
  async flushPending() {
    const items = this.pending.splice(0)
    const results = []
    for (const it of items) results.push(await this.call(it.method, it.path, it.body))
    return results
  }

  // ---- carry ---------------------------------------------------------------
  async carry(node) {
    if (node.carrySupported === false || !this.outbox.length) return null
    const events = this.outbox.slice(0, this.cfg.MAX_CARRY).map((o) => o.event)
    this.counters.carryCalls += 1
    try {
      const r = await this._http(node, 'POST', '/v1/carry', { events }, this.cfg.REQUEST_TIMEOUT_MS)
      if (r.status === 404 || r.status === 405) { node.carrySupported = false; return null }
      if (!r.ok) return null
      node.carrySupported = true
      const results = (r.body && r.body.results) || []
      const done = new Set()
      for (const x of results) {
        if (x.result === 'new') { this.counters.carriedNew += 1; done.add(x.event_id) }
        else if (x.result === 'dup') { this.counters.carriedDup += 1; done.add(x.event_id) }
        else { this.counters.carriedRejected += 1; done.add(x.event_id) } // cannot ever be accepted: stop carrying it
      }
      this.outbox = this.outbox.filter((o) => !done.has(o.event.event_id))
      this._emit()
      return results
    } catch (e) {
      return null
    }
  }

  // ---- status, pruning, proof counters --------------------------------------
  async pollStatus() {
    const serving = this.lastRegion ? this.nodes[this.lastRegion] : this.order()[0]
    if (!serving) return null
    try {
      const r = await this._http(serving, 'GET', '/v1/status', undefined, this.cfg.REQUEST_TIMEOUT_MS * 2)
      if (!r.ok) return null
      this.lastStatus[serving.id] = r.body
      serving.level = r.body.level || serving.level
      this._prune(r.body)
      this._emit()
      return r.body
    } catch (e) {
      return null
    }
  }

  // Drop an event from the outbox once any peer has confirmed it (acked >= seq), and apply the TTL.
  _prune(status) {
    const peers = (status && status.peers) || {}
    const before = this.outbox.length
    const cutoff = this.now() - this.cfg.OUTBOX_TTL_MIN * 60_000
    this.outbox = this.outbox.filter((o) => {
      const e = o.event
      if (o.addedAt < cutoff) return false
      for (const p of Object.values(peers)) {
        const heads = p.acked || p.their_heads || {}   // v1.1 shape, then the basic-build shape
        if ((heads[e.origin] || 0) >= e.seq) return false
      }
      return true
    })
    this.counters.outboxConfirmed += before - this.outbox.length
  }

  /**
   * The judge counter "acknowledged = present on survivors".
   * Asks every node that is up whether it holds each acknowledged event.
   */
  async presentOnSurvivors() {
    const ids = [...this.acked.keys()]
    const survivors = Object.values(this.nodes).filter((n) => n.up)
    if (!ids.length) {   // nothing acknowledged yet: nothing to verify, and nothing is "estimated"
      return { acknowledged: 0, present: 0, missing: [], estimated: false, survivors: survivors.map((n) => n.id) }
    }
    const present = new Set()
    let estimated = false
    for (const node of survivors) {
      let usedHas = false
      if (node.hasSupported !== false && ids.length) {
        try {
          const r = await this._http(node, 'POST', '/v1/events/has', { ids: ids.slice(0, 500) }, this.cfg.REQUEST_TIMEOUT_MS * 2)
          if (r.status === 404 || r.status === 405) node.hasSupported = false
          else if (r.ok) {
            node.hasSupported = true
            usedHas = true
            const missing = new Set((r.body && r.body.missing) || [])
            for (const id of ids.slice(0, 500)) if (!missing.has(id)) present.add(id)
          }
        } catch { /* try the fallback */ }
      }
      if (!usedHas) {
        // fallback: the node's contiguous head for the event's origin
        estimated = true
        try {
          const r = await this._http(node, 'GET', '/v1/status', undefined, this.cfg.REQUEST_TIMEOUT_MS * 2)
          if (r.ok) {
            const heads = (r.body && r.body.heads) || {}
            for (const [id, a] of this.acked) if ((heads[a.origin] || 0) >= a.seq) present.add(id)
          }
        } catch { /* node unreachable */ }
      }
    }
    const missing = ids.filter((id) => !present.has(id))
    return { acknowledged: ids.length, present: present.size, missing, estimated, survivors: survivors.map((n) => n.id) }
  }

  // ---- timers ----------------------------------------------------------------
  start() {
    this.stop()
    this.probeAll()
    this._timers.push(setInterval(() => this.probeAll(), this.cfg.PROBE_MS))
    this._timers.push(setInterval(() => this.pollStatus(), this.cfg.STATUS_POLL_MS))
  }

  stop() {
    for (const t of this._timers) clearInterval(t)
    this._timers = []
  }
}
