import { useState } from 'react'
import { resolveField } from '../api.js'
import ReceiptPanel, { normalize } from './ReceiptPanel.jsx'

const keyOf = (it) => `${it.report_id}|${it.field}|${normalize(it.receipt).candidates.map((c) => c.event_id).sort().join(',')}`

// Items are merged across regions (after convergence every region holds the same ones).
function collect(snaps) {
  const map = new Map()
  for (const s of snaps) {
    if (!s.ok || !Array.isArray(s.review)) continue
    for (const it of s.review) {
      const k = keyOf(it)
      if (!map.has(k)) map.set(k, { ...it, regions: [s.id] })
      else map.get(k).regions.push(s.id)
    }
  }
  return [...map.values()]
}

function Item({ item, regions, onChange }) {
  const r = normalize(item.receipt)
  const [value, setValue] = useState(null)
  const [reason, setReason] = useState('')
  const [msg, setMsg] = useState('')
  const target = regions.find((x) => x.id === item.regions[0])

  const submit = async (e) => {
    e.preventDefault()
    try {
      await resolveField(target, item.report_id, {
        field: r.field, value, candidates: r.candidates.map((c) => c.event_id), reason,
      })
      setMsg('resolved')
    } catch (err) {
      if (err.status === 409) setMsg('Changed meanwhile, queue refreshed. Review again.')
      else if (err.status === 403) setMsg('Supervisor role required.')
      else setMsg(`failed: ${err.message}`)
    } finally {
      onChange()
    }
  }

  return (
    <div className="card">
      <div className="row">
        <strong>{item.report_id}</strong>
        <span className="muted">{item.title} · seen in {item.regions.join(', ')}</span>
      </div>
      <ReceiptPanel conflict={item.receipt} />
      <form className="create" onSubmit={submit}>
        {r.candidates.map((c) => (
          <label key={c.event_id}>
            <input type="radio" name={keyOf(item)} checked={value === c.value} onChange={() => setValue(c.value)} />
            {String(c.value)} <span className="muted">({c.origin || c.event_id})</span>
          </label>
        ))}
        <input required placeholder="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button type="submit" disabled={value === null}>Resolve in {target.id}</button>
        <span className="muted">{msg}</span>
      </form>
    </div>
  )
}

export default function ReviewQueue({ snaps, regions, onChange }) {
  const items = collect(snaps)
  const supported = snaps.some((s) => s.ok && Array.isArray(s.review))
  return (
    <section className="panel">
      <h2>Review queue <span className="chip">{supported ? items.length : 'n/a'}</span></h2>
      {!supported && <div className="muted">Review API not available on this backend (older build, or no session in auth mode).</div>}
      {supported && !items.length && <div className="muted">Nothing needs a supervisor.</div>}
      {items.map((it) => <Item key={keyOf(it)} item={it} regions={regions} onChange={onChange} />)}
    </section>
  )
}
