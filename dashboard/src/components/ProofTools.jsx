import { useState } from 'react'
import { nodeCall, nodeHas, nodeReport, nodeStatus, setPairLink, tamperRegion } from '../api.js'
import { proveConvergence } from '../sdk/prove.js'
import Badge from './Badge.jsx'

const short = (h) => (h ? String(h).slice(0, 10) : '-')

// ---- Prove it -----------------------------------------------------------------
// Real partitions and real writes on real nodes, then the claim is checked (see sdk/prove.js).
export function ProveIt({ regions, onDone }) {
  const [running, setRunning] = useState(false)
  const [lines, setLines] = useState([])
  const [result, setResult] = useState(null)

  const byId = Object.fromEntries(regions.map((r) => [r.id, r]))
  const env = {
    regions,
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
    now: () => Date.now(),
    rng: Math.random,
    log: (m) => setLines((l) => [...l, m]),
    write: (id, method, path, body) => nodeCall(byId[id], method, path, body),
    setPair: async (a, b, mode, extra) => { await setPairLink(byId[a], byId[b], mode, extra) },
    status: (id) => nodeStatus(byId[id]),
    has: (id, ids) => nodeHas(byId[id], ids),
    report: (id, rid) => nodeReport(byId[id], rid),
  }

  const run = async () => {
    setRunning(true); setLines([]); setResult(null)
    try {
      setResult(await proveConvergence(env))
    } catch (e) {
      setResult({ ok: false, reason: 'the proof script crashed: ' + e.message, nodes: {}, missing: {}, conflicts: [], log: [] })
    }
    setRunning(false)
    onDone && onDone()
  }

  return (
    <section className="panel">
      <h2>Prove it</h2>
      <p className="muted">
        Cuts random links, writes on different nodes at the same time, heals, then checks that every node
        holds identical events and state and that no acknowledged write is missing. Takes about 20 seconds.
      </p>
      <button disabled={running} onClick={run}>{running ? 'running…' : 'Run the proof'}</button>
      {lines.length > 0 && <pre className="log">{lines.join('\n')}</pre>}
      {result && (
        <div className={`verdict ${result.ok ? 'pass' : 'fail'}`}>
          <strong>{result.ok ? 'PASS' : 'FAIL'}</strong> · {result.reason}
          <div className="muted">
            {result.partitions} partitions · {result.writes} concurrent writes · {result.acknowledged} acknowledged
            {result.convergedMs != null && ` · converged ${(result.convergedMs / 1000).toFixed(1)} s after the last heal`}
            {result.quarantinedDelta ? ` · ${result.quarantinedDelta} quarantined` : ''}
          </div>
          {Object.keys(result.nodes || {}).length > 0 && (
            <table>
              <thead><tr><th>node</th><th>level</th><th>events</th><th>event digest</th><th>state hash</th><th>acknowledged writes missing</th></tr></thead>
              <tbody>
                {Object.entries(result.nodes).map(([id, n]) => (
                  <tr key={id}>
                    <td><b>{id}</b></td>
                    <td>{n ? <Badge value={n.level} /> : <span className="chip">UNKNOWN</span>}</td>
                    <td>{n ? n.event_count : '-'}</td>
                    <td><code>{n ? short(n.event_digest) : '-'}</code></td>
                    <td><code>{n ? short(n.state_hash) : '-'}</code></td>
                    <td>{Array.isArray(result.missing[id]) ? result.missing[id].length : '?'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {result.conflicts.length > 0 && (
            <div className="muted">
              Conflicts the policy settled on the test cart:{' '}
              {result.conflicts.map((c) => `${c.field} (${c.rule})`).join(', ')}
            </div>
          )}
        </div>
      )}
    </section>
  )
}

// ---- Tamper -------------------------------------------------------------------
const MODES = [
  { mode: 'edit', label: 'Edit an event', hint: 'a real event with its content changed: the hash no longer matches' },
  { mode: 'forge', label: 'Forge a signature', hint: 'a well-formed event signed with the wrong key' },
  { mode: 'reuse', label: 'Reuse an event id', hint: 'a different event under an id that already exists' },
]

export function TamperPanel({ regions, onDone }) {
  const [target, setTarget] = useState(regions[0].id)
  const [busy, setBusy] = useState('')
  const [res, setRes] = useState(null)
  const [err, setErr] = useState('')

  const go = async (mode) => {
    setBusy(mode); setErr(''); setRes(null)
    try { setRes(await tamperRegion(target, mode)) } catch (e) {
      setErr(e.body && e.body.detail ? e.body.detail : e.message)
    }
    setBusy('')
    onDone && onDone()
  }

  return (
    <section className="panel">
      <h2>Tamper</h2>
      <p className="muted">Injects one bad event into a node through the peer path. It must be quarantined and the node's state must not change.</p>
      <div className="row-gap">
        <label>Target&nbsp;
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            {regions.map((r) => <option key={r.id}>{r.id}</option>)}
          </select>
        </label>
        {MODES.map((m) => (
          <button key={m.mode} className="cut" disabled={!!busy} title={m.hint} onClick={() => go(m.mode)}>
            {busy === m.mode ? '…' : m.label}
          </button>
        ))}
      </div>
      {err && <div className="banner warn">{err}</div>}
      {res && (
        <div className={`verdict ${res.state_unchanged && res.rejected.length ? 'pass' : 'fail'}`}>
          <strong>{res.rejected.length ? 'Rejected' : 'NOT rejected'}</strong>
          {res.rejected.map((r) => <span key={r.event_id} className="chip bad">{r.event_id}: {r.reason}</span>)}
          <div className="muted">
            quarantined {res.quarantined_before} → <b>{res.quarantined_after}</b> ·
            events {res.event_count_before} → {res.event_count_after} ·
            state hash <code>{short(res.state_hash_before)}</code> → <code>{short(res.state_hash_after)}</code>
          </div>
          <div>{res.state_unchanged ? 'The node’s state did not change.' : 'The node’s state CHANGED: this is a bug.'}</div>
        </div>
      )}
    </section>
  )
}
