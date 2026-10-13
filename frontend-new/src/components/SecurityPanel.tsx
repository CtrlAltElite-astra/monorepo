// Integrity: signatures, quarantine, carry counters, the tamper results and the (honest) partner-trust chip.
import { useState } from 'react'
import { useDemo } from '../state/demo'
import type { EventEnvelope } from '../api/types'
import { Icon } from './icons'
import { Chip, Empty, NODE_NAME, NodeDot } from './ui'

const REASON: Record<string, string> = {
  bad_hash: 'the hash did not match the edited body',
  bad_sig: 'the signature did not match: only the origin holds its key',
  unknown_origin: 'no key is registered for that origin',
  shape: 'not a well-formed event',
}

export function SecurityPanel() {
  const d = useDemo()
  const cart = d.cartFor(d.shopper)
  const [hist, setHist] = useState<EventEnvelope[] | null>(null)
  const check = async () => { if (cart && d.viewRegion) setHist(await d.history(d.viewRegion, cart.report_id)) }
  const count = (k: string) => hist?.filter((e) => e.sig_status === k).length ?? 0

  return (
    <div className="panel security">
      <section>
        <h4><Icon name="shield" size={16} /> Per node</h4>
        <table className="table">
          <thead><tr><th>Node</th><th>Quarantined</th><th>Carried in</th><th>Duplicates</th><th>Rejected</th></tr></thead>
          <tbody>
            {d.joined.map((r) => {
              const s = d.snaps[r]?.ok ? d.snaps[r]!.status : undefined
              return (
                <tr key={r}><td><NodeDot id={r} size={8} /> {NODE_NAME[r]}</td>
                  {s ? <><td className={s.quarantined ? 'tone-bad' : ''}>{s.quarantined}</td><td>{s.carry ? s.carry.new : '—'}</td><td>{s.carry ? s.carry.dup : '—'}</td><td className={s.carry?.rejected ? 'tone-bad' : ''}>{s.carry ? s.carry.rejected : '—'}</td></>
                    : <td colSpan={4} className="muted">unknown</td>}</tr>
              )
            })}
          </tbody>
        </table>
      </section>

      <section>
        <h4><Icon name="lock" size={16} /> Signatures on {d.shopper}'s cart</h4>
        <div className="security__row">
          <button className="btn btn--sm" disabled={!cart} onClick={() => void check()}><Icon name="check" size={14} />Check history signatures</button>
          {hist && <><Chip tone="ok" icon="check">{count('verified')} verified</Chip><Chip tone={count('unsigned') ? 'warn' : 'muted'}>{count('unsigned')} unsigned</Chip><Chip tone={count('bad') ? 'bad' : 'muted'}>{count('bad')} bad</Chip><Chip tone="info" icon="package">{hist.filter((e) => e.carried).length} carried</Chip></>}
        </div>
        <p className="muted small">Every event is signed by the node that wrote it. A relaying node, or a browser carrying it, cannot forge or alter one.</p>
      </section>

      <section>
        <h4><Icon name="skull" size={16} /> Tamper results</h4>
        {!d.tamperRows.length && !d.authMode ? <Empty icon="shield" title="No tampering yet">Use the Tamper button in the Network tab.</Empty> : (
          <ul className="tamper">
            {d.tamperRows.map((r, i) => (
              <li key={i} className={r.result === 'rejected' ? 'is-ok' : 'is-bad'}>
                <Chip tone={r.result === 'rejected' ? 'ok' : 'bad'} icon={r.result === 'rejected' ? 'shield' : 'alert'}>{r.result === 'rejected' ? 'rejected' : r.result}</Chip>
                <span><b>{r.label}</b>{r.reason && <em> · {r.reason}: {REASON[r.reason] ?? ''}</em>}</span>
              </li>
            ))}
            {d.authMode && (
              <li className="is-pending" data-slot="revoked-credential">
                <Chip tone="muted" icon="clock">not sent</Chip>
                <span><b>An event signed by a revoked credential</b><em> · this row is filled in when the identity lane is joined</em></span>
              </li>
            )}
          </ul>
        )}
      </section>

      <section>
        <h4><Icon name="key" size={16} /> Partner identity</h4>
        <div className="security__row"><Chip tone="muted" icon="wifioff">not connected</Chip><span className="muted small">Integration ready, partner service unavailable. A mock result is never shown as a real integration.</span></div>
      </section>
    </div>
  )
}
