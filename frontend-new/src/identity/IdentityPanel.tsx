// Identity panel (lane C, contract v1.2 C1/C3/C4): what the shopper holds against what the shop received.
//
// Display only: every value arrives through props, and every action is a callback. client.ts and wallet.ts in this
// folder do the work; the console mounts this one component into <div id="identity-slot">.
import { useEffect, useState } from 'react'
import { CLAIM_LABEL, CLAIMS, secondsLeft, type Claim, type IdentitySession } from './client'
import './identity.css'

// What every sign-in reveals: who the member is to SyncShield, nothing personal (no name, no date of birth).
export const SIGN_IN_MINIMUM = ['actor_id', 'syncshield_member', 'syncshield_role', 'credential_id']
const REGION_NAME: Record<string, string> = { mum: 'Mumbai', pun: 'Pune', del: 'Delhi', blr: 'Bengaluru' }

export type IdentityPanelProps = {
  shopper: string
  holds: Record<string, unknown> | null             // every attribute in the credential (from the wallet)
  session: Omit<IdentitySession, 'token' | 'regionUrl'> | null
  revoked?: boolean                                 // the shopper's credential was revoked (sessions now refused)
  busy?: boolean
  error?: string | null
  now?: number                                      // ms; omit to tick with the wall clock
  onSignIn?: (claim: Claim | null) => void
  onSignOut?: () => void
  onRevoke?: () => void
}

function useNow(fixed?: number): number {
  const [now, setNow] = useState(() => fixed ?? Date.now())
  useEffect(() => {
    if (fixed !== undefined) return undefined
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [fixed])
  return fixed ?? now
}

const label = (name: string) => CLAIM_LABEL[name] ?? name
function show(value: unknown): string {
  if (value === true) return 'yes'
  if (value === false) return 'no'
  const s = String(value)
  return s.length > 18 ? `${s.slice(0, 14)}…` : s
}
const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`

export function IdentityPanel(props: IdentityPanelProps) {
  const { shopper, holds, session, revoked, busy, error, onSignIn, onSignOut, onRevoke } = props
  const now = useNow(props.now)
  const [ask, setAsk] = useState<Claim | ''>('age_over_18')
  const left = secondsLeft(session, now)
  const live = Boolean(session) && left > 0
  const shared = new Set(live && session ? [...SIGN_IN_MINIMUM, ...session.disclosed] : [])
  const received = live && session ? session.disclosed : []

  return (
    <section className="card idp" aria-label={`Identity for ${shopper}`}>
      <header className="idp__head">
        <h3>Identity · {shopper}</h3>
        {revoked ? <span className="chip chip--bad">credential revoked</span>
          : live && session ? <span className="chip chip--ok">signed in at {REGION_NAME[session.region] ?? session.region} · {clock(left)} left</span>
            : <span className="chip">not signed in</span>}
      </header>

      <div className="idp__actions">
        <label className="idp__ask">
          <span className="muted">Prove</span>
          <select value={ask} onChange={(e) => setAsk(e.target.value as Claim | '')} disabled={busy}>
            <option value="">nothing extra</option>
            {CLAIMS.map((c) => <option key={c} value={c}>{label(c)}</option>)}
          </select>
        </label>
        <button className="btn btn--sm btn--primary" disabled={busy || !holds || revoked || !onSignIn}
          onClick={() => onSignIn?.(ask || null)}>{live ? 'Sign in again' : 'Sign in with wallet'}</button>
        {live && onSignOut && <button className="btn btn--sm btn--ghost" disabled={busy} onClick={onSignOut}>Sign out</button>}
        <button className="btn btn--sm btn--bad idp__revoke" disabled={busy || revoked || !onRevoke || !session}
          onClick={onRevoke}>Revoke this shopper</button>
      </div>

      <div className="idp__cols">
        <div className="idp__col">
          <h4>What the shopper holds</h4>
          {holds ? (
            <ul className="idp__list">
              {Object.entries(holds).map(([k, v]) => (
                <li key={k} className={shared.has(k) ? 'is-shared' : ''}>
                  <span>{label(k)}</span><span className="mono">{show(v)}</span>
                  <span className="idp__tag">{shared.has(k) ? 'shared' : 'kept in wallet'}</span>
                </li>
              ))}
            </ul>
          ) : <p className="muted small">No credential in this wallet yet.</p>}
        </div>

        <div className="idp__col">
          <h4>What the shop received</h4>
          {!live ? <p className="muted small">Nothing: not signed in.</p>
            : received.length === 0 ? <p className="muted small">No attribute: an ordinary sign-in.</p>
              : (
                <ul className="idp__list">
                  {received.map((k) => {
                    const proven = session!.claims.includes(k as Claim)
                    return (
                      <li key={k} className={proven ? 'is-proven' : 'is-unproven'}>
                        <span>{label(k)}</span><span className="mono">{proven ? 'yes' : 'no'}</span>
                        <span className="idp__tag">{proven ? 'proven' : 'not proven'}</span>
                      </li>
                    )
                  })}
                </ul>
              )}
          <p className="muted idp__foot">Every sign-in also sends the member id, role and credential id. Never a name or a date of birth.</p>
        </div>
      </div>

      {revoked && <p className="idp__note tone-bad">Every region refuses this shopper's session once its status list refreshes (a few seconds).</p>}
      {error && <p className="idp__note tone-bad" role="alert">{error}</p>}
    </section>
  )
}

export default IdentityPanel
