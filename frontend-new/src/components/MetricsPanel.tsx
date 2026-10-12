// Tier 2: live metrics from GET /v1/metrics, plus the fuzz result card (the REAL run count, never hand-written).
import { useEffect, useState } from 'react'
import { useDemo } from '../state/demo'
import type { RegionId } from '../api/types'
import { fmtMs } from '../lib/util'
import { Icon } from './icons'
import { Chip, NODE_NAME, NodeDot } from './ui'

interface Metrics { writes: number; commit_ms: { p50: number | null; p95: number | null }; duplicates: number; lag_s: number; catch_up_s: number | null; peers: Record<string, { bytes_sent: number; bytes_received: number }> }
interface Fuzz { examples?: number; runs?: number; passed?: boolean; divergences?: number; at?: string }

export function MetricsPanel() {
  const d = useDemo()
  const [m, setM] = useState<Partial<Record<RegionId, Metrics | 'missing'>>>({})
  const [fuzz, setFuzz] = useState<Fuzz | null | 'missing'>(null)

  useEffect(() => {
    let stop = false
    const load = async () => {
      for (const r of d.joined) {
        try {
          const res = await d.api.transport.request<Metrics>(r, 'GET', '/v1/metrics', { timeoutMs: 1500 })
          if (!stop) setM((x) => ({ ...x, [r]: res.status === 200 ? res.body : 'missing' }))
        } catch { if (!stop) setM((x) => ({ ...x, [r]: 'missing' })) }
      }
    }
    void load()
    const t = setInterval(load, 2000)
    return () => { stop = true; clearInterval(t) }
  }, [d.api, d.joined])

  useEffect(() => {
    fetch('/fuzz-result.json').then(async (r) => { const ct = r.headers.get('content-type') ?? ''; setFuzz(r.ok && ct.includes('json') ? await r.json() : 'missing') }).catch(() => setFuzz('missing'))
  }, [])

  const runs = fuzz && fuzz !== 'missing' ? fuzz.examples ?? fuzz.runs : undefined
  return (
    <div className="panel metrics">
      <section>
        <h4><Icon name="chart" size={16} /> Per node</h4>
        <div className="metrics__grid">
          {d.joined.map((r) => {
            const x = m[r]
            return (
              <div key={r} className="metric card">
                <header><NodeDot id={r} size={9} /><b>{NODE_NAME[r]}</b></header>
                {!x ? <span className="muted">loading…</span> : x === 'missing' ? <span className="muted">Metrics are unavailable from this region.</span> : (
                  <dl>
                    <div><dt>Writes</dt><dd>{x.writes}</dd></div>
                    <div><dt>Commit p50 / p95</dt><dd>{fmtMs(x.commit_ms.p50)} / {fmtMs(x.commit_ms.p95)}</dd></div>
                    <div><dt>Duplicates ignored</dt><dd>{x.duplicates}</dd></div>
                    <div><dt>Sync lag</dt><dd>{x.lag_s ? `${x.lag_s.toFixed(1)} s` : '0 s'}</dd></div>
                    <div><dt>Last catch-up</dt><dd>{x.catch_up_s === null ? '—' : `${x.catch_up_s.toFixed(1)} s`}</dd></div>
                    {Object.entries(x.peers).map(([p, v]) => <div key={p}><dt>→ {p}</dt><dd>{v.bytes_sent} B out · {v.bytes_received} B in</dd></div>)}
                  </dl>
                )}
              </div>
            )
          })}
        </div>
      </section>
      <section>
        <h4><Icon name="flask" size={16} /> Randomized convergence test (F1)</h4>
        {fuzz === 'missing' || fuzz === null ? (
          <p className="muted">No recorded randomized test result is available. Open Evidence to run a live convergence proof against the connected regions.</p>
        ) : (
          <div className="fuzz"><Chip tone={fuzz.passed === false ? 'bad' : 'ok'} icon={fuzz.passed === false ? 'alert' : 'check'}>{fuzz.passed === false ? 'divergence found' : 'no divergence'}</Chip><strong>{runs ?? '?'} runs executed</strong>{fuzz.at && <span className="muted">{fuzz.at}</span>}</div>
        )}
      </section>
    </div>
  )
}
