// The panel renders from fixture props in isolation, and the client signs in against a fake region.
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { IdentityError, signIn, type Wallet } from './client'
import { FIXTURES } from './fixtures'
import { IdentityPanel } from './IdentityPanel'

const html = (name: string) => renderToStaticMarkup(<IdentityPanel {...FIXTURES[name]} onSignIn={() => {}} onRevoke={() => {}} />)
const text = (name: string) => html(name).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')
// the "What the shop received" column only
const received = (name: string) => text(name).split('What the shop received')[1]

describe('IdentityPanel (fixture props, no backend)', () => {
  it('renders every fixture', () => {
    for (const name of Object.keys(FIXTURES)) expect(html(name)).toContain('What the shopper holds')
  })

  it('shows both columns: everything held, only the proven claim received', () => {
    const t = text('Proved over 18')
    expect(t).toContain('signed in at Mumbai · 4:12 left')
    expect(t.split('What the shop received')[0]).toContain('Loyalty member')     // held...
    expect(received('Proved over 18')).toContain('Over 18 yes proven')
    expect(received('Proved over 18')).not.toContain('Loyalty member')           // ...but never sent
  })

  it('an under-18 shopper disclosed the fact but proved nothing', () => {
    expect(received('Under 18: disclosed, not proven')).toContain('Over 18 no not proven')
  })

  it('an ordinary sign-in sends no attribute, and the held attributes stay in the wallet', () => {
    expect(received('Ordinary sign-in')).toContain('No attribute')
    expect(text('Ordinary sign-in')).toMatch(/Over 18 yes kept in wallet/)
  })

  it('signed out, expired and revoked states are honest', () => {
    expect(text('Holds a credential, not signed in')).toContain('not signed in')
    expect(received('Session expired')).toContain('Nothing: not signed in')
    expect(text('Revoked')).toContain('credential revoked')
    expect(html('Revoked')).toMatch(/<button[^>]*disabled=""[^>]*>Revoke this shopper/)
    expect(html('No credential yet')).toMatch(/<button[^>]*disabled=""[^>]*>Sign in with wallet/)
  })
})

// ---- client: a fake region and a fake wallet ----------------------------------------------------------------
type Call = { url: string; body?: unknown }

function fakeRegion(behaviour: Record<string, 'down' | 'ok' | 'deny'>, calls: Call[]): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ url, body })
    const region = url.split('/')[2]
    const mode = behaviour[region]
    if (mode === 'down') throw new TypeError('fetch failed')
    const reply = (status: number, b: unknown) => new Response(JSON.stringify(b), { status, headers: { 'content-type': 'application/json' } })
    if (url.endsWith('/v1/auth/challenge')) {
      const claims = ['actor_id', 'syncshield_member', 'syncshield_role', 'credential_id', ...(body?.claim ? [body.claim] : [])]
      return reply(201, { challenge_id: 'ch', nonce: 'n', audience: `syncshield-region:${region}`, expires_at: 0, required_claims: claims, region })
    }
    if (url.endsWith('/v1/auth/session')) {
      if (mode === 'deny') return reply(403, { error: { code: 'credential_revoked', message: 'Credential is not active' } })
      return reply(200, { token: 'tok', expires_at: 1_800_000_300, auth_mode: 'online' })
    }
    return reply(200, { actor_id: 'act_1', role: 'officer', credential_id: 'cred_1', auth_mode: 'online', region,
      session_expires_at: 1_800_000_300, claims: ['age_over_18'], disclosed: ['age_over_18'] })
  }) as typeof fetch
}

const wallet = (seen: string[][]): Wallet => ({
  holds: async () => ({}),
  present: async (req) => { seen.push(req.claims); return 'presentation' },
})

describe('signIn', () => {
  const regions = [{ id: 'mum', url: 'http://mum' }, { id: 'pun', url: 'http://pun' }]

  it('asks for exactly one attribute and takes claims from the region, not from itself', async () => {
    const calls: Call[] = [], seen: string[][] = []
    const s = await signIn({ regions, wallet: wallet(seen), claim: 'age_over_18', fetch: fakeRegion({ mum: 'ok', pun: 'ok' }, calls) })
    expect(calls[0].body).toEqual({ claim: 'age_over_18' })
    expect(seen[0]).toContain('age_over_18')
    expect(s).toMatchObject({ region: 'mum', claims: ['age_over_18'], disclosed: ['age_over_18'], token: 'tok' })
  })

  it('moves to the next region when one is down', async () => {
    const calls: Call[] = []
    const s = await signIn({ regions, wallet: wallet([]), fetch: fakeRegion({ mum: 'down', pun: 'ok' }, calls) })
    expect(s.region).toBe('pun')
    expect(calls[0].body).toEqual({})
  })

  it('stops at a real denial: another region would say the same', async () => {
    const calls: Call[] = []
    await expect(signIn({ regions, wallet: wallet([]), fetch: fakeRegion({ mum: 'deny', pun: 'ok' }, calls) }))
      .rejects.toMatchObject({ status: 403, code: 'credential_revoked' })
    expect(calls.some((c) => c.url.startsWith('http://pun'))).toBe(false)
    expect(new IdentityError('x', 403, 'c')).toBeInstanceOf(Error)
  })
})
