// Renders whatever GET /v1/metrics returns as a flat key/value list per region (shape not frozen yet).
function flatten(obj, prefix = '') {
  return Object.entries(obj || {}).flatMap(([k, v]) =>
    v !== null && typeof v === 'object' ? flatten(v, `${prefix}${k}.`) : [[`${prefix}${k}`, v]],
  )
}

export default function MetricsPanel({ snaps }) {
  const withMetrics = snaps.filter((s) => s.ok && s.metrics)
  return (
    <section className="panel">
      <h2>Metrics</h2>
      {!withMetrics.length && <div className="muted">Metrics API not available on the backend yet (Phase 3).</div>}
      <div className="metrics">
        {withMetrics.map((s) => (
          <div key={s.id} className="card">
            <h3>{s.id}</h3>
            <table><tbody>
              {flatten(s.metrics).map(([k, v]) => <tr key={k}><td>{k}</td><td><code>{String(v)}</code></td></tr>)}
            </tbody></table>
          </div>
        ))}
      </div>
    </section>
  )
}
