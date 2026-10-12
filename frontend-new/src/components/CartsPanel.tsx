// Cart cards per node: GET /v1/reports from every node, side by side, so replication is visible.
import { useDemo } from '../state/demo'
import type { RegionId, ReportView } from '../api/types'
import { SPEEDS, STATUS_LABEL, cartCode, cartCount, cartItems, cartTotal } from '../domain/shop'
import { plural, rupees } from '../lib/util'
import { Icon } from './icons'
import { Chip, Empty, LevelChip, NODE_NAME, NodeDot, ReplBadge } from './ui'

function Row({ rep, onOpen }: { rep: ReportView; onOpen: () => void }) {
  const items = cartItems(rep)
  const st = STATUS_LABEL[rep.state.status ?? 'open']
  return (
    <button className="cartrow" onClick={onOpen} title="Open this cart's history">
      <span className="cartrow__main"><strong>{rep.state.title}</strong> <code className="muted">{cartCode(rep.report_id)}</code></span>
      <span className="cartrow__sub">{plural(cartCount(items), 'item')} · {rupees(cartTotal(items))} · {SPEEDS[rep.state.severity ?? 1]}{rep.state.assigned_team ? ` · ${rep.state.assigned_team}` : ''}</span>
      <span className="cartrow__chips">
        <Chip tone={st?.tone ?? 'muted'}>{st?.label ?? rep.state.status}</Chip>
        {rep.conflicts.some((c) => c.needs_review) && <Chip tone="warn" icon="scale">review</Chip>}
        {rep.incomplete && <Chip tone="muted" icon="clock">incomplete</Chip>}
        <ReplBadge value={rep.consistency.replication} stale={rep.consistency.stale_possible} compact />
      </span>
    </button>
  )
}

export function CartsPanel({ onOpenHistory }: { onOpenHistory: (region: RegionId, id: string) => void }) {
  const d = useDemo()
  const all = [...new Set(d.joined.flatMap((r) => (d.snaps[r]?.reports ?? []).map((x) => x.report_id)))]
  return (
    <div className="panel carts">
      {d.joined.map((r) => {
        const snap = d.snaps[r]
        const ok = snap?.ok
        const here = new Map((snap?.reports ?? []).map((x) => [x.report_id, x]))
        return (
          <section key={r} className="carts__col">
            <header><NodeDot id={r} size={10} /><h4>{NODE_NAME[r]}</h4><LevelChip level={snap?.status?.level ?? null} unknown={!ok} /><span className="muted">{ok ? `${here.size} carts` : 'UNKNOWN'}</span></header>
            {!ok ? <div className="carts__unknown"><Icon name="wifioff" size={20} /><span>Cannot be reached. What this node holds is unknown.</span></div> : (
              <div className="carts__list">
                {all.map((id) => here.get(id)
                  ? <Row key={id} rep={here.get(id)!} onOpen={() => onOpenHistory(r, id)} />
                  : <div key={id} className="cartrow cartrow--ghost"><span className="muted">{cartCode(id)} not here yet</span><Icon name="clock" size={14} /></div>)}
                {!all.length && <Empty icon="cart" title="No carts yet">Add something in the storefront.</Empty>}
              </div>
            )}
          </section>
        )
      })}
    </div>
  )
}
