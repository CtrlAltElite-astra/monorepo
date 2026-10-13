// LIVE mode: plain fetch against the real regions (and the chaos agent). Configure with VITE_REGION_URLS,
// VITE_CHAOS_URL and the dev keys (see web/.env.example). The browser-visible keys are development credentials.
import type { Chaos, ChaosState, HttpResult, RegionId, RegionInfo, RequestOptions, Role, Transport } from './types'
import { NetworkError } from './types'
import { bearerModeOn, tokenFor } from '../identity/session'

const env = import.meta.env as Record<string, string | undefined>
const NAMES: Record<string, string> = { mum: 'Mumbai', pun: 'Pune', del: 'Delhi', blr: 'Bengaluru' }

/** Bengaluru is listed so it can be found when it is started, but it is not part of the mesh until it answers (see state/demo.tsx). */
export const DEFAULT_REGION_URLS = 'mum=/api/mum,pun=/api/pun,del=/api/del,blr=/api/blr'

export function parseRegions(raw: string): RegionInfo[] {
  return raw.split(',').map((item) => item.trim()).filter(Boolean).map((item) => {
    const eq = item.indexOf('=')
    const id = item.slice(0, eq).trim() as RegionId
    return { id, name: NAMES[id] ?? id.toUpperCase(), url: item.slice(eq + 1).trim().replace(/\/$/, '') }
  })
}

export const keys = (): Record<Role, string> => ({
  officer: env.VITE_OFFICER_KEY || 'dev-officer',
  supervisor: env.VITE_SUPERVISOR_KEY || 'dev-supervisor',
  admin: env.VITE_ADMIN_KEY || 'dev-admin',
})

/** Optional per-shopper API keys, "Alice=dev-alice,Bob=dev-bob". With a key per shopper a region can count each shopper's own
 *  allowance; without them every shopper uses the officer key and so shares one. */
export function shopperKeys(raw = env.VITE_SHOPPER_KEYS || ''): Record<string, string> {
  const out: Record<string, string> = {}
  for (const item of raw.split(',')) {
    const eq = item.indexOf('=')
    if (eq > 0 && item.slice(eq + 1).trim()) out[item.slice(0, eq).trim()] = item.slice(eq + 1).trim()
  }
  return out
}

async function timed(url: string, init: RequestInit, timeoutMs: number): Promise<Response> {
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), timeoutMs)
  try {
    return await fetch(url, { ...init, signal: ctl.signal })
  } catch (e: any) {
    throw new NetworkError(e?.name === 'AbortError' ? 'request timed out' : 'could not reach the node', e?.name === 'AbortError' ? 'timeout' : 'refused')
  } finally {
    clearTimeout(timer)
  }
}

export function createLiveTransport(raw = env.VITE_REGION_URLS || DEFAULT_REGION_URLS): Transport {
  const regions = parseRegions(raw)
  return {
    mode: 'live',
    regions,
    async request<T = any>(region: RegionId, method: string, path: string, opts: RequestOptions = {}): Promise<HttpResult<T>> {
      const info = regions.find((r) => r.id === region)
      if (!info) throw new NetworkError(`no such region: ${region}`, 'refused')
      const headers: Record<string, string> = { ...opts.headers }
      if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
      if (bearerModeOn()) {
        // Sign-in mode: a verified session, never an API key. No session = no header = the region's honest 401.
        const token = tokenFor({ actor: opts.actor, as: opts.as })
        if (token) headers.Authorization = `Bearer ${token}`
      } else if (method !== 'GET' || opts.as) headers['X-Api-Key'] = (!opts.as && opts.actor && shopperKeys()[opts.actor]) || keys()[opts.as ?? 'officer']
      const started = performance.now()
      const res = await timed(info.url + path, { method, headers, body: opts.body === undefined ? undefined : JSON.stringify(opts.body) }, opts.timeoutMs ?? 1500)
      const out: Record<string, string> = {}
      res.headers.forEach((v, k) => { out[k.toLowerCase()] = v })
      let body: any = null
      try { body = await res.json() } catch { body = null }
      return { status: res.status, headers: out, body, ms: Math.round(performance.now() - started) }
    },
  }
}

export function createLiveChaos(url = env.VITE_CHAOS_URL || '/chaos'): Chaos {
  const headers = () => ({ 'X-Admin-Key': keys().admin })
  const chaos: Chaos = {
    available: false,
    async crash(region) { const res = await timed(`${url}/crash/${region}`, { method: 'POST', headers: headers() }, 4000); if (!res.ok) throw new Error(`Crash refused (${res.status})`) },
    async start(region) { const res = await timed(`${url}/start/${region}`, { method: 'POST', headers: headers() }, 8000); if (!res.ok) throw new Error(`Restart refused (${res.status})`) },
    async state(): Promise<ChaosState | null> {
      try {
        const res = await timed(`${url}/state`, { headers: headers() }, 4000)
        if (!res.ok) { chaos.available = false; return null }
        chaos.available = true
        return (await res.json()) as ChaosState
      } catch { chaos.available = false; return null }
    },
  }
  return chaos
}
