// Small dependency-free helpers shared by the simulator, the SDK and the UI.

export const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export function uuid(): string {
  const c = (globalThis as any).crypto
  if (c?.randomUUID) return c.randomUUID()
  const hex = () => Math.floor(Math.random() * 16).toString(16)
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => (ch === 'x' ? hex() : ((Math.random() * 4) | 8).toString(16)))
}

/** A client-chosen cart id: "R-" + 8 lowercase hex characters (contract 3.3). */
export function newReportId(): string {
  const bytes = new Uint8Array(4)
  const c = (globalThis as any).crypto
  if (c?.getRandomValues) c.getRandomValues(bytes)
  else for (let i = 0; i < 4; i++) bytes[i] = Math.floor(Math.random() * 256)
  return 'R-' + Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

export const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n))

export function median(values: number[]): number | null {
  if (!values.length) return null
  const s = [...values].sort((a, b) => a - b)
  const mid = Math.floor(s.length / 2)
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2
}

/** Canonical JSON: sorted keys, no spaces (the same shape the backend hashes). */
export function canonical(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null'
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  const obj = value as Record<string, unknown>
  return '{' + Object.keys(obj).sort().filter((k) => obj[k] !== undefined).map((k) => JSON.stringify(k) + ':' + canonical(obj[k])).join(',') + '}'
}

function cyrb53(str: string, seed: number): number {
  let h1 = 0xdeadbeef ^ seed
  let h2 = 0x41c6ce57 ^ seed
  for (let i = 0; i < str.length; i++) {
    const ch = str.charCodeAt(i)
    h1 = Math.imul(h1 ^ ch, 2654435761)
    h2 = Math.imul(h2 ^ ch, 1597334677)
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909)
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909)
  return 4294967296 * (2097151 & h2) + (h1 >>> 0)
}

/** 64 hex chars. A demo hash for the simulator only; the real backend uses sha256 and Ed25519. */
export function hash256(text: string): string {
  let out = ''
  for (let i = 0; i < 4; i++) out += cyrb53(text, 0x9e37 + i * 7919).toString(16).padStart(14, '0').slice(-16)
  return out
}

export const short = (hash: string | null | undefined, n = 8) => (hash ? hash.slice(0, n) : '—')

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`
}

export function fmtAge(ms: number): string {
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

export function fmtClock(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export const rupees = (n: number) => '₹' + n.toLocaleString('en-IN')

export function groupBy<T, K extends string>(items: T[], key: (t: T) => K): Record<K, T[]> {
  const out = {} as Record<K, T[]>
  for (const item of items) (out[key(item)] ||= []).push(item)
  return out
}
