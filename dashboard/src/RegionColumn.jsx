import { patchField } from './api.js'
import Badge from './components/Badge.jsx'

const STATUSES = ['open', 'assigned', 'resolved', 'closed', 'cancelled']

function StatusCard({ region, status }) {
  return (
    <div className="card">
      <div className="row">
        <h2>{region.id.toUpperCase()}</h2>
        <span className={`badge level-${status.level}`}>{status.level}</span>
      </div>
      <div className="muted">{region.url}</div>
      <div className="kv">heads <code>{JSON.stringify(status.heads)}</code></div>
      <div className="kv">events <code>{status.event_count}</code> · quarantined <code>{status.quarantined}</code></div>
      <div className="kv">digest <code>{status.event_digest.slice(0, 10)}</code> state <code>{status.state_hash.slice(0, 10)}</code></div>
      {status.risk && (
        <div className="kv" title="data at risk: events not yet confirmed by every peer">
          at risk <code>{status.risk.unreplicated}</code> · critical <code>{status.risk.critical_unreplicated}</code>
          {status.risk.oldest_age_ms != null && <> · oldest <code>{Math.round(status.risk.oldest_age_ms / 1000)}s</code></>}
        </div>
      )}
      {status.queues && (
        <div className="kv" title="pending push per priority lane">
          queue P0 <code>{status.queues.P0}</code> P1 <code>{status.queues.P1}</code> P2 <code>{status.queues.P2}</code>
        </div>
      )}
      <div className="chips">
        {status.quarantined > 0 && (
          <span className="chip bad" title="events rejected for bad hash or reused id">quarantined: {status.quarantined}</span>
        )}
        {status.incomplete_reports > 0 && (
          <span className="chip warn" title="changes received before their CREATE">incomplete: {status.incomplete_reports}</span>
        )}
        {Object.entries(status.peers).map(([p, s]) => (
          <span key={p} className={`chip ${s.link === 'down' ? 'bad' : s.reachable ? 'good' : 'warn'}`}
                title={s.last_error || 'ok'}>
            {p}: link {s.link}{s.reachable ? '' : ' · unreachable'}
          </span>
        ))}
      </div>
    </div>
  )
}

function Row({ region, rep, onChange, onSelect, selected, peerDown }) {
  const s = rep.state
  const set = async (field, value) => {
    try { await patchField(region, rep.report_id, field, value) } finally { onChange() }
  }
  return (
    <tr className={`clickable ${rep.conflicts.length ? 'conflict' : ''} ${selected ? 'selected' : ''}`}
        onClick={() => onSelect(rep.report_id)}>
      <td><code>{rep.report_id}</code></td>
      <td>{s.title}</td>
      <td>
        <select value={s.severity ?? ''} onClick={(e) => e.stopPropagation()} onChange={(e) => set('severity', Number(e.target.value))}>
          {[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}
        </select>
      </td>
      <td>
        <select value={s.status ?? ''} onClick={(e) => e.stopPropagation()} onChange={(e) => set('status', e.target.value)}>
          {STATUSES.map((v) => <option key={v}>{v}</option>)}
        </select>
      </td>
      <td>
        {rep.conflicts.map((c) => (
          <div key={c.field + c.rule} className="chip warn" title={`${c.rule}: ${c.event_ids.join(', ')}`}>
            {c.field}: {c.values.join(' / ')}
          </div>
        ))}
      </td>
      <td>
        <Badge value={rep.consistency.replication} />
        {peerDown && <span className="chip warn" title="a peer is unreachable now; this may be stale">stale?</span>}
      </td>
    </tr>
  )
}

export default function RegionColumn({ region, snap, onChange, onSelect, selectedId }) {
  if (!snap) return <section className="col"><div className="card muted">loading {region.id}…</div></section>
  if (!snap.ok) {
    return (
      <section className="col">
        <div className="card down">
          <h2>{region.id.toUpperCase()}</h2>
          <strong>UNREACHABLE</strong>
          <div className="muted">{region.url} — {snap.error}</div>
        </div>
      </section>
    )
  }
  return (
    <section className="col">
      <StatusCard region={region} status={snap.status} />
      <table>
        <thead><tr><th>id</th><th>title</th><th>sev</th><th>status</th><th>conflicts</th><th>repl</th></tr></thead>
        <tbody>
          {snap.reports.map((r) => <Row key={r.report_id} region={region} rep={r} onChange={onChange}
            onSelect={(id) => onSelect(region.id, id)} selected={selectedId === r.report_id}
            peerDown={Object.values(snap.status.peers).some((p) => !p.reachable)} />)}
          {!snap.reports.length && <tr><td colSpan="6" className="muted">no reports</td></tr>}
        </tbody>
      </table>
    </section>
  )
}
