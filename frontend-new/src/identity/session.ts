// Sign-in state for the console: which personas hold a live session, and the bearer token each request carries.
//
// Tokens live ONLY in this module's memory. They are never written to localStorage, IndexedDB or a URL, and they
// are not put in any event: a page reload means "sign in again" (the credential and its non-exportable holder key
// stay in the wallet, so that is one click).
import type { IdentitySession } from './client'

/** The loopback revoke relay of `python scripts/true_stack.py relay` (proxied by vite.config.ts). */
export const RELAY_URL = (import.meta.env.VITE_RELAY_URL as string | undefined) || '/relay'

export type Persona = string
export type PersonaInfo = { name: Persona; role: 'officer' | 'supervisor' | 'admin'; member: string }

/** The synthetic members `python scripts/true_stack.py up --provision` creates; `member` is the name the issuer knows. */
export const PERSONAS: PersonaInfo[] = [
  { name: 'Alice', role: 'officer', member: 'Alice' },
  { name: 'Bob', role: 'officer', member: 'Bob' },
  { name: 'Chitra', role: 'officer', member: 'Chitra' },
  { name: 'Dev', role: 'officer', member: 'Dev' },
  { name: 'Esha', role: 'officer', member: 'Esha' },
  { name: 'Sunita', role: 'supervisor', member: 'Sunita (supervisor)' },
  { name: 'Admin', role: 'admin', member: 'Admin Judge' },
]

type Stored = IdentitySession
const sessions = new Map<Persona, Stored>()
const revoked = new Set<Persona>()
const listeners = new Set<() => void>()
let version = 0
let bearerMode = (import.meta.env.VITE_AUTH_ENABLED as string | undefined) === '1'  // the console is told up front; detection confirms it

const bump = () => { version += 1; listeners.forEach((l) => l()) }
export const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }
export const getVersion = () => version

/** Sign-in mode of the backend: when on, every request carries a session instead of an API key. */
export function setBearerMode(on: boolean) { if (bearerMode !== on) { bearerMode = on; bump() } }
export const bearerModeOn = () => bearerMode

const live = (s: Stored, now = Date.now()) => s.expiresAt * 1000 > now

export function setSession(p: Persona, s: Stored) { sessions.set(p, s); revoked.delete(p); bump() }
export function clearSession(p: Persona) { if (sessions.delete(p)) bump() }
export function markRevoked(p: Persona) { revoked.add(p); bump() }
export const isRevoked = (p: Persona) => revoked.has(p)
export const sessionOf = (p: Persona): Stored | null => { const s = sessions.get(p); return s && live(s) ? s : null }
export const liveSessions = (): { persona: Persona; session: Stored }[] =>
  [...sessions.entries()].filter(([, s]) => live(s)).map(([persona, session]) => ({ persona, session }))
export const resetSessions = () => { sessions.clear(); revoked.clear(); bump() }

/**
 * The token for one request, or null when there is none (the region then answers 401, which the console shows as it is).
 * Never falls back to somebody else's identity: a write as Bob uses Bob's session or nothing.
 *   actor           a shopper's own session
 *   as supervisor   a supervisor session (resolve); as admin: an admin session
 *   anything else   reads: the broadest live view (supervisor, then admin, then any member)
 */
export function tokenFor(opts: { actor?: string; as?: string }): string | null {
  if (opts.actor) return sessionOf(opts.actor)?.token ?? null
  const all = liveSessions()
  if (opts.as === 'supervisor' || opts.as === 'admin') return all.find((x) => x.session.role === opts.as)?.session.token ?? null
  const pick = all.find((x) => x.session.role === 'supervisor') ?? all.find((x) => x.session.role === 'admin') ?? all[0]
  return pick?.session.token ?? null
}
