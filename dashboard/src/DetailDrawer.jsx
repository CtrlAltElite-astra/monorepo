import { useState } from 'react'
import { addNote, getHistory } from './api.js'
import Badge from './components/Badge.jsx'
import ReceiptPanel from './components/ReceiptPanel.jsx'

const FIELDS = ['title', 'category', 'severity', 'status', 'assigned_team', 'people_estimate', 'location', 'description']

const show = (v) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v))

// Everything shown comes from the region's report view: state, live candidates, conflicts, notes, history.
export default function DetailDrawer({ region, report, onClose, onChange }) {
  const [text, setText] = useState('')
  const [msg, setMsg] = useState('')
  const [history, setHistory] = useState(null)
  const [histMsg, setHistMsg] = useState('')
  const conflicts = Object.fromEntries(report.conflicts.map((c) => [c.field, c]))

  const submit = async (e) => {
    e.preventDefault()
    try {
      await addNote(region, report.report_id, text)
      setText('')
      setMsg('')
      onChange()
    } catch (err) {
      setMsg(`failed: ${err.message}`)
    }
  }

  const loadHistory = async () => {
    try {
      setHistory(await getHistory(region, report.report_id))
      setHistMsg('')
    } catch (err) {
      setHistMsg(err.status === 404 ? 'History API not available on the backend yet.' : `failed: ${err.message}`)
    }
  }

  return (
    <aside className="drawer">
      <div className="row">
        <h2>{report.report_id} <span className="muted">via {region.id}</span></h2>
        <button onClick={onClose}>Close</button>
      </div>
      <p className="muted">
        replication: <Badge value={report.consistency.replication} />
        {report.consistency.stale_possible && <span className="chip warn">stale possible</span>}
        {report.incomplete && ' · incomplete (waiting for CREATE)'}
      </p>
      <table>
        <thead><tr><th>field</th><th>value shown</th><th>live events</th></tr></thead>
        <tbody>
          {FIELDS.map((f) => (
            <tr key={f} className={conflicts[f] ? 'conflict' : ''}>
              <td>{f}</td>
              <td>{show(report.state[f])}</td>
              <td><code>{(report.live[f] || []).join(', ') || '—'}</code></td>
            </tr>
          ))}
        </tbody>
      </table>

      {report.conflicts.length > 0 && <h3>Conflicts</h3>}
      {report.conflicts.map((c) => <ReceiptPanel key={c.field} conflict={c} />)}

      <h3>Notes</h3>
      <ul className="notes">
        {report.state.notes.map((n) => <li key={n.id}><code>{n.id}</code> {n.text}</li>)}
        {!report.state.notes.length && <li className="muted">no notes</li>}
      </ul>
      <form className="create" onSubmit={submit}>
        <input required maxLength={2000} placeholder="Add a note" value={text} onChange={(e) => setText(e.target.value)} />
        <button type="submit">Add note</button>
        <span className="muted">{msg}</span>
      </form>

      <h3>History</h3>
      {!history && <button onClick={loadHistory}>Load history</button>}
      <span className="muted"> {histMsg}</span>
      {history && (
        <table>
          <thead><tr><th>event</th><th>type</th><th>field</th><th>value</th><th>replaces</th><th>actor</th><th>auth</th></tr></thead>
          <tbody>
            {history.map((e) => (
              <tr key={e.event_id}>
                <td><code>{e.event_id}</code></td>
                <td>{e.type}</td>
                <td>{show(e.field)}</td>
                <td>{show(e.value)}</td>
                <td><code>{(e.supersedes || []).join(', ') || '—'}</code></td>
                <td>{e.actor}</td>
                <td>{e.auth?.mode ? <Badge value={e.auth.mode} /> : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </aside>
  )
}
