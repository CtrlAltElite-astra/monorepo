// The identity section of the console (joining step J4): sign-in with the wallet (the credential is issued automatically
// the first time), the panel of lane C, and a revoke button. It renders nothing unless the backend is in sign-in mode, so with sign-in off the
// console is exactly what it was.
//
// Every value on screen comes from the regions (/v1/auth/me) or the wallet; nothing is invented here. Sessions are
// kept in memory only (session.ts); a page reload means "sign in again".
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { useDemo } from '../state/demo'
import { keys } from '../api/liveClient'
import { IdentityPanel } from './IdentityPanel'
import { requestClaimCode, revokeShopper, signIn, type Claim } from './client'
import { browserWallet, type BrowserWallet } from './wallet'
import {
  PERSONAS, RELAY_URL, clearSession, getVersion, isRevoked, markRevoked, sessionOf, setSession, subscribe,
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
    const info = PERSONAS.find((x) => x.name === p)
    const wallet = walletOf(p)
    // Onboarding: fetch a one-time code from the demo relay and redeem it (holder proof, then the issuer signs the credential).
    // After that the wallet holds the credential and nobody has to paste anything.
    const issue = async () => {
      try {
        await wallet.claim(await requestClaimCode(RELAY_URL, info!.member, keys().admin), ISSUER_URL)
      } catch (e) {
        throw new Error(`Could not issue ${p}'s credential (${e instanceof Error ? e.message : 'failed'}). Is \`python scripts/true_stack.py up\` running?`)
      }
      await refreshHolds()
    }
    if (info && !(await wallet.hasCredential())) await issue()

    // The region, not this page, picks nonce and audience; a region that is down just means "ask the next one".
    const ordered = [...regions].sort((a, b) => Number(Boolean(d.snaps[b.id]?.ok)) - Number(Boolean(d.snaps[a.id]?.ok)))
    // A credential claimed a moment ago is not in the regions' status list until their next refresh (about 5 seconds):
    // "not active" right after onboarding is worth a few patient retries; any other refusal is final.
    const attempt = async () => {
      for (let n = 0; ; n++) {
        try { return await signIn({ regions: ordered, wallet, claim }) } catch (e) {
          if (n >= 4 || !(e instanceof Error) || !/not active/i.test(e.message)) throw e
          await new Promise((r) => setTimeout(r, 2500))
        }
      }
    }
    try {
      let s
      try { s = await attempt() } catch (first) {
        // A credential left in this browser by an earlier run of the demo (another issuer) can never verify again: issue a fresh
        // one, once. A revoked member cannot get one, and then the original refusal is the honest answer.
        if (!info || isRevoked(p) || /revoked/i.test(first instanceof Error ? first.message : '')) throw first
        try { await issue() } catch { throw first }
        s = await attempt()
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
    // Everyone at once: each member's credential is issued and signed in together, so the wait for the regions' status
    // refresh (about 5 seconds) is paid once, not once per member.
    const todo = PERSONAS.filter((p) => !isRevoked(p.name) && !sessionOf(p.name))
    const results = await Promise.allSettled(todo.map((p) => login(p.name, null)))
    const failed = results.flatMap((r, i) => (r.status === 'rejected' ? [`${todo[i].name}: ${r.reason instanceof Error ? r.reason.message : 'sign-in failed'}`] : []))
    if (failed.length) setError(failed.join(' | '))
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
        <button className="btn btn--sm" disabled={busy} onClick={() => void onSignInAll()}>Sign in everyone</button>
        <span className="muted small">{have.length === PERSONAS.length ? 'All 7 members are ready' : 'Each member\'s credential is issued the first time they sign in'}</span>
      </div>
      <IdentityPanel
        shopper={persona}
        holds={holds[persona] ?? null}
        session={session ? (({ token: _t, regionUrl: _u, ...rest }) => rest)(session) : null}
        revoked={isRevoked(persona)}
        busy={busy}
        onboard
        error={error}
        onSignIn={(c) => void onSignIn(c)}
        onSignOut={onSignOut}
        onRevoke={() => void onRevoke()}
      />
    </div>
  )
}

export default IdentityHost
