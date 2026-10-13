// Joining step J4: the console's sign-in plumbing. Pure logic: no browser, no backend.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createLiveTransport } from '../src/api/liveClient'
import type { IdentitySession } from '../src/identity/client'
import {
  PERSONAS, liveSessions, markRevoked, parseClaimBlock, resetSessions, sessionOf, setBearerMode, setSession, tokenFor,
} from '../src/identity/session'

const NOW = () => Math.floor(Date.now() / 1000)
const session = (token: string, role: string, extra: Partial<IdentitySession> = {}): IdentitySession => ({
  token, region: 'mum', regionUrl: '/api/mum', actorId: 'act_x', credentialId: 'cred_x', role, authMode: 'online',
  claims: [], disclosed: [], expiresAt: NOW() + 300, ...extra,
})

afterEach(() => { resetSessions(); setBearerMode(false); vi.unstubAllGlobals() })

describe('tokens: whose session a request carries', () => {
  it('a write as a shopper uses that shopper\'s session, and never anybody else\'s', () => {
    setSession('Alice', session('tok-alice', 'officer'))
    setSession('Sunita', session('tok-sup', 'supervisor'))
    expect(tokenFor({ actor: 'Alice' })).toBe('tok-alice')
    expect(tokenFor({ actor: 'Bob' })).toBeNull()          // Bob has no session: no token, not Alice's or the supervisor's
  })

  it('reads and review use the broadest live view: supervisor, then admin, then any member', () => {
    setSession('Alice', session('tok-alice', 'officer'))
    expect(tokenFor({})).toBe('tok-alice')
    setSession('Admin', session('tok-admin', 'admin'))
    expect(tokenFor({})).toBe('tok-admin')
    setSession('Sunita', session('tok-sup', 'supervisor'))
    expect(tokenFor({})).toBe('tok-sup')
    expect(tokenFor({ as: 'admin' })).toBe('tok-admin')
    expect(tokenFor({ as: 'supervisor' })).toBe('tok-sup')
  })

  it('an expired session carries nothing', () => {
    setSession('Alice', session('tok-old', 'officer', { expiresAt: NOW() - 1 }))
    expect(sessionOf('Alice')).toBeNull()
    expect(liveSessions()).toEqual([])
    expect(tokenFor({ actor: 'Alice' })).toBeNull()
  })

  it('a re-sign-in clears the revoked mark', () => {
    markRevoked('Bob')
    setSession('Bob', session('tok-bob', 'officer'))
    expect(sessionOf('Bob')?.token).toBe('tok-bob')
  })
})

describe('the live transport in sign-in mode', () => {
  const calls: { url: string; init: RequestInit }[] = []
  const fakeFetch = async (url: string, init: RequestInit) => {
    calls.push({ url, init })
    return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
  }
  const send = async (opts: Record<string, unknown> = {}, method = 'POST') => {
    calls.length = 0
    vi.stubGlobal('fetch', fakeFetch)
    await createLiveTransport('mum=/api/mum').request('mum', method, '/v1/reports', opts)
    return calls[0].init.headers as Record<string, string>
  }

  it('sends the session as a bearer token and never an API key', async () => {
    setBearerMode(true)
    setSession('Alice', session('tok-alice', 'officer'))
    const h = await send({ actor: 'Alice', body: { title: 'x' } })
    expect(h.Authorization).toBe('Bearer tok-alice')
    expect(Object.keys(h).map((k) => k.toLowerCase())).not.toContain('x-api-key')
  })

  it('with no session it sends no credential at all (the region answers 401 and the console says so)', async () => {
    setBearerMode(true)
    const h = await send({ actor: 'Alice', body: { title: 'x' } })
    expect(h.Authorization).toBeUndefined()
    expect(Object.keys(h).map((k) => k.toLowerCase())).not.toContain('x-api-key')
  })

  it('outside sign-in mode it still sends the API key, exactly as before', async () => {
    setBearerMode(false)
    setSession('Alice', session('tok-alice', 'officer'))
    const h = await send({ actor: 'Alice', body: { title: 'x' } })
    expect(h['X-Api-Key']).toBeTruthy()
    expect(h.Authorization).toBeUndefined()
  })
})

describe('claim codes', () => {
  it('reads the block `true_stack.py up --provision` prints, one persona per line', () => {
    const got = parseClaimBlock('Alice: STDRX642\nBob: RZ9VMKH3\nSunita: K4PT7225\nAdmin: R63DHAKA\nnoise line\nMallory: ZZZZZZZZ')
    expect(got).toEqual([
      { persona: 'Alice', code: 'STDRX642' }, { persona: 'Bob', code: 'RZ9VMKH3' },
      { persona: 'Sunita', code: 'K4PT7225' }, { persona: 'Admin', code: 'R63DHAKA' },
    ])
  })

  it('knows every demo member and the name the issuer uses for it', () => {
    expect(PERSONAS.map((p) => p.name)).toEqual(['Alice', 'Bob', 'Chitra', 'Dev', 'Esha', 'Sunita', 'Admin'])
    expect(PERSONAS.find((p) => p.name === 'Sunita')).toMatchObject({ role: 'supervisor', member: 'Sunita (supervisor)' })
  })
})
