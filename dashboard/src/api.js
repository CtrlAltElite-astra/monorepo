// All backend access lives here. Nothing on screen is hard-coded: every value comes from a region API.
import fixtureReport from '../../contracts/fixtures/report.json'
import fixtureStatus from '../../contracts/fixtures/status.json'
import * as mock from './mock/data.js'

const DEFAULT = 'mum=http://127.0.0.1:8001,pun=http://127.0.0.1:8002'

export const REGIONS = (import.meta.env.VITE_REGIONS || DEFAULT)
  .split(',')
  .map((s) => s.trim().split('='))
  .map(([id, url]) => ({ id, url: url.replace(/\/$/, '') }))

// VITE_FIXTURES=1 runs the UI with no backend (read-only), from contracts/fixtures + src/mock.
export const FIXTURES = import.meta.env.VITE_FIXTURES === '1'

const KEY = import.meta.env.VITE_API_KEY || 'dev-officer'
const SUPERVISOR_KEY = import.meta.env.VITE_SUPERVISOR_KEY || KEY

// ---- TRUE credential login (VITE_AUTH_ENABLED=1) -------------------------------------------------------
// In auth mode the UI NEVER sends an API key. Every request carries the bearer session of a persona (by name,
// by {role}, or the active one). Tokens are held in memory by auth/AuthContext.jsx and handed over here.
export const AUTH_ENABLED = import.meta.env.VITE_AUTH_ENABLED === '1'
export const ISSUER_URL = (import.meta.env.VITE_ISSUER_URL || 'http://127.0.0.1:4001').replace(/\/$/, '')
let tokenProvider = null
export const setTokenProvider = (fn) => { tokenProvider = fn }
const tokenFor = (who) => (tokenProvider ? tokenProvider(who) : null)

function headers(key, who) {
  const h = { 'Content-Type': 'application/json' }
  if (AUTH_ENABLED) {
    const t = tokenFor(who)
    if (t) h.Authorization = `Bearer ${t}`
  } else {
    h['X-API-Key'] = key
  }
  return h
}

async function call(url, opts = {}, key = KEY, who) {
  if (FIXTURES && opts.method) throw new Error('fixture mode: writes disabled')
  const ctl = new AbortController()
  const t = setTimeout(() => ctl.abort(), 1500)
  try {
    const r = await fetch(url, { ...opts, headers: headers(key, who), signal: ctl.signal })
    const body = await r.json().catch(() => null)
    if (!r.ok) throw Object.assign(new Error(`HTTP ${r.status}`), { status: r.status, body })
    return body
  } finally {
    clearTimeout(t)
  }
}

// Endpoints that arrive in PDF 2: ask once, and stop asking a region that answers 404/405.
const unsupported = new Set()
async function optional(region, path) {
  const k = region.id + path
  if (unsupported.has(k)) return null
  try {
    return await call(region.url + path)
  } catch (e) {
    if (e.status === 404 || e.status === 405) unsupported.add(k)
    return null
  }
}

// One poll of one region. An unreachable region is reported as {ok:false}, never thrown.
export async function pollRegion(region) {
  const { id, url } = region
  if (FIXTURES) {
    const status = { ...fixtureStatus, region: id, peers: {}, quarantined: id === 'pun' ? 1 : 0 }
    return { id, url, ok: true, status, reports: [fixtureReport], review: mock.review, metrics: mock.metrics }
  }
  try {
    // /v1/status is public. In auth mode /v1/reports needs a session: with none, the region is still UP
    // (it answered); it simply has nothing to show this visitor yet. That is not "unreachable".
    const reportsCall = call(`${url}/v1/reports`).catch((e) => {
      if (AUTH_ENABLED && (e.status === 401 || e.status === 503)) return { __authRequired: e.status }
      throw e
    })
    const [status, reportsRaw] = await Promise.all([call(`${url}/v1/status`), reportsCall])
    const authRequired = reportsRaw && reportsRaw.__authRequired
    const reports = authRequired ? [] : reportsRaw
    const [review, metrics] = await Promise.all([
      authRequired ? null : optional(region, '/v1/review'), optional(region, '/v1/metrics'),
    ])
    return { id, url, ok: true, status, reports, review, metrics, authRequired: authRequired || null }
  } catch (e) {
    return { id, url, ok: false, error: String(e.message || e) }
  }
}

const body = (o) => JSON.stringify(o)
export const createReport = (r, b) => call(`${r.url}/v1/reports`, { method: 'POST', body: body(b) })
export const patchField = (r, id, field, value, expected) =>
  call(`${r.url}/v1/reports/${id}`, { method: 'PATCH', body: body({ field, value, expected }) })
export const addNote = (r, id, text) =>
  call(`${r.url}/v1/reports/${id}/notes`, { method: 'POST', body: body({ text }) })

// Supervisor action (PDF 2 6.6). 409 carries {live:[ids]} when the conflict changed meanwhile.
export const resolveField = (r, id, payload) =>
  call(`${r.url}/v1/reports/${id}/resolve`, { method: 'POST', body: body(payload) }, SUPERVISOR_KEY, { role: 'supervisor' })

export async function getHistory(r, id) {
  if (FIXTURES) return mock.history
  return call(`${r.url}/v1/reports/${id}/history`)
}

// Cut or heal one pair: the switch is set on BOTH regions (each backend enforces its own half).
// extra: {delay_ms, rate_bps, drop_pct} for mode 'slow'.
export async function setPairLink(a, b, mode, extra = {}) {
  return Promise.allSettled([
    call(`${a.url}/admin/links/${b.id}`, { method: 'POST', body: body({ mode, ...extra }) }, KEY, { role: 'admin' }),
    call(`${b.url}/admin/links/${a.id}`, { method: 'POST', body: body({ mode, ...extra }) }, KEY, { role: 'admin' }),
  ])
}

// ---- chaos agent (scripts/chaos_agent.py): crash / start a node. Dev and demo only. ----
export const CHAOS_URL = (import.meta.env.VITE_CHAOS_URL || 'http://127.0.0.1:9000').replace(/\/$/, '')
// The chaos agent has its own admin key. In auth mode it is typed in at run time (auth/AuthPanel.jsx) and held
// in memory only: it is never baked into the bundle.
let ADMIN_KEY = AUTH_ENABLED ? '' : (import.meta.env.VITE_ADMIN_KEY || 'dev-admin')
export const setChaosKey = (k) => { ADMIN_KEY = k }
export const hasChaosKey = () => Boolean(ADMIN_KEY)
export const API_KEY = KEY

async function chaos(path, method = 'POST', payload) {
  if (FIXTURES) throw new Error('fixture mode: chaos disabled')
  const ctl = new AbortController()
  const t = setTimeout(() => ctl.abort(), 15000)
  try {
    const headers = { 'X-Admin-Key': ADMIN_KEY, ...(payload ? { 'Content-Type': 'application/json' } : {}) }
    const r = await fetch(CHAOS_URL + path, { method, headers, body: payload ? JSON.stringify(payload) : undefined, signal: ctl.signal })
    const b = await r.json().catch(() => null)
    if (!r.ok) throw Object.assign(new Error(`HTTP ${r.status}`), { status: r.status, body: b })
    return b
  } finally {
    clearTimeout(t)
  }
}
export const crashRegion = (id) => chaos(`/crash/${id}`)
export const startRegion = (id) => chaos(`/start/${id}`)
export const chaosState = () => chaos('/state', 'GET')
// Dev-only: inject one bad event (mode: edit | forge | reuse) and report whether it was quarantined.
export const tamperRegion = (id, mode) => chaos(`/tamper/${id}`, 'POST', { mode })

// ---- direct, single-node calls (no failover): used by the Prove-it script ----
export async function nodeCall(region, method, path, payload) {
  try {
    const b = await call(region.url + path, method === 'GET' ? {} : { method, body: JSON.stringify(payload || {}) })
    return { ok: true, status: 200, body: b }
  } catch (e) {
    return { ok: false, status: e.status || 0, body: e.body || null }
  }
}
export const nodeStatus = async (region) => (await nodeCall(region, 'GET', '/v1/status')).body
export const nodeReport = async (region, id) => (await nodeCall(region, 'GET', `/v1/reports/${id}`)).body
export async function nodeHas(region, ids) {
  const out = []
  for (let i = 0; i < ids.length; i += 500) {
    const r = await nodeCall(region, 'POST', '/v1/events/has', { ids: ids.slice(i, i + 500) })
    if (!r.ok) return null
    out.push(...r.body.missing)
  }
  return { missing: out }
}
