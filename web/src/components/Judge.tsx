// The judge panel's always-on part: node cards, request dots, the four counters, the data-at-risk meter.
import { useDemo, type Heal } from '../state/demo'
import type { Level, RegionId, Status } from '../api/types'
import { fmtAge, fmtMs, plural, short } from '../lib/util'
import { Icon } from './icons'
import { Chip, HoldButton, LEVEL_TEXT, LevelChip, Meter, NODE_NAME, NodeDot, Stat, nodeColor, useNow } from './ui'

const RUNGS: { level: Level; label: string }[] = [
  { level: 'HEALTHY', label: 'Healthy' }, { level: 'CONSTRAINED', label: 'Constrained' }, { level: 'ISOLATED', label: 'Isolated' }, { level: 'RECOVERING', label: 'Recovering' },
]
const rungOf = (l: Level | null): Level | null => (l === 'SYNCING' ? 'HEALTHY' : l === 'OVERLOADED' ? 'ISOLATED' : l)

export function Ladder({ level }: { level: Level | null }) {
  const cur = rungOf(level)
  return (
    <ol className="ladder" aria-label="Degradation ladder">
      {RUNGS.map((r) => (
        <li key={r.level} className={`ladder__rung ${cur === r.level ? `is-on is-${r.level.toLowerCase()}` : ''}`} title={LEVEL_TEXT[r.level]}><i />{r.label}</li>
      ))}
    </ol>
  )
}

function QueueBars({ q }: { q: NonNullable<Status['queues']> }) {
  const total = Math.max(1, q.P0 + q.P1 + q.P2)
  return (
    <div className="queue" title={`Waiting to be pushed. P0 critical ${q.P0}, P1 normal ${q.P1}, P2 bulk ${q.P2}`}>
      <div className="queue__bar">
        <span className="q0" style={{ flexGrow: q.P0 / total }} /><span className="q1" style={{ flexGrow: q.P1 / total }} /><span className="q2" style={{ flexGrow: q.P2 / total }} />
      </div>
      <span className="queue__text"><b className="q0t">P0 {q.P0}</b> <b className="q1t">P1 {q.P1}</b> <b className="q2t">P2 {q.P2}</b></span>
    </div>
  )
}

export function NodeCard({ id }: { id: RegionId }) {
  const d = useDemo()
  const joined = d.joined.includes(id)
  const snap = d.snaps[id]
  const s = snap?.ok ? snap.status : undefined
  const up = !!s
  const serving = d.sdkState.serving === id
  const level = s?.level ?? null
  const risk = s?.risk
  const carts = snap?.reports.length ?? 0
  const killedByAgent = d.chaosRunning[id] === false

  if (!joined && d.mode === 'mock') {
    return (
      <article className="node node--empty" style={{ ['--node' as string]: nodeColor(id) }}>
        <header className="node__head"><NodeDot id={id} size={12} /><h3>{NODE_NAME[id]}</h3><Chip tone="muted">not joined</Chip></header>
        <p className="muted">This node is configured but not part of the mesh yet. It joins as an empty node and catches up from the others.</p>
        <button className="btn btn--primary btn--sm" onClick={() => void d.start(id)}><Icon name="plus" size={14} />Join {NODE_NAME[id]}</button>
      </article>
    )
  }

  return (
    <article className={`node ${serving ? 'node--serving' : ''} ${!up ? 'node--down' : ''} ${level ? `node--${level.toLowerCase()}` : ''}`} style={{ ['--node' as string]: nodeColor(id) }}>
      <header className="node__head">
        <NodeDot id={id} size={12} /><h3>{NODE_NAME[id]}</h3>
        <LevelChip level={level} unknown={!up} />
        {serving && <Chip tone="accent" icon="bolt" title="The browser SDK is sending requests here">serving</Chip>}
      </header>

      <Ladder level={up ? level : null} />

      {!up ? (
        <div className="node__down">
          <Icon name={killedByAgent ? 'skull' : 'wifioff'} size={22} />
          <div><strong>{killedByAgent ? 'Process killed' : 'Unreachable from this browser'}</strong>
            <p className="muted">{snap?.error ?? 'waiting for the first answer'}. Shown as UNKNOWN, never as healthy.</p></div>
        </div>
      ) : (
        <dl className="node__facts">
          <div><dt>Heads</dt><dd className="heads">{Object.entries(s!.heads).map(([o, n]) => <span key={o} className="head-chip" style={{ ['--c' as string]: nodeColor(o) }}>{o} {n}</span>)}</dd></div>
          <div><dt>Events</dt><dd>{s!.event_count}<small> · {carts} carts</small></dd></div>
          <div title="Events only this node holds: what a crash right now would lose unless a browser outbox carries them">
            <dt>At risk</dt>
            <dd className={risk && risk.unreplicated > 0 ? (risk.critical_unreplicated > 0 ? 'tone-bad' : 'tone-warn') : 'tone-ok'}>
              {risk ? risk.unreplicated : '—'}{risk && risk.critical_unreplicated > 0 && <small> · {risk.critical_unreplicated} critical</small>}
              {risk && risk.unreplicated > 0 && <small> · oldest {fmtAge(risk.oldest_age_ms)}</small>}
            </dd>
          </div>
          {s!.queues && <div><dt>Push queue</dt><dd><QueueBars q={s!.queues} /></dd></div>}
          <div><dt>Links</dt><dd className="links">{Object.entries(s!.peers).map(([p, st]) => (
            <Chip key={p} tone={st.link === 'down' ? 'bad' : st.link === 'slow' ? 'warn' : st.reachable ? 'ok' : 'muted'} icon={st.link === 'down' ? 'unlink' : 'link'} title={st.last_error ?? 'ok'}>
              {p} {st.link}{!st.reachable && st.link === 'up' ? ' · silent' : ''}</Chip>))}
          </dd></div>
          {(s!.quarantined > 0 || (s!.carry && s!.carry.received > 0)) && (
            <div><dt>Security</dt><dd>{s!.quarantined > 0 && <Chip tone="bad" icon="shield">{s!.quarantined} quarantined</Chip>} {s!.carry && s!.carry.received > 0 && <Chip tone="info" icon="package">{s!.carry.new} carried in</Chip>}</dd></div>
          )}
        </dl>
      )}

      <footer className="node__foot">
        <span className="muted node__digest" title="event digest">#{up ? short(s!.event_digest, 7) : '—'}</span>
        {up ? (
          <HoldButton disabled={!d.chaosAvailable} tone="bad" onConfirm={() => void d.crash(id)} title={d.chaosAvailable ? 'Press and hold to kill this node (kill -9)' : 'The chaos agent is not running on :9000'}><Icon name="power" size={14} />Hold to crash</HoldButton>
        ) : (
          <button className="btn btn--primary btn--sm" disabled={!d.chaosAvailable} onClick={() => void d.start(id)}><Icon name="play" size={14} />Restart</button>
        )}
      </footer>
    </article>
  )
}

export function NodeStrip() {
  const d = useDemo()
  const ids = d.regions.map((r) => r.id)
  return <div className="nodes">{ids.map((id) => <NodeCard key={id} id={id} />)}</div>
}

export function RequestStream() {
  const { sdkState } = useDemo()
  const tl = sdkState.timeline.slice(-30)
  const lastEntry = tl.at(-1)
  return (
    <div className="stream card">
      <header className="card__head card__head--tight"><h3><Icon name="activity" size={16} /> Requests</h3>
        <div className="legend">{(['mum', 'pun', 'del'] as const).map((r) => <span key={r}><NodeDot id={r} size={8} />{NODE_NAME[r]}</span>)}<span><i className="dot-fail" />failed attempt</span></div>
      </header>
      <div className="stream__dots" aria-live="polite">
        {!tl.length && <span className="muted">Add something to a cart and watch the request land.</span>}
        {tl.map((e) => (
          <span key={e.n} className="stream__item">
            {e.failedOn.map((f, i) => <i key={i} className="dot-fail" title={`${NODE_NAME[f]} did not answer`} />)}
            <i className={`dot ${e.queued ? 'dot--queued' : ''} ${e.tried ? 'dot--failover' : ''}`} style={{ background: e.queued ? 'transparent' : nodeColor(e.region) }}
              title={`${e.label}\n${e.queued ? 'kept on the device' : NODE_NAME[e.region ?? ''] ?? '—'} · ${fmtMs(e.ms)}${e.tried ? ` · after ${e.tried} failed attempt` : ''}`} />
          </span>
        ))}
      </div>
      {lastEntry && (
        <p className="stream__last"><b>{lastEntry.label}</b> → {lastEntry.queued ? 'kept on the device' : <><NodeDot id={lastEntry.region} size={8} /> {NODE_NAME[lastEntry.region ?? '']}</>} · {fmtMs(lastEntry.ms)}
          {lastEntry.tried > 0 && <Chip tone="warn" icon="refresh">failed over</Chip>}</p>
      )}
    </div>
  )
}

export function Counters() {
  const { sdkState: s, presence } = useDemo()
  const c = s.counters
  const present = presence?.present ?? 0
  const checked = presence?.checked ?? 0
  const missing = checked - present
  return (
    <div className="counters card">
      <Stat label="Requests" value={c.requests} tone="info" hint="Calls made by the storefront" />
      <Stat label="Errors shown to shoppers" value={c.errors} tone={c.errors ? 'bad' : 'ok'} hint="Errors the shopper actually saw. The number that must stay 0." />
      <Stat label="Acknowledged writes" value={c.acknowledged} tone="info" hint="Distinct events a node acknowledged" />
      <div className={`stat stat--${presence === null || !checked ? 'muted' : missing ? 'warn' : 'ok'}`} title="Acknowledged events held by at least one node that answers right now">
        <div className="stat__value">{presence === null ? '—' : `${present}`}<small>{presence ? ` / ${checked}` : ''}</small></div>
        <div className="stat__label">Present on survivors</div>
        <div className="stat__sub">{presence === null ? 'no node answered' : !checked ? 'no writes yet' : missing ? `${missing} not on a survivor yet` : 'acknowledged = present'}</div>
      </div>
      <div className="counters__extra">
        <Chip tone={c.failovers ? 'warn' : 'muted'} icon="refresh">{plural(c.failovers, 'failover')}</Chip>
        <Chip tone={c.carried ? 'info' : 'muted'} icon="package">{c.carried} carried</Chip>
        {c.queued > 0 && <Chip tone="warn" icon="clock">{c.queued} kept on device</Chip>}
        {c.carryRejected > 0 && <Chip tone="bad" icon="shield">{c.carryRejected} rejected</Chip>}
      </div>
    </div>
  )
}

export function RiskMeter() {
  const d = useDemo()
  const rows = d.regions.map((r) => ({ id: r.id, s: d.snaps[r.id]?.ok ? d.snaps[r.id]!.status : undefined }))
  const max = Math.max(8, ...rows.map((r) => r.s?.risk?.unreplicated ?? 0))
  return (
    <div className="risk card">
      <header className="card__head card__head--tight"><h3><Icon name="gauge" size={16} /> Data at risk</h3><span className="muted">what a crash right now would lose</span></header>
      {rows.map(({ id, s }) => (
        <div key={id} className="risk__row">
          <span className="risk__name"><NodeDot id={id} size={8} />{NODE_NAME[id]}</span>
          {s?.risk ? (
            <Meter value={s.risk.unreplicated} max={max} tone={s.risk.critical_unreplicated ? 'bad' : s.risk.unreplicated ? 'warn' : 'ok'}
              sub={s.risk.unreplicated ? `${s.risk.unreplicated} events · ${s.risk.critical_unreplicated} critical` : 'nothing at risk'} />
          ) : <span className="muted">{s ? 'not reported' : 'unknown'}</span>}
        </div>
      ))}
    </div>
  )
}

export function HealBanner() {
  const { heals } = useDemo()
  const now = useNow(1000)
  const h: Heal | undefined = heals.find((x) => now - x.at < 25000)
  if (!h) return null
  const mins = Math.floor(h.isolatedMs / 60000), secs = Math.round((h.isolatedMs % 60000) / 1000)
  return (
    <div className="heal" role="status" key={h.at}>
      <Icon name="check" size={18} />
      <span><b>{NODE_NAME[h.region]} healed.</b> Cut {mins ? `${mins}m ` : ''}{secs}s · {h.eventsSynced} events synced · {h.conflicts} conflicts · {h.reviews} need review</span>
    </div>
  )
}
