// The identity section of the console (joining step J4): claim-code onboarding, sign-in with the wallet, the panel
// of lane C, and a revoke button. It renders nothing unless the backend is in sign-in mode, so with sign-in off the
// console is exactly what it was.
//
// Every value on screen comes from the regions (/v1/auth/me) or the wallet; nothing is invented here. Sessions are
// kept in memory only (session.ts); a page reload means "sign in again".
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { useDemo } from '../state/demo'
import { keys } from '../api/liveClient'
import { IdentityPanel } from './IdentityPanel'
import { revokeShopper, signIn, type Claim } from './client'
import { browserWallet, type BrowserWallet } from './wallet'
import {
  PERSONAS, RELAY_URL, clearSession, parseClaimBlock, getVersion, isRevoked, markRevoked, sessionOf, setSession, subscribe,
} from './session'

const env = import.meta.env as Record<string, string | undefined>
const ISSUER_URL = env.VITE_ISSUER_URL || '/issuer'
const RENEW_BEFORE_S = 30

export function IdentityHost() {
  const d = useDemo()
  useSyncExternalStore(subscribe, getVersion)
  const wallets = useRef(new Map<string, BrowserWallet>())
  const walletOf = (p: string) => { let w = wallets.current.get(p); if (!w) { w = browserWallet(p); wallets.current.set(p, w) } return w }
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const [persona, setPersona] = useState<string>(d.shopper)
  const [holds, setHolds] = useState<Record<string, Record<string, unknown> | null>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [codes, setCodes] = useState('')
  const [claimMsg, setClaimMsg] = useState<string | null>(null)

  // follow the shopper chosen in the shop (staff personas stay selectable by hand)
  useEffect(() => { if (PERSONAS.some((p) => p.name === d.shopper)) setPersona(d.shopper) }, [d.shopper])

  const regions = useMemo(() => d.members.map((r) => ({ id: r.id, url: r.url })), [d.members])
  const refreshHolds = useCallback(async () => {
    const next: Record<string, Record<string, unknown> | null> = {}
    for (const p of PERSONAS) next[p.name] = await walletOf(p.name).holds().catch(() => null)
    setHolds(next)
  }, [])
  useEffect(() => { if (d.authMode) void refreshHolds() }, [d.authMode, refreshHolds])
  useEffect(() => () => { for (const t of timers.current.values()) clearTimeout(t) }, [])

  const login = useCallback(async (p: string, claim: Claim | null): Promise<boolean> => {
    // The region, not this page, picks nonce and audience; a region that is down just means "ask the next one".
    const ordered = [...regions].sort((a, b) => Number(Boolean(d.snaps[b.id]?.ok)) - Number(Boolean(d.snaps[a.id]?.ok)))
    try {
      // A credential claimed a moment ago is not in the regions' status list until their next refresh (about 5 seconds):
      // "not active" right after onboarding is worth a few patient retries; any other refusal is final.
      let s
      for (let attempt = 0; ; attempt++) {
        try { s = await signIn({ regions: ordered, wallet: walletOf(p), claim }); break } catch (e) {
          if (attempt >= 4 || !(e instanceof Error) || !/not active/i.test(e.message)) throw e
          await new Promise((r) => setTimeout(r, 2500))
        }
      }
      setSession(p, s)
      clearTimeout(timers.current.get(p))
      const ms = Math.max(5_000, (s.expiresAt - RENEW_BEFORE_S) * 1000 - Date.now())
      timers.current.set(p, setTimeout(() => { void login(p, claim).catch(() => clearSession(p)) }, ms))
      return true
    } catch (e) {
      clearSession(p)
      throw e
    }
  }, [regions, d.snaps])

  const onSignIn = async (claim: Claim | null) => {
    setBusy(true); setError(null)
    try { await login(persona, claim) } catch (e) { setError(e instanceof Error ? e.message : 'Sign-in failed') }
    setBusy(false)
  }
  const onSignInAll = async () => {
    setBusy(true); setError(null)
    for (const p of PERSONAS) {
      if (!holds[p.name] || isRevoked(p.name) || sessionOf(p.name)) continue
      try { await login(p.name, null) } catch (e) { setError(`${p.name}: ${e instanceof Error ? e.message : 'sign-in failed'}`) }
    }
    setBusy(false)
  }
  const onSignOut = () => { clearTimeout(timers.current.get(persona)); clearSession(persona) }
  const onRevoke = async () => {
    const info = PERSONAS.find((p) => p.name === persona)
    if (!info) return
    setBusy(true); setError(null)
    try {
      await revokeShopper(RELAY_URL, info.member, keys().admin)
      markRevoked(persona)
      d.toast('warn', `${persona}'s credential was revoked. Each region refuses their session after its next status refresh (a few seconds).`)
    } catch (e) { setError(e instanceof Error ? e.message : 'Revoke failed (is `python scripts/true_stack.py relay` running?)') }
    setBusy(false)
  }
  const onClaim = async () => {
    const entries = parseClaimBlock(codes)
    if (!entries.length) { setClaimMsg('No "Name: CODE" lines found.'); return }
    setBusy(true); setClaimMsg(null)
    const done: string[] = []; const failed: string[] = []
    for (const { persona: p, code } of entries) {
      try { await walletOf(p).claim(code, ISSUER_URL); done.push(p) } catch (e) { failed.push(`${p}: ${e instanceof Error ? e.message : 'failed'}`) }
    }
    await refreshHolds()
    setClaimMsg([done.length ? `Added ${done.join(', ')}.` : '', failed.length ? `Not added: ${failed.join('; ')}.` : ''].filter(Boolean).join(' '))
    if (!failed.length) setCodes('')
    setBusy(false)
  }

  if (!d.authMode) return null
  const session = sessionOf(persona)
  const have = PERSONAS.filter((p) => holds[p.name])
  return (
    <div className="idp-host" data-testid="identity-host">
      <div className="idp-host__bar">
        <label className="idp__ask"><span className="muted">Identity of</span>
          <select value={persona} onChange={(e) => setPersona(e.target.value)} aria-label="Whose identity">
            {PERSONAS.map((p) => <option key={p.name} value={p.name}>{p.name}{p.role !== 'officer' ? ` (${p.role})` : ''}</option>)}
          </select>
        </label>
        <button className="btn btn--sm" disabled={busy || !have.length} onClick={() => void onSignInAll()}>Sign in everyone</button>
        <span className="muted small">{have.length ? `${have.length} of ${PERSONAS.length} credentials in this wallet` : 'No credentials in this wallet yet'}</span>
      </div>
      <details className="idp-host__claim card" open={!have.length}>
        <summary className="small">Add claim codes to this wallet</summary>
        <div>
          <p className="small">Start the stack with <code>python scripts/true_stack.py up --provision</code> and paste the claim codes it prints (one <code>Name: CODE</code> per line). Each code works once.</p>
          <textarea value={codes} onChange={(e) => setCodes(e.target.value)} rows={4} aria-label="Claim codes" placeholder={'Alice: ABCD-EFGH-…\nBob: …'} />
          <div><button className="btn btn--sm btn--primary" disabled={busy || !codes.trim()} onClick={() => void onClaim()}>Add to wallet</button></div>
          {claimMsg && <p className="small" role="status">{claimMsg}</p>}
        </div>
      </details>
      <IdentityPanel
        shopper={persona}
        holds={holds[persona] ?? null}
        session={session ? (({ token: _t, regionUrl: _u, ...rest }) => rest)(session) : null}
        revoked={isRevoked(persona)}
        busy={busy}
        error={error}
        onSignIn={(c) => void onSignIn(c)}
        onSignOut={onSignOut}
        onRevoke={() => void onRevoke()}
      />
    </div>
  )
}

export default IdentityHost
