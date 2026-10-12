// /demo: the live console. Storefront on the left, the judge panel on the right.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { DemoProvider, useDemo, type Convergence } from '../state/demo'
import type { RegionId } from '../api/types'
import { Storefront } from '../components/Storefront'
import { Counters, HealBanner, NodeStrip, RequestStream, RiskMeter } from '../components/Judge'
import { CartsPanel } from '../components/CartsPanel'
import { NetworkPanel } from '../components/NetworkPanel'
import { ReviewPanel, useReviewEntries } from '../components/ReviewPanel'
import { OutboxPanel } from '../components/OutboxPanel'
import { SecurityPanel } from '../components/SecurityPanel'
import { MetricsPanel } from '../components/MetricsPanel'
import { ApiPanel } from '../components/ApiPanel'
import { ProveButton, ProvePanel } from '../components/ProvePanel'
import { HistoryDrawer } from '../components/HistoryDrawer'
import { Toasts } from '../components/Toasts'
import { Icon, Logo } from '../components/icons'
import { ThemeToggle } from '../components/Theme'
import { Chip, Tabs, type Tone } from '../components/ui'

const CONV: Record<Convergence, { tone: Tone; icon: 'check' | 'refresh' | 'wifioff' | 'alert'; text: string; hint: string }> = {
  CONVERGED: { tone: 'ok', icon: 'check', text: 'CONVERGED', hint: 'Every node is reachable and reports the same event digest, state hash and policy version.' },
  SYNCING: { tone: 'info', icon: 'refresh', text: 'SYNCING', hint: 'Nodes still hold different events. They are catching up.' },
  UNKNOWN: { tone: 'muted', icon: 'wifioff', text: 'UNKNOWN', hint: 'At least one node cannot be reached, so convergence cannot be claimed. Never shown as green.' },
  INCOMPATIBLE: { tone: 'bad', icon: 'alert', text: 'INCOMPATIBLE', hint: 'Nodes run different policy versions.' },
  DIVERGED: { tone: 'bad', icon: 'alert', text: 'DIVERGED', hint: 'Heads are equal but state hashes differ. This should never happen: it is a bug alarm.' },
}

export function ConvergenceBadge() {
  const { convergence } = useDemo()
  const c = CONV[convergence]
  return <Chip tone={c.tone} icon={c.icon} title={c.hint} className="conv" pulse={convergence === 'DIVERGED'}>{c.text}</Chip>
}

type TabId = 'carts' | 'network' | 'review' | 'outbox' | 'security' | 'metrics' | 'api' | 'prove'

function Console() {
  const d = useDemo()
  const [tab, setTab] = useState<TabId>('carts')
  const [history, setHistory] = useState<{ region: RegionId; id: string } | null>(null)
  const reviews = useReviewEntries().length
  const openFor = (id: string) => setHistory({ region: d.viewRegion ?? d.joined[0], id })

  return (
    <div className="demo">
      <header className="topbar">
        <Link to="/" className="brand" aria-label="SyncShield home"><Logo size={26} /><span>SyncShield</span></Link>
        <span className="topbar__title">Console</span>
        <Chip tone={d.mode === 'mock' ? 'accent' : 'ok'} icon={d.mode === 'mock' ? 'flask' : 'wifi'} title={d.mode === 'mock' ? 'Everything here runs against an in-browser simulator of three nodes. Start the real nodes and open /demo?mode=live to see the real thing.' : 'Connected to the real nodes'}>
          {d.mode === 'mock' ? 'DEMO DATA · simulator' : 'LIVE · real nodes'}
        </Chip>
        <ConvergenceBadge />
        <div className="topbar__spacer" />
        <label className="switch" title="Five simulated shoppers keep adding to their carts"><input type="checkbox" checked={d.autoShoppers} onChange={(e) => d.setAutoShoppers(e.target.checked)} /><i /><span>Auto-shoppers</span></label>
        <ProveButton />
        <Link to="/" className="btn btn--ghost btn--sm"><Icon name="arrow" size={14} className="flip" />Story</Link>
        <ThemeToggle />
      </header>
      <HealBanner />
      <main className="demo__main">
        <Storefront onOpenHistory={openFor} />
        <div className="judge">
          <NodeStrip />
          <Counters />
          <div className="judge__row"><RequestStream /><RiskMeter /></div>
          <section className="card judge__tabs">
            <Tabs value={tab} onChange={setTab} tabs={[
              { id: 'carts', label: 'Carts', icon: 'cart' }, { id: 'network', label: 'Network', icon: 'link' },
              { id: 'review', label: 'Review', icon: 'scale', badge: reviews }, { id: 'outbox', label: 'Outbox', icon: 'package', badge: d.sdkState.outbox.length },
              { id: 'security', label: 'Security', icon: 'shield' }, { id: 'metrics', label: 'Metrics', icon: 'chart' },
              { id: 'api', label: 'API', icon: 'code' }, { id: 'prove', label: 'Prove it', icon: 'flask' },
            ]} />
            <div className="tabpane" key={tab}>
              {tab === 'carts' && <CartsPanel onOpenHistory={(region, id) => setHistory({ region, id })} />}
              {tab === 'network' && <NetworkPanel />}
              {tab === 'review' && <ReviewPanel />}
              {tab === 'outbox' && <OutboxPanel />}
              {tab === 'security' && <SecurityPanel />}
              {tab === 'metrics' && <MetricsPanel />}
              {tab === 'api' && <ApiPanel />}
              {tab === 'prove' && <ProvePanel />}
            </div>
          </section>
        </div>
      </main>
      {history && <HistoryDrawer region={history.region} reportId={history.id} onClose={() => setHistory(null)} />}
      <Toasts />
    </div>
  )
}

export default function Demo() {
  const nodes = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('nodes') === '2' ? 2 : 3
  return <DemoProvider startNodes={nodes}><Console /></DemoProvider>
}
