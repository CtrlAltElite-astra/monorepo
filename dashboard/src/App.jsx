import { useEffect, useState } from 'react'
import { REGIONS, FIXTURES, pollRegion, setPairLink } from './api.js'
import RegionColumn from './RegionColumn.jsx'
import CreateReportForm from './CreateReportForm.jsx'
import DetailDrawer from './DetailDrawer.jsx'
import LinkMatrix from './components/LinkMatrix.jsx'
import ReviewQueue from './components/ReviewQueue.jsx'
import MetricsPanel from './components/MetricsPanel.jsx'
import ShopConsole from './ShopConsole.jsx'

// CONVERGED only when every region is reachable and all report the same digests and policy version.
// Any unreachable region gives UNKNOWN, never green.
function convergence(snaps) {
  if (!snaps.length || snaps.some((s) => !s || !s.ok)) return 'UNKNOWN'
  const same = (k) => new Set(snaps.map((s) => s.status[k])).size === 1
  if (!same('policy_version')) return 'INCOMPATIBLE'
  return same('event_digest') && same('state_hash') ? 'CONVERGED' : 'DIVERGED'
}

export default function App() {
  const [snaps, setSnaps] = useState([])
  const [busy, setBusy] = useState(false)
  const [sel, setSel] = useState(null) // {regionId, reportId}
  const [updated, setUpdated] = useState(null)
  const [tab, setTab] = useState('shop') // 'shop' = failover console, 'operator' = per-region operator view

  const refresh = async () => {
    setSnaps(await Promise.all(REGIONS.map(pollRegion)))
    setUpdated(new Date())
  }
  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 1000)
    return () => clearInterval(t)
  }, [])

  const bySnap = (id) => snaps.find((s) => s.id === id)
  const linkMode = (a, b) => bySnap(a)?.status?.peers?.[b]?.link
  const pairs = REGIONS.flatMap((a, i) => REGIONS.slice(i + 1).map((b) => [a, b]))
  const isDown = (a, b) => linkMode(a.id, b.id) === 'down' || linkMode(b.id, a.id) === 'down'

  const setPair = async (a, b, mode) => {
    setBusy(true)
    await setPairLink(a, b, mode)
    await refresh()
    setBusy(false)
  }
  const toggle = (a, b) => setPair(a, b, isDown(a, b) ? 'up' : 'down')
  const toggleFromMatrix = (a, b, current) => setPair(a, b, current === 'down' ? 'up' : 'down')

  const conv = convergence(snaps)
  const unreachable = snaps.filter((s) => !s.ok).map((s) => s.id)
  const levels = snaps.filter((s) => s.ok).map((s) => `${s.id}: ${s.status.level}`)
  const selRegion = REGIONS.find((r) => r.id === sel?.regionId)
  const selReport = bySnap(sel?.regionId)?.reports?.find((r) => r.report_id === sel?.reportId)

  return (
    <div className="app">
      <header>
        <h1>SyncShield</h1>
        <span className={`badge conv-${conv}`}>{conv}</span>
        <nav className="tabs">
          <button className={tab === 'shop' ? 'active' : ''} onClick={() => setTab('shop')}>Shop and failover</button>
          <button className={tab === 'operator' ? 'active' : ''} onClick={() => setTab('operator')}>Operator</button>
        </nav>
        <div className="spacer" />
        {tab === 'operator' && pairs.map(([a, b]) => (
          <button key={a.id + b.id} disabled={busy} className={isDown(a, b) ? 'heal' : 'cut'} onClick={() => toggle(a, b)}>
            {isDown(a, b) ? `Heal ${a.id}↔${b.id}` : `Cut ${a.id}↔${b.id}`}
          </button>
        ))}
      </header>
      {FIXTURES && <div className="banner warn">Fixture mode: showing contracts/fixtures and mock data, no backend, writes disabled.</div>}
      {unreachable.length > 0 && (
        <div className="banner bad">Unreachable: {unreachable.join(', ')}. Values for these regions are unknown, not zero.</div>
      )}
      <div className="muted status-line">
        {levels.join(' · ')}{updated && ` · updated ${updated.toLocaleTimeString()}`}
      </div>
      {tab === 'shop' && <ShopConsole snaps={snaps} onRefresh={refresh} />}
      {tab === 'operator' && <CreateReportForm regions={REGIONS} onDone={refresh} />}
      {tab === 'operator' && <main style={{ gridTemplateColumns: `repeat(${REGIONS.length}, minmax(0, 1fr))` }}>
        {REGIONS.map((r) => (
          <RegionColumn key={r.id} region={r} snap={bySnap(r.id)} onChange={refresh}
            onSelect={(regionId, reportId) => setSel({ regionId, reportId })}
            selectedId={sel?.regionId === r.id ? sel.reportId : null} />
        ))}
      </main>}
      {tab === 'operator' && selReport && (
        <DetailDrawer region={selRegion} report={selReport} onClose={() => setSel(null)} onChange={refresh} />
      )}
      {tab === 'operator' && <ReviewQueue snaps={snaps} regions={REGIONS} onChange={refresh} />}
      {tab === 'operator' && <LinkMatrix regions={REGIONS} snaps={snaps} busy={busy} onToggle={toggleFromMatrix} />}
      {tab === 'operator' && <MetricsPanel snaps={snaps} />}
    </div>
  )
}
