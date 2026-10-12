// The partition lab: cut or slow any link (applied to BOTH ends), burst traffic, forge an event.
import { useState } from 'react'
import { useDemo } from '../state/demo'
import type { RegionId } from '../api/types'
import { Icon } from './icons'
import { Chip, NODE_NAME, NodeDot, Segmented } from './ui'

type Mode = 'up' | 'slow' | 'down'

export function NetworkPanel() {
  const d = useDemo()
  const ids = d.joined
  const pairs = ids.flatMap((a, i) => ids.slice(i + 1).map((b) => [a, b] as [RegionId, RegionId]))
  const [target, setTarget] = useState<RegionId>(ids[0] ?? 'mum')

  /** the mode a pair is in, as the nodes themselves report it (the worse end wins) */
  const modeOf = (a: RegionId, b: RegionId): Mode | null => {
    const x = d.snaps[a]?.status?.peers[b]?.link, y = d.snaps[b]?.status?.peers[a]?.link
    const seen = [x, y].filter(Boolean)
    if (!seen.length) return null
    return seen.includes('down') ? 'down' : seen.includes('slow') ? 'slow' : 'up'
  }

  return (
    <div className="panel network">
      <section>
        <header className="panel__head"><h4><Icon name="link" size={16} /> Links</h4><span className="muted">A change is applied on both nodes, as in the real backend.</span></header>
        {pairs.map(([a, b]) => {
          const m = modeOf(a, b)
          return (
            <div key={a + b} className="linkrow">
              <span className="linkrow__name"><NodeDot id={a} size={9} />{NODE_NAME[a]}<Icon name={m === 'down' ? 'unlink' : 'link'} size={14} className={`linkrow__ico is-${m ?? 'unknown'}`} /><NodeDot id={b} size={9} />{NODE_NAME[b]}</span>
              <Segmented value={(m ?? 'up') as Mode} onChange={(v) => void d.setLink(a, b, v)}
                options={[{ id: 'up', label: 'Up', tone: 'ok' }, { id: 'slow', label: 'Slow', tone: 'warn' }, { id: 'down', label: 'Cut', tone: 'bad' }]} />
            </div>
          )
        })}
        <div className="network__quick">
          <button className="btn btn--sm" onClick={() => pairs.forEach(([a, b]) => void d.setLink(a, b, 'down'))}><Icon name="unlink" size={14} />Cut every link</button>
          <button className="btn btn--sm" onClick={() => pairs.forEach(([a, b]) => void d.setLink(a, b, 'up'))}><Icon name="link" size={14} />Heal every link</button>
        </div>
        <p className="muted small">Slow = 2 kB/s with 150 ms delay: critical events (a payment, a Same-day upgrade) cross first, bulk waits but is never dropped.</p>
      </section>

      <section>
        <header className="panel__head"><h4><Icon name="bolt" size={16} /> Traffic and tampering</h4>
          <Segmented size="sm" value={target} onChange={setTarget} options={ids.map((r) => ({ id: r, label: NODE_NAME[r] }))} /></header>
        <div className="network__quick">
          <button className="btn btn--sm" onClick={() => void d.burst(target, 50)}><Icon name="activity" size={14} />Burst 50</button>
          <button className="btn btn--sm" onClick={() => void d.burst(target, 300)}><Icon name="activity" size={14} />Burst 300</button>
          <button className="btn btn--sm btn--warn" onClick={() => void d.tamper(target)}><Icon name="skull" size={14} />Tamper</button>
        </div>
        <p className="muted small">Burst writes bulk cart activity (priority 2) to {NODE_NAME[target]}. Tamper sends three forged events to {NODE_NAME[target]} through the carry door: all must be rejected and the state must not move. Results are in the Security tab.</p>
      </section>

      <section>
        <header className="panel__head"><h4><Icon name="refresh" size={16} /> Demo</h4></header>
        <div className="network__quick">
          <button className="btn btn--sm" onClick={() => d.reset(3)}><Icon name="refresh" size={14} />Reset (3 nodes)</button>
          {d.mode === 'mock' && <button className="btn btn--sm" onClick={() => d.reset(2)}><Icon name="refresh" size={14} />Reset with 2 nodes (join Delhi live)</button>}
          {d.mode === 'live' && <Chip tone={d.chaosAvailable ? 'ok' : 'warn'} icon="power">{d.chaosAvailable ? 'chaos agent connected' : 'chaos agent not running on :9000'}</Chip>}
        </div>
      </section>
    </div>
  )
}
