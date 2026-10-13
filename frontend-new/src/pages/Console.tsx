import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { DemoProvider, useDemo } from '../state/demo'
import type { RegionId } from '../api/types'
import { Brand, Light, MotionToggle } from '../components/Visuals'
import { Icon } from '../components/icons'
import { Shop } from '../components/Shop'
import { SessionStats, Topology, RegionCards, LatestAction, EventTrail, FailureControls } from '../components/LiveSystem'
import { Chip, Tabs, NODE_NAME } from '../components/ui'
import { ConvergenceBadge } from './Demo'
import { RequestStream, RiskMeter, Counters, HealBanner } from '../components/Judge'
import { CartsPanel } from '../components/CartsPanel'
import { NetworkPanel } from '../components/NetworkPanel'
import { ReviewPanel, useReviewEntries } from '../components/ReviewPanel'
import { OutboxPanel } from '../components/OutboxPanel'
import { SecurityPanel } from '../components/SecurityPanel'
import { MetricsPanel } from '../components/MetricsPanel'
import { ApiPanel } from '../components/ApiPanel'
import { ProvePanel } from '../components/ProvePanel'
import { HistoryDrawer } from '../components/HistoryDrawer'
import { Toasts } from '../components/Toasts'

type Tab = 'carts' | 'network' | 'review' | 'outbox' | 'security' | 'metrics' | 'scaling' | 'api' | 'prove'
function Workspace() {
  const d = useDemo()
  const initial = new URLSearchParams(window.location.search).get('panel')
  const [view, setView] = useState<'overview' | 'inspect' | 'proof'>(initial === 'prove' ? 'proof' : initial ? 'inspect' : 'overview')
  const [tab, setTab] = useState<Tab>(['carts', 'network', 'review', 'outbox', 'security', 'metrics', 'scaling', 'api', 'prove'].includes(initial || '') ? initial as Tab : 'carts')
  const [history, setHistory] = useState<{ region: RegionId; id: string } | null>(null)
  const scroll = useRef<HTMLElement>(null)
  const reviews = useReviewEntries().length
  const closeHistory = useCallback(() => setHistory(null), [])
  useEffect(() => { document.title = 'SyncShield — Live commerce console'; window.scrollTo(0, 0) }, [])
  useEffect(() => { scroll.current?.scrollTo({ top: 0, behavior: 'smooth' }) }, [view])
  const inspect = (next: Tab) => { setTab(next); setView(next === 'prove' ? 'proof' : 'inspect') }
  const openHistory = (id: string) => setHistory({ region: d.viewRegion || 'mum', id })
  const online = d.joined.filter(r => d.snaps[r]?.ok).length
  const serving = d.route.region // where the selected shopper's next write goes
  return (
    <div className="cx-app">
      <header className="cx-top">
        <Brand />
        <nav aria-label="Console navigation">
          <button className={view === 'overview' ? 'active' : ''} onClick={() => setView('overview')}>Overview</button>
          <button className={view === 'inspect' ? 'active' : ''} onClick={() => setView('inspect')}>Inspect</button>
          <button className={view === 'proof' ? 'active' : ''} onClick={() => setView('proof')}>Evidence</button>
          <Link to="/#how-it-works">How it works<Icon name="arrow" size={15} /></Link>
        </nav>
        <div className="cx-top-right">
          <MotionToggle />
          <span className="cx-workspace"><Icon name="server" size={18} />{d.mode === 'live' ? 'Live workspace' : 'Simulator workspace'}</span>
          <span className="cx-avatar" title={`Shopping as ${d.shopper}`}>{d.shopper[0]}</span>
        </div>
      </header>
      <div className="cx-split">
        <Shop onHistory={openHistory} onReview={() => inspect('review')} />
        <main ref={scroll} className="cx-right">
          <div className="cx-head">
            <div>
              <span className="cx-eyebrow">Behind the scenes</span>
              <h1>Live backend<Light phase={online ? (online === d.joined.length ? 'healthy' : 'degraded') : 'down'} size={16} /></h1>
              <p>{serving ? `${d.shopper}’s writes go to ${NODE_NAME[serving]}` : 'Ready for a shopper action'}<span>·</span>{online} of {d.joined.length} regions reachable</p>
            </div>
            <div className="cx-head-right">
              <ConvergenceBadge />
              <label className="switch"><input type="checkbox" checked={d.autoShoppers} onChange={e => d.setAutoShoppers(e.target.checked)} /><i /><span>Auto-shoppers</span></label>
              {d.mode === 'live' && <label className="switch" title="Start Bengaluru by itself when the regions are over their capacity (low 0.2, medium 1, high 4 writes a second per region)"><input type="checkbox" checked={d.autoscale} onChange={e => d.setAutoscale(e.target.checked)} /><i /><span>Autoscale</span></label>}
            </div>
          </div>
          {d.mode === 'mock' && <div className="mode-note"><Icon name="flask" size={18} />Simulator mode. These are simulated regional responses. <a href="/demo">Switch to live backends</a></div>}
          <HealBanner />
          {view === 'overview' && (
            <div className="cx-overview">
              <SessionStats />
              <Topology inspect={() => inspect('carts')} />
              <LatestAction />
              <div className="cx-pair"><EventTrail /><FailureControls inspect={() => inspect('network')} /></div>
              <nav className="cx-dock" aria-label="Inspectors">
                <span><Icon name="layers" size={18} />Take a closer look</span>
                {[['carts', 'Carts'], ['network', 'Network'], ['review', 'Review'], ['outbox', 'Outbox'], ['security', 'Security'], ['metrics', 'Metrics'], ['api', 'API']].map(([key, name]) => (
                  <button key={key} onClick={() => inspect(key as Tab)}>{name}{key === 'review' && reviews > 0 && <b>{reviews}</b>}<Icon name="chevron" size={14} /></button>
                ))}
              </nav>
            </div>
          )}
      {view === 'inspect' && <div className="inspector-content"><div className="inspector-intro"><h2>A closer look at the system.</h2><span>Live regional facts, without the guesswork.</span></div><section className="cx-board cx-board--flat"><RegionCards /></section><section className="inspector-panel"><Tabs value={tab} onChange={setTab} tabs={[
        { id: 'carts', label: 'Carts', icon: 'cart' }, { id: 'network', label: 'Network', icon: 'link' }, { id: 'review', label: 'Review', icon: 'scale', badge: reviews }, { id: 'outbox', label: 'Outbox', icon: 'package', badge: d.sdkState.outbox.length }, { id: 'security', label: 'Security', icon: 'shield' }, { id: 'metrics', label: 'Metrics', icon: 'chart' }, { id: 'api', label: 'API', icon: 'code' }, { id: 'prove', label: 'Prove it', icon: 'flask' },
      ]} /><div className="tabpane" key={tab}>{tab === 'carts' && <CartsPanel onOpenHistory={(region, id) => setHistory({ region, id })} />}{tab === 'network' && <NetworkPanel />}{tab === 'review' && <ReviewPanel />}{tab === 'outbox' && <OutboxPanel />}{tab === 'security' && <SecurityPanel />}{tab === 'metrics' && <MetricsPanel />}{tab === 'api' && <ApiPanel />}{tab === 'prove' && <ProvePanel />}</div></section></div>}
      {view === 'proof' && <div className="evidence-content"><section className="evidence-intro"><span className="eyebrow">MEASURED, NOT ASSUMED</span><h2>Let the system show its work.</h2><p>Run a controlled partition, make concurrent changes, then heal and compare all three regions.</p><Chip tone="muted" icon="info">The proof creates a test cart and temporarily changes regional links.</Chip></section><Counters /><section className="inspector-panel"><ProvePanel /></section><div className="console-bottom-grid"><RequestStream /><RiskMeter /></div><p className="evidence-boundary"><Icon name="info" size={16} />Recovery depends on a surviving peer or this browser retaining the signed event. Closing the tab clears its in-memory recovery outbox.</p></div>}
          <footer className="cx-foot"><span><Icon name="shield" size={15} />SyncShield <i /> Every change. Everywhere.</span><span>{d.mode === 'live' ? 'Connected to local regional APIs' : 'Explicit simulator mode'}</span></footer>
        </main>
      </div>
      {history && <HistoryDrawer region={history.region} reportId={history.id} onClose={closeHistory} />}
      <Toasts />
    </div>
  )
}
export default function Console() { return <DemoProvider startNodes={3}><Workspace /></DemoProvider> }
