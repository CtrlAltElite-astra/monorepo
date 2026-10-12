import Badge from './Badge.jsx'

// Partition lab: row = region reporting, column = peer. Each cell is that row's own view of its link.
// A pair is "half cut" when only one side is down. The backend blocks both directions anyway; shown for honesty.
export default function LinkMatrix({ regions, snaps, busy, onToggle }) {
  const snap = (id) => snaps.find((s) => s.id === id)
  const mode = (a, b) => snap(a)?.status?.peers?.[b]?.link ?? null
  return (
    <section className="panel">
      <h2>Partition lab</h2>
      <table className="matrix">
        <thead><tr><th>from \ to</th>{regions.map((r) => <th key={r.id}>{r.id}</th>)}</tr></thead>
        <tbody>
          {regions.map((a) => (
            <tr key={a.id}>
              <th>{a.id}</th>
              {regions.map((b) => {
                if (a.id === b.id) return <td key={b.id} className="muted">—</td>
                if (!snap(a.id)?.ok) return <td key={b.id} className="muted">unknown</td>
                const m = mode(a.id, b.id)
                if (m === null) return <td key={b.id} className="muted" title="not a configured peer">not peered</td>
                const back = mode(b.id, a.id)
                const half = back !== null && back !== m
                return (
                  <td key={b.id}>
                    <button disabled={busy} className={m === 'down' ? 'heal' : 'cut'} onClick={() => onToggle(a, b, m)}>
                      {m === 'down' ? 'heal' : 'cut'}
                    </button>{' '}
                    <Badge value={m} title={m === 'slow' ? 'throttled link: delay and rate limit applied' : undefined} />
                    {half && <span className="chip warn" title="the other side disagrees">half cut</span>}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
