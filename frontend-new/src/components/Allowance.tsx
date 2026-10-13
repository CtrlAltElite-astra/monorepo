// "Requests left": a shopper's own allowance (contract 8.3), read from the X-RateLimit-* headers of their last write, and the calm
// notice when a region asks them to slow down. A refusal here is an answer, never an error: nothing is lost and the shopper just waits.
import { useDemo } from '../state/demo'
import { fullAllowance, remainingNow, secondsUntilNext } from '../lib/allowance'
import type { Shopper } from '../domain/shop'
import { Icon } from './icons'
import { useNow } from './ui'

/** The thin bar under a shopper's name. Hidden while no region has a per-shopper limit. */
export function AllowanceMeter({ who }: { who: Shopper }) {
  const d = useDemo()
  const now = useNow(300)
  if (!d.rate.on || d.rate.burst <= 0) return null
  const a = d.allowance[who] ?? fullAllowance(d.rate.burst, d.rate.refillPerS, now) // no request since the limit went on: a full bucket
  const left = remainingNow(a, now)
  const pct = a.limit > 0 ? (left / a.limit) * 100 : 0
  const tone = left === 0 ? 'empty' : pct <= 30 ? 'low' : 'ok'
  return (
    <span className={`sp-meter sp-meter--${tone}`} role="meter" aria-label={`${who}: requests left`} aria-valuemin={0} aria-valuemax={a.limit} aria-valuenow={left}
      title={`${who} can make ${left} more request${left === 1 ? '' : 's'} right now. Out of ${a.limit}${a.refillPerS > 0 ? `, refilling ${a.refillPerS} a second.` : '; this allowance does not refill.'}`}>
      <span className="sp-meter-bar"><i style={{ width: `${pct}%` }} /></span>
      <small>{left} left</small>
    </span>
  )
}

/** Shown under the shoppers for a few seconds after the selected shopper was told to slow down. */
export function RateNote() {
  const d = useDemo()
  const now = useNow(400)
  const r = d.refusals[d.shopper]
  if (!r || now - r.at > r.retryAfterMs + 2500) return null
  const a = d.allowance[d.shopper]
  const wait = a ? secondsUntilNext(a, now) : Math.max(0, Math.ceil((r.retryAfterMs - (now - r.at)) / 1000))
  return (
    <p className="sp-rate-note" role="status">
      <Icon name={wait > 0 ? 'clock' : 'check'} size={18} />
      <span>{!Number.isFinite(wait) ? <>{d.shopper} has used every request this limit allows, and it does not refill.</> : wait > 0 ? <>{d.shopper} is going too fast. The next request is allowed in <b>{wait} s</b>.</> : <>{d.shopper} can go on now.</>} Nothing was lost.</span>
    </p>
  )
}
