import { useState } from 'react'
import { SYNCSHIELD_CLAIMS } from '@true/syncshield-shared'
import { hasChaosKey, setChaosKey } from '../api.js'
import Badge from '../components/Badge.jsx'
import { useAuth } from './AuthContext.jsx'

const NEVER_SHARED = ['Name', 'Date of birth', 'Address', 'Photo', 'Student number (PNR)']
const CLAIM_LABEL = {
  actor_id: 'Your opaque member id', syncshield_member: 'Is a SyncShield member', syncshield_role: 'Role (officer / supervisor / admin)',
  credential_id: 'Credential id (for revocation checks)',
}

// Sign-in with a TRUE credential. Everything on this panel is real: claim codes are redeemed at the issuer, the
// consent card lists exactly the claims the region's challenge asks for, and the session comes from the region.
export default function AuthPanel() {
  const auth = useAuth()
  const [block, setBlock] = useState('')
  const [results, setResults] = useState([])
  const [pending, setPending] = useState(null)   // persona waiting for consent
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [chaosKey, setKey] = useState('')
  if (!auth || !auth.enabled) return null

  const claim = async () => {
    setBusy('claim')
    setResults(await auth.claimBlock(block))
    setBlock('')
    setBusy('')
  }
  const signIn = async (persona, skipConsent = false) => {
    setError('')
    if (!skipConsent && !auth.consent.has(persona)) { setPending(persona); return }
    auth.consent.add(persona)
    setPending(null)
    setBusy(persona)
    try {
      await auth.loginNow(persona)
      if (!auth.active) auth.setActive(persona)
    } catch (e) {
      setError(`${persona}: ${e?.body?.error?.message || e.message}`)
    }
    setBusy('')
  }
  const signInAll = async () => {
    for (const p of auth.names) {
      auth.consent.add(p)
      // eslint-disable-next-line no-await-in-loop
      if (!auth.sessionOf(p)) await signIn(p, true)
    }
  }

  return (
    <section className="panel">
      <h2>Sign in with TRUE</h2>
      <p className="muted">
        Your wallet proves you are an eligible member without revealing who you are. No API key is used in this mode.
      </p>

      {auth.names.length === 0 && (
        <div className="banner warn">
          No credentials in this wallet yet. Run <code>python scripts/true_stack.py up --provision</code>, then paste the
          claim codes it prints below.
        </div>
      )}

      <div className="create">
        <textarea rows={3} placeholder={'Alice: ABCD2345\nBob: EFGH6789'} value={block} onChange={(e) => setBlock(e.target.value)} />
        <button onClick={claim} disabled={!block.trim() || busy === 'claim'}>{busy === 'claim' ? 'claiming…' : 'Claim credentials'}</button>
        {results.map((r) => (
          <span key={r.persona} className={`chip ${r.ok ? 'good' : 'bad'}`}>{r.persona}: {r.ok ? `issued (${r.role})` : r.error}</span>
        ))}
      </div>

      {auth.names.length > 0 && (
        <table>
          <thead><tr><th>member</th><th>role (card)</th><th>session</th><th></th></tr></thead>
          <tbody>
            {auth.names.map((p) => {
              const s = auth.sessionOf(p)
              const live = s && s.exp * 1000 > Date.now()
              return (
                <tr key={p} className={auth.active === p ? 'selected' : ''}>
                  <td>
                    <label><input type="radio" name="active" checked={auth.active === p} onChange={() => auth.setActive(p)} /> <b>{p}</b></label>
                  </td>
                  <td>{auth.cards[p]?.role ?? '?'}</td>
                  <td>
                    {live
                      ? <><span className="chip good">signed in</span> <Badge value={s.auth_mode} /> <span className="muted">via {s.region} · {s.role}</span></>
                      : <span className="chip">signed out</span>}
                  </td>
                  <td className="node-actions">
                    {!live && <button disabled={busy === p} onClick={() => signIn(p)}>{busy === p ? '…' : 'Sign in'}</button>}
                    {live && <button className="cut" onClick={() => auth.logout(p)}>Sign out</button>}
                    <button className="cut" title="Remove this credential and its key from the wallet" onClick={() => auth.remove(p)}>Remove</button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
      {auth.names.length > 1 && <button onClick={signInAll}>Sign everyone in</button>}

      {pending && (
        <div className="verdict pass">
          <strong>{pending} will share with SyncShield:</strong>
          <ul>{SYNCSHIELD_CLAIMS.map((c) => <li key={c}>{CLAIM_LABEL[c] ?? c}</li>)}</ul>
          <div className="muted">Not shared: {NEVER_SHARED.join(', ')}.</div>
          <div className="node-actions">
            <button onClick={() => signIn(pending, true)}>Share and sign in</button>
            <button className="cut" onClick={() => setPending(null)}>Cancel</button>
          </div>
        </div>
      )}
      {error && <div className="banner bad">{error}</div>}

      <details>
        <summary className="muted">Judge tools: chaos agent key</summary>
        <div className="row-gap">
          <input type="password" placeholder="chaos agent admin key (kept in memory only)" value={chaosKey}
                 onChange={(e) => { setKey(e.target.value); setChaosKey(e.target.value) }} />
          <span className={`chip ${hasChaosKey() ? 'good' : ''}`}>{hasChaosKey() ? 'key set' : 'not set'}</span>
        </div>
      </details>

      {auth.log.length > 0 && <pre className="log">{auth.log.join('\n')}</pre>}
    </section>
  )
}
