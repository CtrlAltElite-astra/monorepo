// The audit trail of one cart: every event in order, with what it replaced, who wrote it, and how it was signed.
import { useEffect, useState } from 'react'
import { useDemo } from '../state/demo'
import type { EventEnvelope, RegionId } from '../api/types'
import { FIELD_LABEL, cartCode, describeValue, parseNote } from '../domain/shop'
import { fmtClock, short } from '../lib/util'
import { Icon } from './icons'
import { Chip, NODE_NAME, NodeDot } from './ui'

const PRIORITY = ['critical', 'normal', 'bulk']

function summary(e: EventEnvelope): string {
  if (e.type === 'CREATE') return `Cart created for ${e.value?.title ?? '?'}`
  if (e.type === 'NOTE') { const n = typeof e.value?.text === 'string' ? parseNote(e.value.text) : null; return n ? `Added ${n.qty}× ${n.name}` : String(e.value?.text ?? '') }
  return `${FIELD_LABEL[e.field ?? ''] ?? e.field} → ${describeValue(e.field ?? '', e.value)}`
}

export function HistoryDrawer({ region, reportId, onClose }: { region: RegionId; reportId: string; onClose: () => void }) {
  const d = useDemo()
  const [events, setEvents] = useState<EventEnvelope[] | null | undefined>(undefined)
  useEffect(() => {
    let live = true
    const load = async () => { const h = await d.history(region, reportId); if (live) setEvents(h) }
    void load()
    const t = setInterval(load, 2000)
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => { live = false; clearInterval(t); window.removeEventListener('keydown', k) }
  }, [d, region, reportId, onClose])

  return (
    <div className="drawer-wrap" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-label="History">
        <header className="drawer__head">
          <div><h3><Icon name="history" size={18} /> History <code className="muted">{cartCode(reportId)}</code></h3><p className="muted">As held by <NodeDot id={region} size={8} /> {NODE_NAME[region]}</p></div>
          <button className="btn btn--ghost btn--sm" onClick={onClose} aria-label="Close history"><Icon name="x" size={16} /></button>
        </header>
        {events === undefined && <p className="muted">Loading…</p>}
        {events === null && <p className="notice notice--warn"><Icon name="alert" size={16} />This node did not return the history. It may not support it yet, or it is unreachable.</p>}
        {events && (
          <ol className="timeline">
            {events.map((e) => (
              <li key={e.event_id} style={{ ['--c' as string]: `var(--node-${e.origin === 'mum' ? 'mum' : e.origin === 'pun' ? 'pun' : 'del'})` }}>
                <div className="timeline__top"><code>{e.event_id}</code><strong>{summary(e)}</strong><span className="muted">{fmtClock(e.wall)}</span></div>
                <div className="timeline__chips">
                  <Chip tone="muted" icon="user">{e.actor}{e.role ? ` · ${e.role}` : ''}</Chip>
                  <Chip tone={e.priority === 0 ? 'bad' : e.priority === 2 ? 'muted' : 'info'}>P{e.priority} {PRIORITY[e.priority]}</Chip>
                  {e.auth_mode && <Chip tone={e.auth_mode === 'online' ? 'ok' : 'warn'} icon="key">{e.auth_mode}</Chip>}
                  <Chip tone={e.sig_status === 'verified' ? 'ok' : e.sig_status === 'bad' ? 'bad' : 'warn'} icon={e.sig_status === 'bad' ? 'alert' : 'lock'}>{e.sig_status ?? (e.sig ? 'signed' : 'unsigned')}</Chip>
                  {e.carried && <Chip tone="info" icon="package">carried by a browser</Chip>}
                  {e.supersedes.length > 0 && <Chip tone="muted" icon="layers" title="the live values this write replaced">replaces {e.supersedes.join(', ')}</Chip>}
                  {e.op_id && <Chip tone="muted" title={e.op_id}>op {short(e.op_id, 6)}</Chip>}
                  {e.reason && <Chip tone="accent" icon="scale">{e.reason}</Chip>}
                </div>
              </li>
            ))}
          </ol>
        )}
      </aside>
    </div>
  )
}
