// The shopper's side: pick a shopper, add products, set delivery speed, check out. Every write goes through the
// SDK (op_id, failover, outbox); what the cart shows is the view a node returned, never a local guess.
import { useMemo } from 'react'
import { useDemo } from '../state/demo'
import { CENTRES, PRODUCTS, SHOPPERS, SPEEDS, STATUS_LABEL, cartCode, cartCount, cartItems, cartTotal, isDone, type Shopper } from '../domain/shop'
import { rupees } from '../lib/util'
import { ProductArt } from './ProductArt'
import { Icon } from './icons'
import { Chip, NODE_NAME, NodeDot, ReplBadge, Segmented, useNow } from './ui'

const AVATAR = ['#2f6fde', '#7c5ce0', '#d6409f', '#e6a017', '#1f9d63']

export function Storefront({ onOpenHistory }: { onOpenHistory: (reportId: string) => void }) {
  const d = useDemo()
  const cart = d.cartFor(d.shopper)
  const items = useMemo(() => (cart ? cartItems(cart) : []), [cart])
  const status = cart?.state.status ?? 'open'
  const now = useNow(1000)
  const saved = d.saved[d.shopper]
  const busy = (field: string) => !!d.pending[`set:${d.shopper}:${field}`]
  const locked = isDone(status)
  const conflict = cart?.conflicts.some((c) => c.needs_review)

  return (
    <section className="store card" aria-label="Storefront">
      <header className="card__head">
        <div><h2>Storefront</h2><p className="muted">What the shopper sees. Every tap is saved on the nearest node first.</p></div>
        {saved && now - saved.at < 6000 && (
          <span className={`saved-pill ${saved.queued ? 'saved-pill--queued' : ''}`} key={saved.at}>
            <Icon name={saved.queued ? 'clock' : 'check'} size={13} />
            {saved.queued ? 'Saved on your device' : <>Saved on <NodeDot id={saved.region} size={8} /> {NODE_NAME[saved.region ?? ''] ?? '—'} · {saved.ms} ms</>}
            {saved.tried > 0 && <em>after {saved.tried} retry</em>}
          </span>
        )}
      </header>

      <div className="shoppers" role="tablist" aria-label="Shopper">
        {SHOPPERS.map((s, i) => {
          const c = d.cartFor(s as Shopper)
          const n = c ? cartCount(cartItems(c)) : 0
          return (
            <button key={s} role="tab" aria-selected={s === d.shopper} className={`shopper ${s === d.shopper ? 'shopper--on' : ''}`} onClick={() => d.setShopper(s)}>
              <span className="avatar" style={{ background: AVATAR[i] }}>{s[0]}</span>
              <span>{s}</span>
              {n > 0 && <span className="shopper__count">{n}</span>}
            </button>
          )
        })}
      </div>

      <div className="products">
        {PRODUCTS.map((p) => {
          const key = `add:${d.shopper}:${p.sku}`
          return (
            <article key={p.sku} className="product">
              <ProductArt glyph={p.glyph} hue={p.hue} />
              <div className="product__info"><strong>{p.name}</strong><span className="muted">{rupees(p.price)}</span></div>
              <button className="btn btn--primary btn--sm" disabled={d.pending[key] || locked} onClick={() => void d.addToCart(d.shopper, p)} title={locked ? 'This order is closed' : `Add ${p.name}`}>
                <Icon name={d.pending[key] ? 'refresh' : 'plus'} size={14} className={d.pending[key] ? 'spin' : ''} />Add
              </button>
            </article>
          )
        })}
      </div>

      <div className={`cart ${conflict ? 'cart--conflict' : ''}`}>
        <div className="cart__head">
          <h3><Icon name="cart" size={18} /> {d.shopper}'s cart {cart && <code className="muted">{cartCode(cart.report_id)}</code>}</h3>
          <div className="cart__chips">
            {cart && <Chip tone={STATUS_LABEL[status]?.tone ?? 'muted'}>{STATUS_LABEL[status]?.label ?? status}</Chip>}
            {conflict && <Chip tone="warn" icon="scale" title="Two nodes disagreed while cut off. See the Review tab.">needs review</Chip>}
            {cart && <ReplBadge value={cart.consistency.replication} stale={cart.consistency.stale_possible} />}
          </div>
        </div>

        {!cart || !items.length ? (
          <p className="muted cart__empty">{cart ? 'No items yet.' : 'No cart yet. Add a product to start one.'}</p>
        ) : (
          <ul className="cart__items">
            {items.map((i) => (
              <li key={i.sku}><span><strong>{i.qty}×</strong> {i.name}</span><span>{rupees(i.qty * i.price)}</span></li>
            ))}
            <li className="cart__total"><span>Total</span><strong>{rupees(cartTotal(items))}</strong></li>
          </ul>
        )}

        {cart && (
          <>
            <div className="cart__row">
              <label>Delivery</label>
              <Segmented size="sm" disabled={locked || busy('severity')} value={String(cart.state.severity ?? 1)}
                options={Object.entries(SPEEDS).map(([k, v]) => ({ id: k, label: v, tone: Number(k) >= 4 ? 'warn' : 'info' }))}
                onChange={(v) => void d.writeField(d.shopper, 'severity', Number(v))} />
            </div>
            <div className="cart__row">
              <label>Fulfilment</label>
              <select className="select" disabled={locked || busy('assigned_team')} value={cart.state.assigned_team ?? ''} onChange={(e) => void d.writeField(d.shopper, 'assigned_team', e.target.value || null)}>
                <option value="">Not assigned</option>{CENTRES.map((c) => <option key={c}>{c}</option>)}
              </select>
              <button className="btn btn--ghost btn--sm" onClick={() => onOpenHistory(cart.report_id)}><Icon name="history" size={14} />History</button>
            </div>
            <div className="cart__actions">
              <button className="btn btn--sm" disabled={locked || status !== 'open' || !items.length || busy('status')} onClick={() => void d.writeField(d.shopper, 'status', 'assigned')}><Icon name="cart" size={14} />Check out</button>
              <button className="btn btn--sm" disabled={locked || status === 'resolved' || !items.length || busy('status')} onClick={() => void d.writeField(d.shopper, 'status', 'resolved')}><Icon name="bolt" size={14} />Pay</button>
              <button className="btn btn--primary btn--sm" disabled={locked || !items.length || busy('status')} onClick={() => void d.writeField(d.shopper, 'status', 'closed')}><Icon name="package" size={14} />Place order</button>
              <button className="btn btn--ghost btn--sm" disabled={locked || busy('status')} onClick={() => void d.writeField(d.shopper, 'status', 'cancelled')}><Icon name="x" size={14} />Cancel</button>
            </div>
          </>
        )}
      </div>
    </section>
  )
}
