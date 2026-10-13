// Identity client for the console (lane C). Plain fetch against a region's /v1/auth/* routes; no other file in
// frontend-new is needed, so this folder compiles on its own.
//
// Sign-in: the REGION chooses nonce and audience (challenge), the wallet reveals only what the challenge asks for
// (the sign-in minimum, plus at most ONE attribute such as age_over_18), and the region answers with a short-lived
// session token. Tokens are returned to the caller and kept in memory only: never localStorage, never a URL.

export type Claim = 'age_over_18' | 'member'
export const CLAIMS: readonly Claim[] = ['age_over_18', 'member']
export const CLAIM_LABEL: Record<string, string> = {
  age_over_18: 'Over 18', member: 'Loyalty member',
  actor_id: 'Member id', credential_id: 'Credential id', syncshield_member: 'SyncShield member', syncshield_role: 'Role',
}

export type Challenge = {
  challenge_id: string; nonce: string; audience: string; expires_at: number; required_claims: string[]; region: string
}

export type PresentRequest = { claims: string[]; nonce: string; audience: string }

// What the panel needs from a wallet. wallet.ts implements it with the partner's real SD-JWT code; tests pass a fake.
export interface Wallet {
  holds(): Promise<Record<string, unknown> | null>   // every attribute in the credential (display only)
  present(req: PresentRequest): Promise<string>      // a holder-bound presentation revealing only req.claims
}

export type IdentitySession = {
  token: string
  region: string
  regionUrl: string
  actorId: string
  credentialId: string
  role: string
  authMode: string
  claims: Claim[]        // proven at sign-in
  disclosed: string[]    // attribute names the shop received
  expiresAt: number      // epoch seconds
}

export class IdentityError extends Error {
  constructor(message: string, readonly status: number, readonly code: string) { super(message) }
}

type Fetch = typeof fetch

async function json<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = body?.error ?? body?.detail ?? {}
    throw new IdentityError(err.message || `HTTP ${res.status}`, res.status, err.code || err.error || `http_${res.status}`)
  }
  return body as T
}

const post = (f: Fetch, url: string, body: unknown) =>
  f(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })

export async function requestChallenge(regionUrl: string, claim: Claim | null = null, f: Fetch = fetch): Promise<Challenge> {
  return json(await post(f, `${regionUrl}/v1/auth/challenge`, claim ? { claim } : {}))
}

export async function me(regionUrl: string, token: string, f: Fetch = fetch) {
  return json<{
    actor_id: string; role: string; credential_id: string; auth_mode: string; region: string
    session_expires_at: number | null; claims: Claim[]; disclosed: string[]
  }>(await f(`${regionUrl}/v1/auth/me`, { headers: { Authorization: `Bearer ${token}` } }))
}

// Try each region in order. A region that is down (network error, 5xx, 503) just means "ask the next one"
// with a fresh challenge; a real denial (401/403/409...) stops, because another region would say the same.
export async function signIn(opts: {
  regions: { id: string; url: string }[]; wallet: Wallet; claim?: Claim | null; fetch?: Fetch
}): Promise<IdentitySession> {
  const f = opts.fetch ?? fetch
  let last: unknown = new IdentityError('No region to sign in at', 0, 'no_region')
  for (const r of opts.regions) {
    try {
      const ch = await requestChallenge(r.url, opts.claim ?? null, f)
      const presentation = await opts.wallet.present({ claims: ch.required_claims, nonce: ch.nonce, audience: ch.audience })
      const out = await json<{ token: string; expires_at: number; auth_mode: string }>(
        await post(f, `${r.url}/v1/auth/session`, { challenge_id: ch.challenge_id, presentation }))
      const who = await me(r.url, out.token, f)   // claims and disclosed as the REGION recorded them
      return {
        token: out.token, region: who.region || r.id, regionUrl: r.url, actorId: who.actor_id, credentialId: who.credential_id,
        role: who.role, authMode: who.auth_mode, claims: who.claims ?? [], disclosed: who.disclosed ?? [],
        expiresAt: who.session_expires_at ?? out.expires_at,
      }
    } catch (e) {
      last = e
      if (e instanceof IdentityError && e.status > 0 && e.status < 500) break
    }
  }
  throw last
}

// Demo only: revoke a shopper through the loopback admin relay (`python scripts/true_stack.py relay`), which holds
// the issuer admin token server-side. The browser never sees that token; the relay asks for the console admin key.
export async function revokeShopper(relayUrl: string, member: string, adminKey: string, reason = 'revoked in the demo', f: Fetch = fetch) {
  const res = await f(`${relayUrl}/revoke`, {
    method: 'POST', headers: { 'content-type': 'application/json', 'X-Admin-Key': adminKey },
    body: JSON.stringify({ member, reason }),
  })
  return json<{ revokedCredentials: number; statusVersion: number }>(res)
}

// Demo only: a fresh one-time claim code for one demo member, from the loopback relay (`python scripts/true_stack.py up` starts it).
// The wallet then redeems it exactly as before (holder proof, then the issuer signs the credential), so nobody pastes codes.
export async function requestClaimCode(relayUrl: string, member: string, adminKey: string, f: Fetch = fetch): Promise<string> {
  const res = await f(`${relayUrl}/claim-code`, {
    method: 'POST', headers: { 'content-type': 'application/json', 'X-Admin-Key': adminKey }, body: JSON.stringify({ member }),
  })
  const out = await json<{ code: string }>(res)
  return out.code
}

export function secondsLeft(session: Pick<IdentitySession, 'expiresAt'> | null, nowMs: number): number {
  return session ? Math.max(0, Math.floor(session.expiresAt - nowMs / 1000)) : 0
}
