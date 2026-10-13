// The partition lab: cut or slow any link (applied to BOTH ends), limit a shopper or a region, burst traffic, forge an event.
import { useState } from 'react'
import { useDemo } from '../state/demo'
import type { RegionId } from '../api/types'
import { fmtMs } from '../lib/util'
import { Icon } from './icons'
import { Chip, NODE_NAME, NodeDot, Segmented } from './ui'

type Mode = 'up' | 'slow' | 'down'
const SHOPPER_LIMITS = [{ id: '0', label: 'Off' }, { id: '5', label: '5' }, { id: '10', label: '10' }, { id: '20', label: '20' }]
const REGION_LIMITS = [{ id: '1', label: '1' }, { id: '2', label: '2' }, { id: '5', label: '5' }]

export function NetworkPanel() {
  const d = useDemo()
  const ids = d.joined
  const pairs = ids.flatMap((a, i) => ids.slice(i + 1).map((b) => [a, b] as [RegionId, RegionId]))
  const [target, setTarget] = useState<RegionId>(ids[0] ?? 'mum')
  const [perS, setPerS] = useState('2')
  const aim = ids.includes(target) ? target : ids[0] ?? 'mum'
  const busy = !!d.loading || !!d.spamming
  const last = d.loadRuns[0]

  /** the mode a pair is in, as the nodes themselves report it (the worse end wins) */
  const modeOf = (a: RegionId, b: RegionId): Mode | null => {
    const x = d.snaps[a]?.status?.peers[b]?.link, y = d.snaps[b]?.status?.peers[a]?.link
    const seen = [x, y].filter(Boolean)
    if (!seen.length) return null
    return seen.includes('down') ? 'down' : seen.includes('slow') ? 'slow' : 'up'
  }

  const limited = ids.filter((r) => (d.snaps[r]?.status?.rate?.region_limit_per_s ?? 0) > 0)
  const changeRate = (v: string) => { setPerS(v); for (const r of limited) void d.setRegionLimit(r, Number(v)) } // a region that is limited follows the new rate

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

      <section className="limits">
        <header className="panel__head"><h4><Icon name="gauge" size={16} /> Limits</h4>
          {!d.rate.reported && <Chip tone="muted" icon="info" title="This backend does not report its limits in /v1/status yet.">not reported</Chip>}</header>

        <div className="limit-row">
          <div className="limit-text">
            <b>Limit each shopper</b>
            <small>{d.rate.on ? `${d.rate.burst} requests at once, then ${d.rate.refillPerS} more every second. Each shopper has an allowance of their own.` : 'Off: a shopper may send as many requests as they like.'}</small>
          </div>
          <Segmented size="sm" value={String(d.rate.on ? d.rate.burst : 0)} options={SHOPPER_LIMITS} onChange={(v) => void d.setShopperLimit(Number(v))} />
        </div>

        <div className="limit-row limit-row--head">
          <div className="limit-text"><b>Limit a region</b><small>A region above this many writes a second answers 503 and the browser sends the write to the next region.</small></div>
          <div className="limit-rate"><small>Writes a second</small><Segmented size="sm" value={perS} options={REGION_LIMITS} onChange={changeRate} /></div>
        </div>
        {ids.map((r) => {
          const st = d.snaps[r]?.status, lim = st?.rate
          const on = (lim?.region_limit_per_s ?? 0) > 0
          return (
            <div key={r} className={`limit-row limit-row--region ${on ? 'is-on' : ''}`}>
              <div className="limit-text"><b><NodeDot id={r} size={9} />{NODE_NAME[r]}</b>
                <small>{!st ? 'Not answering' : on ? `Accepts ${lim!.region_limit_per_s} write${lim!.region_limit_per_s === 1 ? '' : 's'} a second. ${lim!.refused_region} turned away so far.` : 'No limit'}</small></div>
              <label className="switch"><input type="checkbox" checked={on} disabled={!st} onChange={(e) => void d.setRegionLimit(r, e.target.checked ? Number(perS) : 0)} /><i /><span>Limit this region</span></label>
            </div>
          )
        })}
        <p className="muted small">Try it: limit Mumbai, then press <b>Spam as {d.shopper}</b> below, or turn on Auto-shoppers. The route on the Overview moves to the next region and no shopper sees an error. A shopper who moves starts with a full allowance in the new region: these limits protect each region, they are not a quota for the whole system.</p>
      </section>

      <section>
        <header className="panel__head"><h4><Icon name="bolt" size={16} /> Traffic and tampering</h4>
          <Segmented size="sm" value={aim} onChange={setTarget} options={ids.map((r) => ({ id: r, label: NODE_NAME[r] }))} /></header>
        <div className="network__quick">
          <button className="btn btn--sm" disabled={busy} onClick={() => void d.burst(aim, 300)}><Icon name="activity" size={14} />Burst 300</button>
          <button className="btn btn--sm" disabled={busy} onClick={() => void d.burst(aim, 1000)}><Icon name="activity" size={14} />Burst 1000</button>
          <button className="btn btn--sm" disabled={busy || ids.length < 2} onClick={() => void d.burst('spread', 1000)} title={`The same 1000 writes, divided over all ${ids.length} regions`}><Icon name="layers" size={14} />Spread 1000</button>
          <button className="btn btn--sm btn--spam" disabled={busy} onClick={() => void d.spam(d.shopper)}
            title={d.rate.on ? `25 quick adds as ${d.shopper}` : 'The per-shopper limit is off, so all 25 adds go through. A quick way to put load on a limited region.'}>
            <Icon name="bolt" size={14} />{d.spamming ? `${d.spamming} is spamming…` : `Spam as ${d.shopper}`}</button>
          <button className="btn btn--sm btn--warn" onClick={() => void d.tamper(aim)}><Icon name="skull" size={14} />Tamper</button>
        </div>

        {d.loading && (
          <div className="loadbar" role="status" aria-live="polite">
            <div className="loadbar-track"><i style={{ width: `${Math.min(100, (d.loading.done / d.loading.n) * 100)}%` }} /></div>
            <span>{d.loading.phase === 'sending'
              ? <>Sending <b>{d.loading.done}</b> of {d.loading.n} to {d.loading.targets.length > 1 ? `${d.loading.targets.length} regions` : NODE_NAME[d.loading.targets[0]]}{d.loading.peakQueue !== null && <> · queue peaked at <b>{d.loading.peakQueue}</b></>}</>
              : <>All {d.loading.n} sent. Waiting for every region to hold the same events…</>}</span>
          </div>
        )}
        {last && !d.loading && (
          <p className="loadlast">
            <b>Last run:</b> {last.ok} of {last.n} writes to {last.targets.length > 1 ? `${last.targets.length} regions` : NODE_NAME[last.targets[0]]} ({last.power} power) saved in {fmtMs(last.ms)}, <b>{last.perS} a second</b>
            {last.peakQueue !== null && <>, queue peaked at {last.peakQueue}</>}
            {last.settledMs !== null ? <>, and every region agreed {fmtMs(last.settledMs)} later.</> : <>; the regions had not agreed yet when it stopped waiting.</>}
            {(last.refused > 0 || last.overloaded > 0 || last.failed > 0) && <> {last.refused ? `${last.refused} refused by a shopper limit. ` : ''}{last.overloaded ? `${last.overloaded} turned away by a region limit. ` : ''}{last.failed ? `${last.failed} failed.` : ''}</>}
          </p>
        )}

        <p className="muted small">Burst writes real cart notes to {NODE_NAME[aim]} as fast as it takes them and times how long every region needs to agree. Spread divides the same writes over all {ids.length} regions. Spam sends 25 quick adds as {d.shopper}. Tamper sends three forged events to {NODE_NAME[aim]} through the carry door: all must be rejected and the state must not move. Results are in the Security and Scaling tabs.</p>
      </section>

      <section>
        <header className="panel__head"><h4><Icon name="refresh" size={16} /> Demo</h4></header>
        <div className="network__quick">
          <button className="btn btn--sm" onClick={() => d.reset(3)}><Icon name="refresh" size={14} />Reset session (3 nodes)</button>
          {d.mode === 'mock' && <button className="btn btn--sm" onClick={() => d.reset(2)}><Icon name="refresh" size={14} />Reset with 2 nodes (join Delhi live)</button>}
          {d.mode === 'live' && <Chip tone={d.chaosAvailable ? 'ok' : 'warn'} icon="power">{d.chaosAvailable ? 'chaos agent connected' : 'chaos agent not running on :9000'}</Chip>}
        </div>
      </section>
    </div>
  )
}
