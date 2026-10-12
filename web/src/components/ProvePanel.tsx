// U2: honest consistency, proven live. Scripted chaos in the browser, then identical digests on every node.
import { useDemo } from '../state/demo'
import { short } from '../lib/util'
import { Icon } from './icons'
import { Chip, NODE_NAME, NodeDot } from './ui'

export function ProveButton() {
  const d = useDemo()
  return <button className="btn btn--primary" disabled={d.proving} onClick={() => void d.proveIt()}><Icon name={d.proving ? 'refresh' : 'flask'} size={16} className={d.proving ? 'spin' : ''} />{d.proving ? 'Proving…' : 'Prove it'}</button>
}

export function ProvePanel() {
  const d = useDemo()
  const r = d.proveResult
  return (
    <div className="panel prove">
      <div className="prove__head">
        <div><h4>Cut, write on both sides, heal, compare</h4><p className="muted small">Cuts one node off, changes the same cart on both sides of the cut, heals everything, then compares what every node holds. Nothing here is a claim: the numbers come from each node's /v1/status.</p></div>
        <ProveButton />
      </div>
      {d.proveSteps.length > 0 && (
        <ol className="steps">
          {d.proveSteps.map((s, i) => (
            <li key={i} className={`step step--${s.state}`}><i>{s.state === 'done' ? <Icon name="check" size={12} /> : s.state === 'fail' ? <Icon name="x" size={12} /> : s.state === 'run' ? <Icon name="refresh" size={12} className="spin" /> : i + 1}</i>{s.text}</li>
          ))}
        </ol>
      )}
      {r && (
        <div className={`verdict verdict--${r.verdict === 'CONVERGED' ? 'ok' : 'bad'}`} role="status">
          <header><Icon name={r.verdict === 'CONVERGED' ? 'check' : 'alert'} size={20} /><strong>{r.verdict === 'CONVERGED' ? 'Identical on every node' : `Not converged: ${r.verdict}`}</strong>
            <span className="muted">cut {r.cuts.join(', ') || 'none'} · {r.writes} writes · {(r.ms / 1000).toFixed(1)} s</span></header>
          <table className="table table--mono">
            <thead><tr><th>Node</th><th>Events</th><th>Event digest</th><th>State hash</th><th>Policy</th></tr></thead>
            <tbody>{r.rows.map((x) => (
              <tr key={x.region}><td><NodeDot id={x.region} size={8} /> {NODE_NAME[x.region]}</td><td>{x.events}</td><td>{short(x.event_digest, 16)}</td><td>{short(x.state_hash, 16)}</td><td>v{x.policy_version}</td></tr>
            ))}</tbody>
          </table>
          {r.verdict === 'CONVERGED' && <Chip tone="ok" icon="check">AP with strong eventual consistency: same events in, same state out.</Chip>}
          {r.reviews > 0 && <Chip tone="warn" icon="scale">{r.reviews} {r.reviews === 1 ? 'decision is' : 'decisions are'} waiting in the Review tab: every node shows the same safe value until a supervisor decides.</Chip>}
        </div>
      )}
    </div>
  )
}
