// Stand-ins for sha256 and Ed25519 used by the MOCK simulator only (the real backend uses the real ones).
// They keep the same properties the demo needs: editing any field breaks the hash, and only the origin's
// own "key" produces a signature that the other nodes accept.
import type { EventEnvelope } from '../api/types'
import { canonical, hash256 } from '../lib/util'

const secretOf = (origin: string) => `demo-secret-${origin}`

/** Hash over every field except hash, sig and the history-only columns. */
export function eventHash(ev: Partial<EventEnvelope>): string {
  const { hash: _h, sig: _s, sig_status: _ss, carried: _c, ...rest } = ev as any
  return hash256(canonical(rest))
}

export function sign(origin: string, hash: string): string {
  return 'ed25519:' + hash256(`${secretOf(origin)}|${hash}`).slice(0, 48)
}

export function verify(knownOrigins: Iterable<string>, origin: string, hash: string, sig: string | null): boolean {
  if (typeof sig !== 'string' || typeof origin !== 'string') return false
  if (![...knownOrigins].includes(origin)) return false
  return sig === sign(origin, hash)
}
