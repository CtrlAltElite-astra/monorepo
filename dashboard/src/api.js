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
const headers = (key) => ({ 'X-API-Key': key, 'Content-Type': 'application/json' })

async function call(url, opts = {}, key = KEY) {
  if (FIXTURES && opts.method) throw new Error('fixture mode: writes disabled')
  const ctl = new AbortController()
  const t = setTimeout(() => ctl.abort(), 1500)
  try {
    const r = await fetch(url, { ...opts, headers: headers(key), signal: ctl.signal })
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
    const [status, reports] = await Promise.all([call(`${url}/v1/status`), call(`${url}/v1/reports`)])
    const [review, metrics] = await Promise.all([optional(region, '/v1/review'), optional(region, '/v1/metrics')])
    return { id, url, ok: true, status, reports, review, metrics }
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
  call(`${r.url}/v1/reports/${id}/resolve`, { method: 'POST', body: body(payload) }, SUPERVISOR_KEY)

export async function getHistory(r, id) {
  if (FIXTURES) return mock.history
  return call(`${r.url}/v1/reports/${id}/history`)
}

// Cut or heal one pair: the switch is set on BOTH regions (each backend enforces its own half).
export async function setPairLink(a, b, mode) {
  return Promise.allSettled([
    call(`${a.url}/admin/links/${b.id}`, { method: 'POST', body: body({ mode }) }),
    call(`${b.url}/admin/links/${a.id}`, { method: 'POST', body: body({ mode }) }),
  ])
}
