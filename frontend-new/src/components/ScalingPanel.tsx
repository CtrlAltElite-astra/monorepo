// The Scaling tab: what more power and more regions bought, measured.
// The two result cards and the bars are filled ONLY from public/scale-result.json (written by scripts/bench_scale.py). With no file they
// say "not measured yet"; a mode the benchmark could not run says "unsupported". The runs at the bottom are made in this browser with the
// Burst and Spread buttons (Network tab): real writes, counted and timed here.
import { useEffect, useState } from 'react'
import { useDemo, type LoadRun } from '../state/demo'
import { compareSentence, parseScaleResult, type ScaleCell, type ScaleResult } from '../lib/scale'
import { fmtMs } from '../lib/util'
import { Icon } from './icons'
import { Chip, Empty, NODE_NAME } from './ui'

type FileState = { phase: 'loading' } | { phase: 'missing' } | { phase: 'ready'; result: ScaleResult }

function useScaleResult(): FileState {
  const [state, setState] = useState<FileState>({ phase: 'loading' })
  useEffect(() => {
    let stop = false
    // the dev server answers a missing file with the HTML shell (200), so the content type has to be JSON as well
    fetch('/scale-result.json', { cache: 'no-store' })
      .then(async (r) => {
        const ct = r.headers.get('content-type') ?? ''
        const parsed = r.ok && ct.includes('json') ? parseScaleResult(await r.json()) : null
        if (!stop) setState(parsed ? { phase: 'ready', result: parsed } : { phase: 'missing' })
      })
      .catch(() => { if (!stop) setState({ phase: 'missing' }) })
    return () => { stop = true }
  }, [])
  return state
}

const COMMAND = 'python scripts/bench_scale.py --vertical --horizontal --compare'

function ResultCard({ title, subtitle, cell, file }: { title: string; subtitle: string; cell: ScaleCell | null; file: FileState }) {
  const body = file.phase === 'loading' ? <p className="muted">Looking for the result file…</p>
    : file.phase === 'missing' || cell === null ? <><strong className="scale-none">Not measured yet</strong><p className="muted small">Run <code>{COMMAND}</code> on the demo machine; it writes <code>scale-result.json</code> and this card fills in.</p></>
    : cell === 'unsupported' ? <><strong className="scale-none">Unsupported</strong><p className="muted small">The backend that was measured could not run this mode (it needs the power setting), so there is no number and none is made up.</p></>
    : <><strong className="scale-big" key={cell.writes_per_s}>{Math.round(cell.writes_per_s)}<small> writes a second</small></strong>
        <p className="muted small">{cell.p50_ms !== null && <>p50 {fmtMs(cell.p50_ms)} · </>}{cell.p95_ms !== null ? <>p95 {fmtMs(cell.p95_ms)}</> : 'p95 not reported'}</p></>
  return (
    <article className={`scale-card ${cell && cell !== 'unsupported' ? 'is-measured' : ''}`}>
      <header><h4>{title}</h4><span className="muted">{subtitle}</span></header>
      {body}
    </article>
  )
}

function Bars({ rows, unit }: { rows: { label: string; cell: ScaleCell }[]; unit: string }) {
  const top = Math.max(1, ...rows.map((r) => (r.cell === 'unsupported' ? 0 : r.cell.writes_per_s)))
  return (
    <ul className="scale-bars">
      {rows.map((r) => (
        <li key={r.label}>
          <span>{r.label}</span>
          <div className="scale-track">{r.cell !== 'unsupported' && <i style={{ width: `${(r.cell.writes_per_s / top) * 100}%` }} />}</div>
          <b>{r.cell === 'unsupported' ? 'unsupported' : <>{Math.round(r.cell.writes_per_s)}<small> {unit}</small></>}</b>
        </li>
      ))}
    </ul>
  )
}

function Runs({ runs, simulated }: { runs: LoadRun[]; simulated: boolean }) {
  if (!runs.length) return <Empty icon="activity" title="No runs yet">Open the Network tab and press Burst 1000, or Spread 1000 to divide the same writes over every region. Each run lands here, so you can compare power settings and numbers of regions.</Empty>
  return (
    <div className="scale-runs">
      <table className="table">
        <thead><tr><th>Regions</th><th>Power</th><th>Writes saved</th><th>Time</th><th>A second</th><th>Queue peak</th><th>All agreed after</th></tr></thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id}>
              <td>{r.targets.length > 1 ? `${r.targets.length} regions` : NODE_NAME[r.targets[0]]}<small className="muted"> · {r.regions} in the mesh</small></td>
              <td>{r.power}</td>
              <td>{r.ok} of {r.n}{r.refused + r.overloaded + r.failed > 0 && <small className="muted"> · {r.refused + r.overloaded + r.failed} not saved</small>}</td>
              <td>{fmtMs(r.ms)}</td>
              <td><b>{r.perS}</b></td>
              <td>{r.peakQueue ?? '—'}</td>
              <td>{r.settledMs !== null ? fmtMs(r.settledMs) : 'not yet'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {simulated && <p className="muted small scale-note"><Icon name="flask" size={16} /><span>These runs are against the simulator: its commit costs are modelled, so the shape is the point (more power helps a lot and then less; more regions share the load), not the exact numbers.</span></p>}
    </div>
  )
}

export function ScalingPanel() {
  const d = useDemo()
  const file = useScaleResult()
  const r = file.phase === 'ready' ? file.result : null
  const sentence = r ? compareSentence(r) : null
  return (
    <div className="panel scaling">
      <section>
        <header className="panel__head"><h4><Icon name="gauge" size={16} /> One strong region, or three ordinary ones</h4>
          {r?.measuredAt && <Chip tone="muted" icon="clock">measured {new Date(r.measuredAt).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}</Chip>}</header>
        <div className="scale-cards">
          <ResultCard title="One region, high power" subtitle="32 writes saved per batch" cell={r ? r.oneHigh : null} file={file} />
          <ResultCard title="Three regions, low power" subtitle="1 write saved per batch, each" cell={r ? r.threeLow : null} file={file} />
        </div>
        {sentence && <p className="scale-verdict">{sentence}</p>}
        <p className="muted small">
          {r ? <>{r.writes !== null ? `${r.writes} writes per run` : 'Writes per run not reported'}{r.machine ? ` on ${r.machine}` : ''}. </> : null}
          Every region runs on the same machine and shares its CPU, and power is a batch size, not hardware. So this compares settings; a number is shown only when a run produced it, whichever way it falls.
        </p>
      </section>

      <section>
        <header className="panel__head"><h4><Icon name="chart" size={16} /> Vertical: more power in one region</h4></header>
        {r && r.vertical.length ? <Bars unit="a second" rows={r.vertical.map((v) => ({ label: `${v.power[0].toUpperCase()}${v.power.slice(1)} power`, cell: v.cell }))} />
          : <p className="muted">{file.phase === 'loading' ? 'Looking for the result file…' : 'Not measured yet.'}</p>}
        <p className="muted small">Writes saved per batch: low 1, medium 8, high 32. A bigger batch costs one commit for many writes, so throughput climbs steeply at first and then flattens.</p>
      </section>

      <section>
        <header className="panel__head"><h4><Icon name="layers" size={16} /> Horizontal: more regions sharing the load</h4></header>
        {r && r.horizontal.length ? <Bars unit="a second" rows={r.horizontal.map((h) => ({ label: `${h.regions} region${h.regions === 1 ? '' : 's'}`, cell: h.cell }))} />
          : <p className="muted">{file.phase === 'loading' ? 'Looking for the result file…' : 'Not measured yet.'}</p>}
        <p className="muted small">The same writes, divided over 1, 2 and 3 regions at low power. Each region commits on its own, so the work divides; replication to the others still has to happen.</p>
      </section>

      <section>
        <header className="panel__head"><h4><Icon name="activity" size={16} /> Your runs in this browser</h4>
          {d.mode === 'mock' && <Chip tone="accent" icon="flask">simulator</Chip>}</header>
        <Runs runs={d.loadRuns} simulated={d.mode === 'mock'} />
      </section>
    </div>
  )
}
