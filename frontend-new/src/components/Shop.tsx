// The storefront: ONE column that scrolls as a whole (header, shoppers, products, cart). Products are compact rows with a
// small thumbnail so the cart is never squeezed into a corner. Every action is a real write through the SDK.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useDemo } from '../state/demo'
import { CENTRES, PRODUCTS, RESTRICTED_PRODUCT, SHOPPERS, SPEEDS, STATUS_LABEL, cartCount, cartItems, cartTotal, isDone, restrictedProductEnabled, type Product } from '../domain/shop'
import { rupees } from '../lib/util'
import { AllowanceMeter, RateNote } from './Allowance'
import { Icon } from './icons'
import { ProductPhoto, useScrollReveal } from './Visuals'
import { Chip, NODE_NAME } from './ui'

const SKUS = ['FCOFF-500', 'BOTTLE-01', 'KURTA-01', 'EARBUD-01', 'LAMP-01', 'BAG-01']
const CATALOG = SKUS.map(sku => PRODUCTS.find(p => p.sku === sku)!)
const CATEGORIES = ['All essentials', 'Home', 'Everyday']
const HOME_SKUS = ['FCOFF-500', 'LAMP-01']
// The adults-only item is hidden unless the flag is on (?restricted=1): the check that refuses it joins with the identity lane.
const CATALOG_WITH_RESTRICTED = [...CATALOG, RESTRICTED_PRODUCT]

export function Shop({ onHistory, onReview }: { onHistory: (id: string) => void; onReview: () => void }) {
  const d = useDemo()
  const [category, setCategory] = useState(CATEGORIES[0])
  // Per shopper, never global: a shopper's tap must not be swallowed because ANOTHER shopper's write is still in flight,
  // and the "Added" feedback belongs to the shopper who tapped, not to whoever is selected when the answer arrives.
  const [added, setAdded] = useState<Record<string, string>>({})
  const inFlight = useRef(new Set<string>())
  const justAdded = added[d.shopper] ?? ''

  const scroller = useRef<HTMLElement>(null)
  const cartRef = useRef<HTMLElement>(null)
  const [cartVisible, setCartVisible] = useState(false)

  const cart = d.cartFor(d.shopper)
  const items = useMemo(() => (cart ? cartItems(cart) : []), [cart])
  const count = cartCount(items)
  const status = cart?.state.status || 'open'
  const locked = isDone(status)
  const busy = Object.entries(d.pending).some(([key, val]) => val && key.includes(`:${d.shopper}:`))
  const catalog = useMemo(() => (restrictedProductEnabled() ? CATALOG_WITH_RESTRICTED : CATALOG), [])
  const products = catalog.filter(p => category === CATEGORIES[0] || (category === 'Home' ? HOME_SKUS.includes(p.sku) : !HOME_SKUS.includes(p.sku)))

  useScrollReveal(scroller, [category, d.shopper])

  // the "view cart" pill is only useful while the cart is out of sight
  useEffect(() => {
    const host = scroller.current, target = cartRef.current
    if (!host || !target) return
    const io = new IntersectionObserver(([entry]) => setCartVisible(entry.isIntersecting), { root: host, threshold: 0.2 })
    io.observe(target)
    return () => io.disconnect()
  }, [])

  // the cart count bumps whenever it changes
  const [bump, setBump] = useState(0)
  const lastCount = useRef(count)
  useEffect(() => { if (count !== lastCount.current) { lastCount.current = count; setBump(b => b + 1) } }, [count])

  async function add(product: Product) {
    const who = d.shopper // captured now: the selected shopper can change while this write is in flight
    if (inFlight.current.has(who)) return // a second tap for the SAME shopper (their cart may still be being created)
    inFlight.current.add(who)
    try {
      if (await d.addToCart(who, product)) {
        setAdded(a => ({ ...a, [who]: product.sku }))
        setTimeout(() => setAdded(a => (a[who] === product.sku ? { ...a, [who]: '' } : a)), 1700)
      }
    } catch (error) {
      d.toast('bad', error instanceof Error ? error.message : 'The item could not be added.')
    } finally {
      inFlight.current.delete(who)
    }
  }

  const goToCart = () => cartRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  const stale = cart?.consistency.stale_possible
  const syncLabel = stale ? 'May be stale' : cart?.consistency.replication === 'REPLICATED' ? 'Synced' : 'Saved locally'

  return (
    <aside className="cx-left" ref={scroller} aria-label="Shopping app">
      <header className="sp-head" data-reveal>
        <div className="sp-eyebrow">The storefront</div>
        <h1>Everyday essentials<span>.</span></h1>
        <p>Shopping as <b>{d.shopper}</b>. Every tap is a real write to the live backend on the right.</p>
      </header>

      <div className="sp-shoppers" role="tablist" aria-label="Choose shopper" data-reveal>
        {SHOPPERS.map((who, i) => {
          const c = d.cartFor(who)
          const n = c ? cartCount(cartItems(c)) : 0
          return (
            <button key={who} role="tab" aria-selected={d.shopper === who} className={d.shopper === who ? 'selected' : ''} onClick={() => d.setShopper(who)}>
              <span className={`sp-avatar avatar-${i}`}>{who[0]}{n > 0 && <i>{n}</i>}</span>
              <span>{who}</span>
              <AllowanceMeter who={who} />
            </button>
          )
        })}
      </div>
      <RateNote />
      <div id="identity-slot" /> {/* empty on purpose: the identity panel mounts here when the identity lane is joined */}

      <div className="sp-filter" role="group" aria-label="Product category" data-reveal>
        {CATEGORIES.map(c => <button key={c} className={category === c ? 'selected' : ''} onClick={() => setCategory(c)}>{c}</button>)}
        <span>{products.length} items</span>
      </div>

      <ul className="sp-products" key={category}>
        {products.map((p, i) => (
          <li key={p.sku} className={`sp-product ${justAdded === p.sku ? 'just-added' : ''}`} data-reveal style={{ animationDelay: `${i * 70}ms` }}>
            <ProductPhoto sku={p.sku} className="sp-thumb" />
            <div className="sp-info">
              <h3>{p.name}{p.requires && <em className="sp-age" title="Adults only: the buyer has to prove they are over 18">18+</em>}</h3>
              <strong>{rupees(p.price)}</strong>
            </div>
            <button className="sp-add" aria-label={`Add ${p.name}`} disabled={busy} title={locked ? 'This order is finished. Adding an item starts a new cart.' : undefined} onClick={() => void add(p)}>
              <Icon name={d.pending[`add:${d.shopper}:${p.sku}`] ? 'refresh' : justAdded === p.sku ? 'check' : 'plus'} size={17} className={d.pending[`add:${d.shopper}:${p.sku}`] ? 'spin' : ''} />
              {justAdded === p.sku ? 'Added' : 'Add'}
            </button>
            {justAdded === p.sku && <span className="sp-plus" aria-hidden="true">+1</span>}
          </li>
        ))}
      </ul>
      <p className="sp-note"><Icon name="shield" size={15} /> A demo store. No real payments or orders.</p>

      <section className="sp-cart" id="cart" ref={cartRef} aria-label={`${d.shopper}'s cart`} data-reveal>
        <div className="sp-cart-title">
          <h2><Icon name="cart" size={22} />{d.shopper}’s cart <span className="sp-count" key={bump}>{count}</span></h2>
          {cart && <Chip tone={stale ? 'warn' : cart.consistency.replication === 'REPLICATED' ? 'ok' : 'muted'} icon={stale ? 'clock' : 'check'}>{syncLabel}</Chip>}
        </div>

        {items.length ? (
          <ul className="sp-cart-items">
            {items.map(item => (
              <li key={item.sku}>
                <ProductPhoto sku={item.sku} className="sp-cart-thumb" />
                <span>{item.name}<small>Quantity {item.qty}</small></span>
                <strong>{rupees(item.price * item.qty)}</strong>
                <button className="sp-remove" aria-label={`Remove one ${item.name}`} title="Remove one" disabled={locked || busy} onClick={() => void d.removeFromCart(d.shopper, item)}><Icon name="minus" size={16} /></button>
              </li>
            ))}
          </ul>
        ) : <p className="sp-empty">Your everyday starts here. Add your first item above.</p>}

        {locked && <p className="sp-done"><Icon name="check" size={16} />{STATUS_LABEL[status]?.label}. Add an item above to start a new cart.</p>}

        {cart?.conflicts.some(c => c.needs_review) && (
          <button className="sp-review" onClick={onReview}><Icon name="scale" size={17} />This cart has updates to review<Icon name="arrow" size={17} /></button>
        )}

        <div className="sp-total"><span>Total <small>{items.length ? STATUS_LABEL[status]?.label : 'Your cart is empty'}</small></span><strong>{rupees(cartTotal(items))}</strong></div>

        {cart && (
          <details className="sp-options">
            <summary><span><Icon name="truck" size={18} />{SPEEDS[cart.state.severity || 1]} delivery &amp; order options</span><Icon name="chevron" size={16} /></summary>
            <div>
              <label>Delivery speed
                <select aria-label="Delivery speed" disabled={locked || busy} value={cart.state.severity || 1} onChange={e => void d.writeField(d.shopper, 'severity', Number(e.target.value))}>
                  {Object.entries(SPEEDS).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
                </select>
              </label>
              <label>Fulfilment centre
                <select aria-label="Fulfilment centre" disabled={locked || busy} value={cart.state.assigned_team || ''} onChange={e => void d.writeField(d.shopper, 'assigned_team', e.target.value || null)}>
                  <option value="">Not assigned</option>
                  {CENTRES.map(c => <option key={c}>{c}</option>)}
                </select>
              </label>
              <div className="sp-option-buttons">
                <button className="btn" disabled={locked || busy || status !== 'open'} onClick={() => void d.writeField(d.shopper, 'status', 'assigned')}><Icon name="cart" size={16} />Check out</button>
                <button className="btn" disabled={locked || busy || status === 'resolved'} onClick={() => void d.writeField(d.shopper, 'status', 'resolved')}><Icon name="bolt" size={16} />Pay</button>
                <button className="btn btn--ghost" disabled={locked || busy} onClick={() => void d.writeField(d.shopper, 'status', 'cancelled')}>Cancel</button>
              </div>
            </div>
          </details>
        )}

        <div className="sp-order">
          <span><Icon name="lock" size={15} />{d.saved[d.shopper]?.queued ? 'Kept on this device' : d.viewRegion ? `Saved in ${NODE_NAME[d.viewRegion]}` : 'Waiting for a region'}</span>
          <button className="btn btn--primary" disabled={!items.length || locked || busy} onClick={() => void d.writeField(d.shopper, 'status', 'closed')}>
            {locked ? STATUS_LABEL[status]?.label : 'Place order'}<Icon name="arrow" size={18} />
          </button>
        </div>
        {cart && <button className="sp-history" onClick={() => onHistory(cart.report_id)}><Icon name="history" size={16} />View cart history</button>}
      </section>

      <button className={`sp-cart-pill ${count > 0 && !cartVisible ? 'show' : ''}`} onClick={goToCart} tabIndex={count > 0 && !cartVisible ? 0 : -1} aria-label={`Go to ${d.shopper}'s cart`}>
        <Icon name="cart" size={18} /><span>{count} {count === 1 ? 'item' : 'items'}</span><strong>{rupees(cartTotal(items))}</strong><span className="sp-pill-go">View cart<Icon name="chevron" size={15} /></span>
      </button>
    </aside>
  )
}
