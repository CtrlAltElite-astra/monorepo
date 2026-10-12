const show = (v) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v))

// Accepts both shapes: v0 {values, event_ids} and v1 receipts {candidates, relationship, provisional, policy_version}.
export function normalize(c) {
  const candidates = c.candidates || (c.event_ids || []).map((id, i) => ({ event_id: id, value: (c.values || [])[i] }))
  return {
    field: c.field,
    candidates,
    rule: c.rule,
    policy: c.policy_version ?? 0,
    relationship: c.relationship || 'CONCURRENT',
    provisional: c.provisional,
    needsReview: !!c.needs_review,
  }
}

export default function ReceiptPanel({ conflict }) {
  const r = normalize(conflict)
  return (
    <div className="receipt">
      <div>
        <strong>{r.field}</strong> · {r.relationship} · rule <code>{r.rule}</code> · policy v{r.policy}
        {r.needsReview && <span className="chip warn">needs review</span>}
      </div>
      <table>
        <thead><tr><th>event</th><th>value</th><th>origin</th><th>actor</th><th>wall</th></tr></thead>
        <tbody>
          {r.candidates.map((c) => (
            <tr key={c.event_id} className={c.value === r.provisional ? 'selected' : ''}>
              <td><code>{c.event_id}</code></td>
              <td>{show(c.value)}</td>
              <td>{show(c.origin)}</td>
              <td>{show(c.actor)}</td>
              <td>{show(c.wall)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {r.provisional !== undefined && (
        <div className="muted">value shown on every region until resolved: <strong>{show(r.provisional)}</strong></div>
      )}
    </div>
  )
}
