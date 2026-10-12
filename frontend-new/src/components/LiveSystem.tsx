// The live backend, as the browser sees it. Everything here is read from the regions' own answers; a region that does not
// answer is shown as offline or unknown, never as healthy.
import { useEffect, useRef, useState } from 'react'
import { useDemo } from '../state/demo'
import type { EventEnvelope, RegionId } from '../api/types'
import { cartCount, cartItems, cartTotal } from '../domain/shop'
import { rupees } from '../lib/util'
import { Icon } from './icons'
import { HoldButton, NODE_NAME, Segmented, useNow } from './ui'
import { Light, ServerStack, type Phase } from './Visuals'

// ---- the evidence tiles ---------------------------------------------------------------------------------------------
export function SessionStats() {
  const d = useDemo(), c = d.sdkState.counters
  const missing = d.presence?.answered ? Math.max(0, d.presence.checked - d.presence.present) : null
  const tiles = [
    { name: 'Acknowledged', value: c.acknowledged, hint: 'Distinct writes saved this session', icon: 'check', tone: 'ok' },
    { name: 'Recovered', value: c.recovered, hint: 'Carried after a region stopped answering', icon: 'refresh', tone: 'neutral' },
    { name: 'Not on survivors', value: missing === null ? '—' : missing, hint: !c.acknowledged ? 'No session writes to check yet' : d.presence?.answered ? `${d.presence.present} of ${d.presence.checked} checked events present` : 'Waiting for a region to answer', icon: 'shield', tone: missing ? 'warn' : 'neutral' },
  ] as const
  return (
    <section className="cx-tiles" aria-label="Session evidence">
      {tiles.map(t => (
        <article key={t.name} className={`cx-tile cx-tile--${t.tone}`}>
          <span className="cx-tile-icon"><Icon name={t.icon} size={24} /></span>
          <div>
            <span className="cx-tile-name">{t.name}</span>
            <strong key={String(t.value)}>{t.value}</strong>
            <small>{t.hint}</small>
          </div>
        </article>
      ))}
    </section>
  )
}

// ---- a region's life cycle, for the status light ------------------------------------------------------------------
type Marks = Record<string, { crashAt?: number; startAt?: number }>
const PHASE_TEXT: Record<Phase, { label: string; sub: string }> = {
  healthy: { label: 'Healthy', sub: 'Answering and in sync' },
  recovering: { label: 'Catching up', sub: 'Copying what it missed' },
  degraded: { label: 'Degraded', sub: 'Cannot fully reach its peers' },
  starting: { label: 'Starting', sub: 'Replaying its log' },
  crashing: { label: 'Crashing', sub: 'The process is being killed' },
  down: { label: 'Offline', sub: 'Not answering. Its data is unknown' },
  unknown: { label: 'Unknown', sub: 'No answer yet' },
}

/** The three region cards: each shows its own life cycle, and carries its own crash / restart control. */
export function RegionCards() {
  const d = useDemo()
  const now = useNow(400)
  const serving = d.route.region || d.viewRegion // where the selected shopper's next write goes
  const selected = d.cartFor(d.shopper)
  const scale = Math.max(10, ...d.members.map(r => d.rates[r.id] ?? 0)) // one scale for every card, so the split between regions is visible

  const [marks, setMarks] = useState<Marks>({})
  const [arm, setArm] = useState<Record<string, number>>({})
  const [flash, setFlash] = useState<Record<string, 'crash' | 'heal' | undefined>>({})
  const prev = useRef<Record<string, Phase>>({})
  const timers = useRef<number[]>([])
  useEffect(() => () => timers.current.forEach(clearTimeout), [])

  const phaseOf = (id: RegionId): Phase => {
    const snap = d.snaps[id], up = !!(snap?.ok && snap.status)
    const m = marks[id] ?? {}
    if (!up) return m.startAt && now - m.startAt < 20000 ? 'starting' : snap ? 'down' : 'unknown'
    if (m.crashAt && now - m.crashAt < 3500) return 'crashing' // the kill was issued and the node has not stopped answering yet
    const level = snap!.status!.level
    return level === 'HEALTHY' ? 'healthy' : level === 'RECOVERING' ? 'recovering' : 'degraded'
  }
  const phases = Object.fromEntries(d.members.map(r => [r.id, phaseOf(r.id)])) as Record<string, Phase>
  const phaseKey = d.members.map(r => phases[r.id]).join('|')

  // one-shot animations when a region crashes or comes back to health
  useEffect(() => {
    for (const r of d.members) {
      const before = prev.current[r.id], after = phases[r.id]
      prev.current[r.id] = after
      if (before === undefined || before === 'unknown' || before === after) continue // the first answer after page load is not a recovery
      const kind = (after === 'crashing' || (after === 'down' && before !== 'crashing')) ? 'crash' : after === 'healthy' ? 'heal' : undefined
      if (!kind) continue
      setFlash(f => ({ ...f, [r.id]: kind }))
      timers.current.push(window.setTimeout(() => setFlash(f => ({ ...f, [r.id]: undefined })), 1200)) // not cleared by this effect: the phase changes again within a second
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phaseKey])

  const crash = (id: RegionId) => { setMarks(m => ({ ...m, [id]: { crashAt: Date.now() } })); void d.crash(id) }
  const start = (id: RegionId) => { setMarks(m => ({ ...m, [id]: { startAt: Date.now() } })); void d.start(id) }

  return (
    <div className="cx-regions">
        {d.members.map(r => {
          const snap = d.snaps[r.id], status = snap?.ok ? snap.status : undefined
          const load = status?.load, limits = status?.rate, wps = d.rates[r.id]
          const pct = status && wps !== undefined ? Math.min(100, wps / scale * 100) : 0
          const busy = (d.sdkState.regions.find(x => x.id === r.id)?.busyUntil ?? 0) > now
          const phase = phases[r.id]
          const cart = snap?.reports.find(c => c.report_id === selected?.report_id)
          const localItems = cart ? cartItems(cart) : []
          const up = !!status
          const text = phase === 'down' && d.chaosRunning[r.id] === false ? { label: 'Offline', sub: 'The process was killed. Its data is unknown' } : PHASE_TEXT[phase]
          const isServing = serving === r.id && up
          return (
            <article key={r.id} className={`rc rc--${phase} ${isServing ? 'is-serving' : ''} ${flash[r.id] ? `flash-${flash[r.id]}` : ''} ${(arm[r.id] ?? 0) > 0 ? 'is-arming' : ''}`} style={{ ['--arm' as string]: arm[r.id] ?? 0 }}>
              <header>
                <span className={`rc-pill ${isServing ? 'rc-pill--serving' : ''}`}>{isServing ? 'Serving requests' : 'Replica'}</span>
                <div>
                  <h3>{r.name}</h3>
                  <p className="rc-state"><Light phase={phase} size={15} /><b>{text.label}</b></p>
                </div>
                <ServerStack state={phase === 'healthy' ? 'healthy' : phase === 'down' || phase === 'crashing' ? 'down' : 'isolated'} small />
              </header>
              <p className="rc-sub">{text.sub}</p>

              <div className="rc-numbers">
                <div><strong key={status?.event_count}>{status?.event_count ?? '—'}</strong><span>events stored</span></div>
                <div><strong>{up ? snap!.reports.length : '—'}</strong><span>carts</span></div>
                <div><strong className={status?.risk?.unreplicated ? 'warn' : ''}>{status?.risk?.unreplicated ?? '—'}</strong><span>at risk</span></div>
              </div>

              <div className="rc-load">
                <div className="rc-load-head"><span>Writes per second</span><strong key={wps}>{up && wps !== undefined ? wps.toFixed(1) : '—'}</strong></div>
                <div className="rc-bar" role="meter" aria-label={`${r.name} writes per second`} aria-valuemin={0} aria-valuemax={scale} aria-valuenow={up ? wps ?? 0 : 0}
                  title={load ? 'Reported by the region' : 'Measured here from how fast this region’s own write counter moves; the backend does not report it yet'}><i className={pct > 70 ? 'is-hot' : ''} style={{ width: `${pct}%` }} /></div>
                <div className="rc-load-foot">
                  <span>Queue <b>{load ? load.queue_depth : '—'}</b></span>
                  {limits && limits.region_limit_per_s > 0 && <span className="rc-tag rc-tag--warn" title="Above this rate the region answers 503 and the browser sends the write to the next region">Limit {limits.region_limit_per_s} a second{limits.refused_region ? ` · ${limits.refused_region} turned away` : ''}</span>}
                  {busy && !(limits && limits.region_limit_per_s > 0) && <span className="rc-tag rc-tag--warn">Busy</span>}
                </div>
                <div className="rc-power">
                  <span>Power</span>
                  <Segmented size="sm" value={load?.power ?? 'medium'} disabled={!up || !load} onChange={p => void d.setPower(r.id, p)}
                    options={[{ id: 'low', label: 'Low' }, { id: 'medium', label: 'Medium' }, { id: 'high', label: 'High' }]} />
                  <small>{load ? `${load.batch} per batch` : up ? 'Not reported' : ''}</small>
                </div>
              </div>

              <div className="rc-cart">
                <span>{d.shopper}’s cart</span>
                <strong key={cart ? cartCount(localItems) : -1}>{!up ? 'Unknown' : cart ? `${cartCount(localItems)} items · ${rupees(cartTotal(localItems))}` : 'Not here yet'}</strong>
              </div>

              <footer>

                {up && phase !== 'starting'
                  ? <HoldButton label={`Hold to crash ${r.name}`} disabled={!d.chaosAvailable} onArm={k => setArm(a => ({ ...a, [r.id]: k }))} onConfirm={() => crash(r.id)} title={d.chaosAvailable ? 'Press and hold to kill this region’s process' : 'Crash controls need the local chaos agent'}><Icon name="power" size={17} />Hold to crash</HoldButton>
                  : <button className="btn btn--primary rc-restart" aria-label={`Restart ${r.name}`} disabled={!d.chaosAvailable || phase === 'starting'} onClick={() => start(r.id)}><Icon name="refresh" size={17} className={phase === 'starting' ? 'spin' : ''} />{phase === 'starting' ? 'Starting…' : 'Restart'}</button>}
              </footer>
              <span className="rc-arm" aria-hidden="true" />
            </article>
          )
        })}
        {d.optional.map(r => {
          const startedAt = marks[r.id]?.startAt
          const starting = !!startedAt && now - startedAt < 30000
          const known = d.mode === 'mock' || r.id in d.chaosRunning // the real chaos agent only starts regions it was told about (REGIONS=...,blr)
          const reason = !d.chaosAvailable ? 'Adding a region needs the local chaos agent' : !known ? `Start the chaos agent with REGIONS=mum,pun,del,${r.id}` : ''
          return (
            <article key={r.id} className={`rc rc--add ${starting ? 'is-starting' : ''}`}>
              <header>
                <span className="rc-pill">Not joined</span>
                <div>
                  <h3>{r.name}</h3>
                  <p className="rc-state"><Light phase={starting ? 'starting' : 'unknown'} size={15} /><b>{starting ? 'Starting' : 'Not joined'}</b></p>
                </div>
              </header>
              <p className="rc-sub">{starting ? 'Starting up. Once it answers it joins empty and copies everything from the other regions.' : 'It joins empty and catches up from the others. With one more region the load can spread one way further.'}</p>
              <footer>
                <button className="btn btn--primary rc-add" aria-label={`Add ${r.name}`} disabled={!!reason || starting} title={reason || undefined} onClick={() => start(r.id)}>
                  <Icon name={starting ? 'refresh' : 'plus'} size={17} className={starting ? 'spin' : ''} />{starting ? 'Starting…' : `Add ${r.name}`}
                </button>
              </footer>
              {reason && <p className="rc-hint">{reason}</p>}
            </article>
          )
        })}
      </div>
  )
}

export function Topology({ inspect }: { inspect: () => void }) {
  const d = useDemo()
  const route = d.route
  const serving = route.region // where the selected shopper's next write goes
  const pending = Object.values(d.pending).some(Boolean)
  const hasCuts = d.joined.some(a => Object.values(d.snaps[a]?.status?.peers || {}).some(p => p.link === 'down'))
  const allUp = d.joined.every(r => d.snaps[r]?.ok)
  const replicating = allUp && !hasCuts && (d.convergence === 'SYNCING' || pending)
  // why this region serves: the shopper's home region first (when that is on), then the fixed order Mumbai, Pune, Delhi, Bengaluru;
  // a region that answered 503 is left alone until its Retry-After has passed, and one that is just back waits to be trusted again
  const ideal = route.ideal
  const secs = Math.max(1, Math.ceil(route.busyForMs / 1000))
  const why = !serving ? 'No region has answered yet.'
    : route.state === 'ok' ? (d.homeRegions ? `${d.shopper}’s home region is ${NODE_NAME[ideal]}, and it is answering.` : `${NODE_NAME[ideal]} is first in the failover order, and it is answering.`)
    : route.state === 'busy' ? `${NODE_NAME[ideal]} is at its write limit (it answered 503), so the browser sent ${d.shopper}’s writes to ${NODE_NAME[serving]}. It will try ${NODE_NAME[ideal]} again in about ${secs} s.`
    : route.state === 'recovering' ? `${NODE_NAME[ideal]} is back but not trusted yet (it must answer three checks in a row), so ${NODE_NAME[serving]} serves for now.`
    : `${NODE_NAME[ideal]} is not answering, so the browser moved to the next region in order: ${NODE_NAME[serving]}.`
  const short = route.state === 'busy' ? 'at its write limit' : route.state === 'recovering' ? 'recovering' : 'down'

  return (
    <section className="cx-board">
      <div className="cx-section-head">
        <div><h2>{['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six'][d.members.length] ?? d.members.length} region{d.members.length === 1 ? '' : 's'}. One shared cart.</h2><p>Follow {d.shopper}’s changes across the system.</p></div>
        <button className="cx-link" onClick={inspect}>Inspect the nodes<Icon name="arrow" size={17} /></button>
      </div>

      <div className={`cx-browser ${replicating ? 'is-active' : ''}`}>
        <span className="cx-browser-icon"><Icon name="cart" size={24} /></span>
        <div><strong>{d.shopper}’s browser</strong><span>{d.sdkState.outbox.length} signed events in outbox · {d.sdkState.deviceQueue} queued</span></div>
        <label className="switch cx-home" title="Each shopper’s requests go to their own home region first: Alice and Bob Mumbai, Chitra and Dev Pune, Esha Delhi. Off: everyone starts at Mumbai.">
          <input type="checkbox" checked={d.homeRegions} onChange={e => d.setHomeRegions(e.target.checked)} /><i /><span>Home regions</span>
        </label>
        <div className="cx-route" title={why}><i /><span>{serving ? <>Serving from <b>{NODE_NAME[serving]}</b></> : 'Finding an available region'}{serving && route.state !== 'ok' && <em className="cx-why"> · {NODE_NAME[ideal]} is {short}</em>}</span><i /></div>
      </div>
      {serving && route.state !== 'ok' && <p className="cx-why-line" role="status">{why}</p>}

      <RegionCards />

      <div className="cx-links" aria-label="Replication links">
        {d.joined.flatMap((a, i) => d.joined.slice(i + 1).map(b => {
          const p = d.snaps[a]?.status?.peers[b], back = d.snaps[b]?.status?.peers[a]
          const mode = !d.snaps[a]?.ok || !d.snaps[b]?.ok || !p || !back ? 'unknown' : p.link === 'down' || back.link === 'down' ? 'down' : p.link === 'slow' || back.link === 'slow' ? 'slow' : p.reachable && back.reachable ? 'up' : 'unknown'
          return (
            <span key={a + b} className={`cx-link-pill link-${mode} ${replicating && mode === 'up' ? 'is-flowing' : ''}`}>
              <i />{NODE_NAME[a]}<Icon name={mode === 'down' ? 'unlink' : 'link'} size={16} />{NODE_NAME[b]}
              <b>{mode === 'up' ? 'Connected' : mode === 'down' ? 'Paused' : mode === 'slow' ? 'Slow' : 'Unknown'}</b>
            </span>
          )
        }))}
      </div>
    </section>
  )
}

// ---- the latest action, as three steps -----------------------------------------------------------------------------
export function LatestAction() {
  const d = useDemo(), latest = d.sdkState.timeline.at(-1)
  const [event, setEvent] = useState<EventEnvelope | null>(null)
  const id = d.sdk.acknowledgedIds().at(-1)
  const reports = d.viewRegion ? d.snaps[d.viewRegion]?.reports || [] : []
  const report = reports.find(r => Object.values(r.live).some(ids => id && ids.includes(id)) || r.state.notes.some(n => n.id === id))
  useEffect(() => {
    let live = true; setEvent(null)
    if (report && d.viewRegion && id) void d.history(d.viewRegion, report.report_id).then(events => { if (live) setEvent(events?.find(e => e.event_id === id) || null) })
    return () => { live = false }
  }, [id, report?.report_id, d.viewRegion, d.history])
  const saved = latest && !latest.queued && latest.status >= 200 && latest.status < 300
  const replicated = saved && report?.consistency.replication === 'REPLICATED' && !report.consistency.stale_possible
  const steps = [
    { name: latest?.queued ? 'On this device' : 'Saved locally', done: !!saved, icon: 'database' },
    { name: 'Signed event', done: !!event?.sig, icon: 'lock' },
    { name: 'Peers confirmed', done: !!replicated, icon: 'check' },
  ] as const
  return (
    <section className="cx-flow">
      <div className="cx-flow-head"><span>Latest action</span><strong title={latest?.label}>{latest?.label || 'Add an item to start the story'}</strong></div>
      <ol>
        {steps.map((s, i) => (
          <li key={s.name} className={s.done ? 'done' : ''}>
            <i><Icon name={s.done ? 'check' : s.icon} size={17} /></i>{s.name}
            {i < steps.length - 1 && <em aria-hidden="true" />}
          </li>
        ))}
      </ol>
    </section>
  )
}

// ---- what the regions just told us ---------------------------------------------------------------------------------
export function EventTrail() {
  const d = useDemo()
  const seen = useRef<Record<string, number>>({})
  const [observations, setObservations] = useState<{ id: string; text: string; at: number; detail: string }[]>([])
  useEffect(() => {
    const next: typeof observations = []
    for (const r of d.members) {
      const status = d.snaps[r.id]?.status
      if (!status || !d.snaps[r.id]?.ok) continue
      const old = seen.current[r.id]
      if (old !== undefined && status.event_count > old) next.push({ id: `${r.id}-${status.event_count}`, text: `${r.name} received ${status.event_count - old} new event${status.event_count - old === 1 ? '' : 's'}`, at: d.snaps[r.id]!.at, detail: `${status.event_count} events stored` })
      seen.current[r.id] = status.event_count
    }
    if (next.length) setObservations(rows => [...next, ...rows].slice(0, 12))
  }, [d.snaps, d.members])
  const entries = [
    ...observations,
    ...d.sdkState.timeline.slice(-4).map(t => ({ id: `op-${t.n}`, text: t.label, at: t.at, detail: t.queued ? 'Queued on device' : `${t.region ? NODE_NAME[t.region] : 'No region'} · ${t.status} · ${t.ms} ms` })),
    ...d.carryLog.map(c => ({ id: `carry-${c.at}`, text: `Browser carried ${c.sent} events to ${NODE_NAME[c.to]}`, at: c.at, detail: `${c.results.filter(r => r.result === 'new').length} new · ${c.results.filter(r => r.result === 'rejected').length} rejected` })),
  ].sort((a, b) => b.at - a.at).slice(0, 5)
  return (
    <section className="cx-card cx-trail">
      <div className="cx-card-head"><h2>Live event trail</h2><span className="cx-live"><i />This session</span></div>
      {entries.length ? (
        <ol>
          {entries.map(e => (
            <li key={e.id}><i /><div><strong>{e.text}</strong><span>{e.detail}</span></div><time>{new Date(e.at).toLocaleTimeString('en-IN', { hour12: false })}</time></li>
          ))}
        </ol>
      ) : (
        <div className="cx-empty"><Icon name="activity" size={30} /><div><strong>Ready for your first action.</strong><p>Add a product on the left and follow its journey here.</p></div></div>
      )}
    </section>
  )
}

// ---- break things on purpose ---------------------------------------------------------------------------------------
export function FailureControls({ inspect }: { inspect: () => void }) {
  const d = useDemo()
  const [busy, setBusy] = useState(false)
  const pairs = d.joined.flatMap((a, i) => d.joined.slice(i + 1).map(b => [a, b] as [RegionId, RegionId]))
  const paused = pairs.some(([a, b]) => d.snaps[a]?.status?.peers[b]?.link === 'down' || d.snaps[b]?.status?.peers[a]?.link === 'down')
  const change = async () => { setBusy(true); try { await Promise.all(pairs.map(([a, b]) => d.setLink(a, b, paused ? 'up' : 'down'))) } finally { setBusy(false) } }
  return (
    <section className="cx-card cx-failure">
      <div className="cx-card-head"><h2>Try the unexpected.</h2></div>
      <ol className="cx-howto">
        <li><b>1</b>Pause replication between the regions.</li>
        <li><b>2</b>Add an item on the left.</li>
        <li><b>3</b>Crash the region that saved it: <em>press and hold</em> its red button.</li>
      </ol>
      <button className={`btn cx-pause ${paused ? 'is-paused' : ''}`} disabled={busy} onClick={() => void change()}>
        <Icon name={paused ? 'play' : 'pause'} size={20} />{paused ? 'Resume replication' : 'Pause replication'}
      </button>
      <div className="cx-failure-foot">
        <span>{d.chaosAvailable ? 'Hold a region’s crash button for about a second.' : 'Crash controls need the local chaos agent.'}</span>
        <button className="cx-link" onClick={inspect}>More controls<Icon name="arrow" size={16} /></button>
      </div>
    </section>
  )
}
