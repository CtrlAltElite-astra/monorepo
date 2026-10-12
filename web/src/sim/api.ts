// The simulator's REST surface (MOCK MODE): the same paths, status codes and bodies as the real regions
// (contracts/contracts.md v1.1), so the SDK and UI cannot tell the difference.
import type { CarryResult, EventEnvelope, HttpResult, ReportView, RegionId, Role, Status, Transport, RequestOptions, Chaos } from '../api/types'
import { NetworkError } from '../api/types'
import { canonical, sleep } from '../lib/util'
import { eventHash, verify } from './crypto'
import { POLICY_VERSION, order } from './derive'
import { ALL_REGIONS, REGION_NAMES, SimNode, SimWorld, type LinkMode } from './world'

interface Res { status: number; headers?: Record<string, string>; body: any }
const ok = (body: any, status = 200, headers: Record<string, string> = {}): Res => ({ status, body, headers })
const fail = (status: number, body: any): Res => ({ status, body })

const CATEGORIES = ['flood', 'collapse', 'medical', 'fire', 'road_block', 'shelter', 'other', 'cart']
const STATUSES = ['open', 'assigned', 'resolved', 'closed', 'cancelled']
const ACTOR: Record<Role, string> = { officer: 'officer', supervisor: 'supervisor', admin: 'admin' }

/** Mirrors contracts.schemas.validate_field (write time only). Returns an error message or null. */
export function validateField(field: string, value: any): string | null {
  const int = (v: any) => typeof v === 'number' && Number.isInteger(v)
  switch (field) {
    case 'title': return typeof value === 'string' && value.length >= 1 && value.length <= 120 ? null : 'title must be 1-120 chars'
    case 'description': return value === null || (typeof value === 'string' && value.length <= 2000) ? null : 'description must be up to 2000 chars'
    case 'category': return CATEGORIES.includes(value) ? null : 'unknown category'
    case 'severity': return int(value) && value >= 1 && value <= 5 ? null : 'severity must be an int 1-5'
    case 'status': return STATUSES.includes(value) ? null : 'unknown status'
    case 'location': return value === null || (value && int(value.lat_e6) && int(value.lon_e6)) ? null : 'bad location'
    case 'assigned_team': return value === null || (typeof value === 'string' && value.length >= 1 && value.length <= 80) ? null : 'bad team'
    case 'people_estimate': return value === null || (int(value) && value >= 0) ? null : 'bad people_estimate'
    default: return `unknown field '${field}'`
  }
}

/** The shop priority lanes (plan 4.2): P0 critical, P1 normal, P2 bulk. */
export function classify(type: string, field: string | null, value: any): 0 | 1 | 2 {
  if (type === 'RESOLVE') return 0
  if (type === 'NOTE') return 2
  if (type === 'CREATE') return value?.severity >= 4 ? 0 : 1
  if (field === 'severity' && value >= 4) return 0
  if (field === 'assigned_team' && value) return 0
  if (field === 'status' && ['resolved', 'closed', 'cancelled'].includes(value)) return 0
  return 1
}

function consistency(world: SimWorld, node: SimNode, reportId: string) {
  const peers = world.peersOf(node.id).sort()
  const need: Record<string, number> = {}
  for (const e of node.eventsOf(reportId)) need[e.origin] = Math.max(need[e.origin] ?? 0, e.seq)
  const confirmed: string[] = [], awaiting: string[] = []
  for (const p of peers) ((Object.entries(need).every(([o, s]) => (node.peerView[p]?.[o] ?? 0) >= s)) ? confirmed : awaiting).push(p)
  const replication = !confirmed.length ? 'LOCAL_ONLY' : !awaiting.length ? 'REPLICATED' : 'PARTIAL'
  return { replication, confirmed_by: confirmed, awaiting, stale_possible: peers.some((p) => !world.reachable(node.id, p as RegionId)) } as ReportView['consistency']
}

function reportView(world: SimWorld, node: SimNode, id: string): ReportView {
  const v = node.view(id)
  return { report_id: id, state: v.state as ReportView['state'], live: v.live, conflicts: v.conflicts, incomplete: v.incomplete, consistency: consistency(world, node, id) }
}

function observedFor(node: SimNode, reportId: string, type: string, field: string | null, value: any): string[] | null {
  const v = node.view(reportId)
  if ((type === 'SET' || type === 'RESOLVE') && field === 'status' && ['closed', 'cancelled'].includes(value)) {
    const notes = node.eventsOf(reportId).filter((e) => e.type === 'NOTE').map((e) => e.event_id)
    return [...new Set([...notes, ...(v.live.severity ?? [])])].sort()
  }
  if (type === 'NOTE' || ((type === 'SET' || type === 'RESOLVE') && field === 'severity')) return [...(v.live.status ?? [])].sort()
  return null
}

interface WriteArgs { type: 'CREATE' | 'SET' | 'NOTE' | 'RESOLVE'; reportId: string | null; field: string | null; value: any; expected?: string[] | null; opId?: string | null; role: Role; reason?: string | null; candidates?: string[] }

function write(world: SimWorld, node: SimNode, a: WriteArgs): Res {
  const sameRequest = (p: EventEnvelope) => p.type === a.type && p.field === a.field && canonical(p.value) === canonical(a.value) && (a.reportId === null || p.report_id === a.reportId)
  if (a.opId) {
    const prior = node.events.get(node.byOp.get(a.opId) ?? '')
    if (prior) {
      if (!sameRequest(prior)) return fail(409, { error: 'op_id_reuse' })
      return replay(world, node, prior)
    }
  }
  let reportId = a.reportId
  let live: string[] = []
  if (a.type === 'CREATE') {
    if (reportId && node.eventsOf(reportId).length) return fail(409, { error: 'report_exists' })
  } else {
    if (!node.eventsOf(reportId!).length) return fail(404, { detail: 'unknown report' })
    live = a.field ? node.view(reportId!).live[a.field] ?? [] : []
  }
  if (a.type === 'RESOLVE') {
    if ([...live].sort().join() !== [...(a.candidates ?? [])].sort().join()) return fail(409, { error: 'stale_edit', live })
    if (!node.view(reportId!).conflicts.some((c) => c.field === a.field)) return fail(409, { error: 'no_conflict' })
  } else if (a.expected && [...a.expected].sort().join() !== [...live].sort().join()) {
    return fail(409, { error: 'stale_edit', live })
  }
  if (a.type === 'CREATE' && !reportId) reportId = `R-${node.id}-${node.seq + 1}`
  const ev = world.append(node.id, {
    report_id: reportId!, type: a.type, field: a.field, value: a.value, supersedes: live, op_id: a.opId ?? null,
    observed: observedFor(node, reportId!, a.type, a.field, a.value), actor: `${ACTOR[a.role]}-${node.id}-1`, role: a.role,
    auth_mode: 'online', reason: a.reason ?? null,
  }, classify(a.type, a.field, a.value))
  const report = reportView(world, node, reportId!)
  return ok({ report, event: ev, event_id: ev.event_id, consistency: report.consistency }, a.type === 'SET' ? 200 : 201)
}

function replay(world: SimWorld, node: SimNode, ev: EventEnvelope): Res {
  const report = reportView(world, node, ev.report_id)
  return ok({ report, event: ev, event_id: ev.event_id, consistency: report.consistency }, 200, { 'idempotent-replay': 'true' })
}

function status(world: SimWorld, node: SimNode): Status {
  const joined = world.joined as string[]
  const peers: Status['peers'] = {}
  const now = Date.now()
  for (const p of world.peersOf(node.id)) {
    const h = node.health[p]
    const reachable = world.reachable(node.id, p) && !(h?.unreachableSince != null && now - h.unreachableSince >= world.isolateAfterMs)
    const last = h?.lastOkAt ?? null
    peers[p] = {
      link: node.link(p).mode, reachable, fails: h?.fails ?? 0, last_ok_s: last ? Math.round((now - last) / 100) / 10 : null,
      last_ok_ms: last ? now - last : null, their_heads: { ...(node.peerView[p] ?? {}) }, acked: { ...(node.peerView[p] ?? {}) },
      last_error: reachable ? null : node.link(p).mode === 'down' ? 'link_down' : 'unreachable',
    }
  }
  const views = node.reportIds().sort().map((id) => [id, node.view(id)] as const)
  const lines = [...node.events.values()].map((e) => `${e.event_id}:${e.hash}`).sort().join('\n')
  const unrep = node.unreplicated(world.peersOf(node.id))
  const first = [...unrep].sort((x, y) => x.seq - y.seq)[0]
  return {
    region: node.id, level: world.level(node.id), policy_version: POLICY_VERSION, schema_version: 1,
    heads: node.heads(joined), event_count: node.events.size, peers,
    event_digest: eventHashOf(lines), state_hash: eventHashOf(canonical(views.map(([id, v]) => [id, { s: v.state, l: v.live, c: v.conflicts.length }]))),
    quarantined: node.quarantine.length, incomplete_reports: views.filter(([, v]) => v.incomplete).length,
    risk: { unreplicated: unrep.length, critical_unreplicated: unrep.filter((e) => e.priority === 0).length, oldest_age_ms: first ? Math.max(0, now - new Date(first.wall).getTime()) : 0 },
    queues: world.pendingByLane(node.id), carry: { ...node.carry },
  }
}

const eventHashOf = (text: string) => eventHash({ value: text } as any)

function history(world: SimWorld, node: SimNode, id: string): Res {
  const evs = node.eventsOf(id).sort(order)
  if (!evs.length) return fail(404, { detail: 'unknown report' })
  return ok(evs.map((e) => ({
    ...e,
    sig_status: !e.sig ? 'unsigned' : eventHash(e) !== e.hash ? 'bad' : verify(world.joined, e.origin, e.hash, e.sig) ? 'verified' : 'bad',
    carried: node.carried.has(e.event_id),
  })))
}

function review(node: SimNode): Res {
  const items = node.reportIds().flatMap((id) => {
    const v = node.view(id)
    if (v.incomplete) return []
    const created = node.eventsOf(id).find((e) => e.type === 'CREATE')!
    return v.conflicts.filter((c) => c.needs_review).map((receipt) => ({ created, item: { report_id: id, title: v.state.title, field: receipt.field, receipt } }))
  })
  return ok(items.sort((a, b) => order(b.created, a.created)).map((x) => x.item))
}

function carry(world: SimWorld, node: SimNode, events: EventEnvelope[]): Res {
  if (!Array.isArray(events) || events.length > 200) return fail(422, { detail: 'carry takes at most 200 events' })
  const results: CarryResult[] = events.map((e) => {
    node.carry.received++
    const r = world.ingest(node.id, e, 'carry')
    if (r === 'new') { node.carry.new++; return { event_id: e?.event_id, result: 'new' } }
    if (r === 'dup') { node.carry.dup++; return { event_id: e?.event_id, result: 'dup' } }
    node.carry.rejected++
    return { event_id: e?.event_id, result: 'rejected', reason: r === 'bad_shape' ? 'shape' : r === 'unknown_origin' ? 'unknown_origin' : r === 'bad_sig' ? 'bad_sig' : 'bad_hash' }
  })
  return ok({ results })
}

function metrics(world: SimWorld, node: SimNode): Res {
  const sorted = [...node.commitMs].sort((a, b) => a - b)
  const pct = (p: number) => sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))] : null
  const own = [...node.events.values()].filter((e) => e.origin === node.id).length
  const peers: Record<string, any> = {}
  for (const p of world.peersOf(node.id)) peers[p] = { bytes_sent: node.bytesSent[p] ?? 0, bytes_received: node.bytesReceived[p] ?? 0, events_sent: 0, events_received: 0 }
  const pending = Object.values(node.queue).flat()
  return ok({ writes: own, commit_ms: { p50: pct(0.5), p95: pct(0.95) }, duplicates: node.duplicates, lag_s: pending.length ? Math.max(...pending.map((q) => (Date.now() - new Date(q.event.wall).getTime()) / 1000)) : 0, catch_up_s: node.lastCatchUpMs === null ? null : node.lastCatchUpMs / 1000, peers })
}

/** Route one request to one node. `role` is what the API key would be (null: none sent). */
export function handle(world: SimWorld, region: RegionId, method: string, rawPath: string, body: any, role: Role | null): Res {
  const node = world.node(region)
  const url = new URL(rawPath, 'http://sim')
  const path = url.pathname
  const m = (re: RegExp) => path.match(re)
  const writer = role === 'officer' || role === 'supervisor'

  if (method === 'GET' && path === '/healthz') return ok({ region, ok: true, level: world.level(region), ts: Date.now() })
  if (method === 'GET' && path === '/v1/health') return ok({ region, ok: true })
  if (method === 'GET' && path === '/v1/status') return ok(status(world, node))
  if (method === 'GET' && path === '/v1/review') return review(node)
  if (method === 'GET' && path === '/v1/metrics') return metrics(world, node)
  if (method === 'GET' && path === '/v1/reports') {
    const limit = clampInt(url.searchParams.get('limit'), 100, 1, 500), cursor = clampInt(url.searchParams.get('cursor'), 0, 0, 1e9)
    return ok(node.listIds().slice(cursor, cursor + limit).map((id) => reportView(world, node, id)))
  }
  let hit = m(/^\/v1\/reports\/([^/]+)$/)
  if (method === 'GET' && hit) return node.eventsOf(hit[1]).length ? ok(reportView(world, node, hit[1])) : fail(404, { detail: 'unknown report' })
  hit = m(/^\/v1\/reports\/([^/]+)\/history$/)
  if (method === 'GET' && hit) return history(world, node, hit[1])

  if (method === 'POST' && path === '/v1/reports') {
    if (role === null) return fail(401, { detail: 'missing or unknown X-API-Key' })
    if (!writer) return fail(403, { detail: `role '${role}' may not create` })
    const b = body ?? {}
    const value = { title: b.title, category: b.category, severity: b.severity, status: 'open', assigned_team: null, location: b.location ?? null, description: b.description ?? '', people_estimate: b.people_estimate ?? null }
    for (const f of ['title', 'category', 'severity', 'location', 'description', 'people_estimate'] as const) { const bad = validateField(f, (value as any)[f]); if (bad) return fail(422, { detail: bad }) }
    if (b.report_id !== undefined && b.report_id !== null && !/^R-[0-9a-f]{8}$/.test(b.report_id)) return fail(422, { detail: 'report_id must match R-<8 hex>' })
    return write(world, node, { type: 'CREATE', reportId: b.report_id ?? null, field: null, value, opId: b.op_id, role: role! })
  }
  hit = m(/^\/v1\/reports\/([^/]+)$/)
  if (method === 'PATCH' && hit) {
    if (role === null) return fail(401, { detail: 'missing or unknown X-API-Key' })
    if (!writer) return fail(403, { detail: `role '${role}' may not set` })
    const bad = validateField(body?.field, body?.value)
    if (bad) return fail(422, { detail: bad })
    return write(world, node, { type: 'SET', reportId: hit[1], field: body.field, value: body.value, expected: body.expected, opId: body.op_id, role: role! })
  }
  hit = m(/^\/v1\/reports\/([^/]+)\/notes$/)
  if (method === 'POST' && hit) {
    if (role === null) return fail(401, { detail: 'missing or unknown X-API-Key' })
    if (!writer) return fail(403, { detail: `role '${role}' may not note` })
    if (typeof body?.text !== 'string' || !body.text.length || body.text.length > 2000) return fail(422, { detail: 'note must be 1-2000 chars' })
    return write(world, node, { type: 'NOTE', reportId: hit[1], field: null, value: { text: body.text }, opId: body.op_id, role: role! })
  }
  hit = m(/^\/v1\/reports\/([^/]+)\/resolve$/)
  if (method === 'POST' && hit) {
    if (role === null) return fail(401, { detail: 'missing or unknown X-API-Key' })
    if (role !== 'supervisor') return fail(403, { detail: `role '${role}' may not resolve` })
    const bad = validateField(body?.field, body?.value)
    if (bad) return fail(422, { detail: bad })
    if (!body.reason || String(body.reason).length > 500 || !Array.isArray(body.candidates) || !body.candidates.length) return fail(422, { detail: 'reason and candidates are required' })
    return write(world, node, { type: 'RESOLVE', reportId: hit[1], field: body.field, value: body.value, candidates: body.candidates, reason: body.reason, opId: body.op_id, role: role! })
  }
  if (method === 'POST' && path === '/v1/carry') return carry(world, node, body?.events)
  if (method === 'POST' && path === '/v1/events/has') {
    const ids: string[] = body?.ids
    if (!Array.isArray(ids) || ids.length > 500) return fail(422, { detail: 'ids: at most 500' })
    return ok({ missing: ids.filter((id) => !node.events.has(id)) })
  }
  if (path.startsWith('/admin/')) {
    if (role !== 'admin') return fail(role === null ? 401 : 403, { detail: 'admin only' })
    if (method === 'GET' && path === '/admin/links') return ok(Object.fromEntries(world.peersOf(region).map((p) => [p, node.link(p)])))
    hit = m(/^\/admin\/links\/([a-z]+)$/)
    if (method === 'POST' && hit) {
      if (!world.peersOf(region).includes(hit[1] as RegionId)) return fail(404, { detail: 'unknown peer' })
      const mode = body?.mode as LinkMode
      if (!['up', 'down', 'slow'].includes(mode)) return fail(422, { detail: 'mode must be up, down or slow' })
      const rate = mode === 'slow' ? world.clampRate(Number(body.rate_bps) || 2000) : 0
      node.links[hit[1]] = { mode, delay_ms: mode === 'slow' ? Number(body.delay_ms) || 150 : 0, rate_bps: rate }
      return ok({ peer: hit[1], ...node.link(hit[1] as RegionId) })
    }
    if (method === 'POST' && path === '/admin/burst') {
      const n = clampInt(String(body?.n ?? 100), 100, 1, 1000)
      const target = body?.report_id ?? node.listIds()[0]
      if (!target) return fail(409, { error: 'no_report' })
      for (let i = 0; i < n; i++) {
        const text = JSON.stringify({ kind: 'ADD', sku: 'CHAI-01', name: 'Masala chai box', qty: 1, price: 349, user: 'burst' })
        write(world, node, { type: 'NOTE', reportId: target, field: null, value: { text }, opId: `burst-${Date.now()}-${node.seq}-${i}`, role: 'supervisor' })
      }
      return ok({ accepted: n })
    }
  }
  return fail(404, { detail: 'not found' })
}

const clampInt = (raw: string | null, dflt: number, lo: number, hi: number) => {
  const n = raw === null ? dflt : parseInt(raw, 10)
  return Number.isNaN(n) ? dflt : Math.max(lo, Math.min(hi, n))
}

// ---- the transport and chaos agent the SDK/UI use in mock mode ----------------------------------------
export class SimTransport implements Transport {
  mode = 'mock' as const
  regions = ALL_REGIONS.map((id) => ({ id, name: REGION_NAMES[id], url: `sim://${id}` }))
  private n = 0

  constructor(public world: SimWorld) {}

  async request<T = any>(region: RegionId, method: string, path: string, opts: RequestOptions = {}): Promise<HttpResult<T>> {
    const node = this.world.node(region)
    const timeout = opts.timeoutMs ?? 1500
    const started = Date.now()
    if (!this.world.isJoined(region) || !node.up) {
      // a killed process: the connection attempt fails fast, a booting one hangs a moment first
      await sleep(Math.min(timeout, node.booting ? timeout : 260))
      throw new NetworkError(`${region} did not answer`, node.booting ? 'timeout' : 'refused')
    }
    this.n++
    await sleep(18 + ((this.n * 7) % 24))
    if (!node.up) throw new NetworkError(`${region} died during the request`, 'refused')
    const res = handle(this.world, region, method, path, opts.body === undefined ? undefined : JSON.parse(JSON.stringify(opts.body)), opts.as ?? (method === 'GET' ? null : 'officer'))
    return { status: res.status, headers: res.headers ?? {}, body: JSON.parse(JSON.stringify(res.body)) as T, ms: Date.now() - started }
  }
}

export class SimChaos implements Chaos {
  available = true
  constructor(private world: SimWorld) {}
  async crash(region: RegionId) { this.world.crash(region) }
  async start(region: RegionId) { if (!this.world.isJoined(region)) this.world.join(region); else this.world.restart(region) }
  async state() {
    return { regions: Object.fromEntries(this.world.joined.map((r) => [r, { running: this.world.node(r).up, pid: null }])) }
  }
}
