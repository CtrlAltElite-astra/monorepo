import { useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from 'react'
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
import { ScalingPanel } from '../components/ScalingPanel'
import { HistoryDrawer } from '../components/HistoryDrawer'
import { Toasts } from '../components/Toasts'

const RESET_ALL_PROMPT = 'Reset everything?\n\nEvery region is stopped and its database deleted, then all are started again. All carts, events and counters go to zero.'

// The shop sidebar's width: drag the divider (double-click or Home to go back to the default). Remembered per browser.
const SIDEBAR_KEY = 'syncshield.sidebarWidth'
const SIDEBAR_MIN = 300
const sidebarMax = () => Math.max(SIDEBAR_MIN + 40, Math.min(760, Math.round(window.innerWidth * 0.6)))
const clampSidebar = (w: number) => Math.round(Math.max(SIDEBAR_MIN, Math.min(w, sidebarMax())))
function readSidebar(): number | null {
  try { const v = Number(window.localStorage.getItem(SIDEBAR_KEY)); return v >= SIDEBAR_MIN ? clampSidebar(v) : null } catch { return null }
}

type Tab = 'carts' | 'network' | 'review' | 'outbox' | 'security' | 'metrics' | 'scaling' | 'api' | 'prove'
function Workspace() {
  const d = useDemo()
  const initial = new URLSearchParams(window.location.search).get('panel')
  const [view, setView] = useState<'overview' | 'inspect' | 'proof'>(initial === 'prove' ? 'proof' : initial ? 'inspect' : 'overview')
  const [tab, setTab] = useState<Tab>(['carts', 'network', 'review', 'outbox', 'security', 'metrics', 'scaling', 'api', 'prove'].includes(initial || '') ? initial as Tab : 'carts')
  const [history, setHistory] = useState<{ region: RegionId; id: string } | null>(null)
  const scroll = useRef<HTMLElement>(null)
  const split = useRef<HTMLDivElement>(null)
  const [sidebar, setSidebar] = useState<number | null>(readSidebar)
  useEffect(() => {
    try { if (sidebar === null) window.localStorage.removeItem(SIDEBAR_KEY); else window.localStorage.setItem(SIDEBAR_KEY, String(sidebar)) } catch { /* private window: the width is just not remembered */ }
  }, [sidebar])
  const startDrag = (e: PointerEvent) => {
    e.preventDefault()
    const left = split.current?.getBoundingClientRect().left ?? 0
    const move = (ev: globalThis.PointerEvent) => setSidebar(clampSidebar(ev.clientX - left))
    const stop = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop); document.body.classList.remove('cx-resizing') }
    document.body.classList.add('cx-resizing')
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
  }
  const nudge = (e: KeyboardEvent) => {
    const current = sidebar ?? (split.current?.querySelector('.cx-left')?.getBoundingClientRect().width ?? 420)
    if (e.key === 'ArrowLeft') setSidebar(clampSidebar(current - 24))
    else if (e.key === 'ArrowRight') setSidebar(clampSidebar(current + 24))
    else if (e.key === 'Home') setSidebar(null)
    else return
    e.preventDefault()
  }
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
      <div className="cx-split" ref={split} style={sidebar ? ({ '--cx-left-w': `${sidebar}px` } as CSSProperties) : undefined}>
        <Shop onHistory={openHistory} onReview={() => inspect('review')} />
        <div className="cx-resizer" role="separator" aria-orientation="vertical" aria-label="Resize the shop sidebar (drag, or use the arrow keys; Home resets)" tabIndex={0}
          title="Drag to resize. Double-click to reset." onPointerDown={startDrag} onDoubleClick={() => setSidebar(null)} onKeyDown={nudge} />
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
              <button className="btn btn--sm cx-reset-all" disabled={d.mode === 'live' && !d.chaosAvailable} onClick={() => { if (window.confirm(RESET_ALL_PROMPT)) void d.resetAll() }}
                title="Wipe every region together and start from scratch"><Icon name="refresh" size={15} />Reset everything</button>
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
                {[['carts', 'Carts'], ['network', 'Network'], ['review', 'Review'], ['outbox', 'Outbox'], ['security', 'Security'], ['metrics', 'Metrics'], ['scaling', 'Scaling'], ['api', 'API']].map(([key, name]) => (
                  <button key={key} onClick={() => inspect(key as Tab)}>{name}{key === 'review' && reviews > 0 && <b>{reviews}</b>}<Icon name="chevron" size={14} /></button>
                ))}
              </nav>
            </div>
          )}
      {view === 'inspect' && <div className="inspector-content"><div className="inspector-intro"><h2>A closer look at the system.</h2><span>Live regional facts, without the guesswork.</span></div><section className="cx-board cx-board--flat"><RegionCards /></section><section className="inspector-panel"><Tabs value={tab} onChange={setTab} tabs={[
        { id: 'carts', label: 'Carts', icon: 'cart' }, { id: 'network', label: 'Network', icon: 'link' }, { id: 'review', label: 'Review', icon: 'scale', badge: reviews }, { id: 'outbox', label: 'Outbox', icon: 'package', badge: d.sdkState.outbox.length }, { id: 'security', label: 'Security', icon: 'shield' }, { id: 'metrics', label: 'Metrics', icon: 'chart' }, { id: 'scaling', label: 'Scaling', icon: 'gauge' }, { id: 'api', label: 'API', icon: 'code' }, { id: 'prove', label: 'Prove it', icon: 'flask' },
      ]} /><div className="tabpane" key={tab}>{tab === 'carts' && <CartsPanel onOpenHistory={(region, id) => setHistory({ region, id })} />}{tab === 'network' && <NetworkPanel />}{tab === 'review' && <ReviewPanel />}{tab === 'outbox' && <OutboxPanel />}{tab === 'security' && <SecurityPanel />}{tab === 'metrics' && <MetricsPanel />}{tab === 'scaling' && <ScalingPanel />}{tab === 'api' && <ApiPanel />}{tab === 'prove' && <ProvePanel />}</div></section></div>}
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
