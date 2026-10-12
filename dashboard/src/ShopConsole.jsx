import { useEffect, useMemo, useRef, useState } from 'react'
import { API_KEY, AUTH_ENABLED, FIXTURES, REGIONS, crashRegion, setPairLink, startRegion } from './api.js'
import { SyncShieldClient } from './sdk/syncshieldClient.js'
import { useAuth } from './auth/AuthContext.jsx'
import AuthPanel from './auth/AuthPanel.jsx'
import Badge from './components/Badge.jsx'
import { ProveIt, TamperPanel } from './components/ProofTools.jsx'
import {
  CART_CATEGORY, NEXT_ACTION, NEXT_STATUS, PRODUCTS, SHOPPERS, addItemText, findCart, newOpId, newReportId, rupees,
} from './domain/shop.js'

const NODE_COLORS = ['#0f766e', '#7c3aed', '#c2410c', '#1d4ed8']
const colorOf = (id) => NODE_COLORS[Math.max(0, REGIONS.findIndex((r) => r.id === id)) % NODE_COLORS.length]

// ---- small pieces ---------------------------------------------------------
function HoldButton({ label, onConfirm, disabled }) {
  const [held, setHeld] = useState(false)
  const timer = useRef(null)
  const down = () => {
    if (disabled) return
    setHeld(true)
    timer.current = setTimeout(() => { setHeld(false); onConfirm() }, 800)
  }
  const up = () => { clearTimeout(timer.current); setHeld(false) }
  return (
    <button className={`cut hold ${held ? 'holding' : ''}`} disabled={disabled}
            onMouseDown={down} onMouseUp={up} onMouseLeave={up} onTouchStart={down} onTouchEnd={up}
            title="Hold for 0.8 s to confirm">
      {held ? 'keep holding…' : label}
    </button>
  )
}

function Counter({ label, value, tone, hint }) {
  return (
    <div className={`counter ${tone || ''}`} title={hint}>
      <div className="counter-value">{value}</div>
      <div className="counter-label">{label}</div>
    </div>
  )
}

function Timeline({ items }) {
  const last = items.slice(-40)
  return (
    <div className="timeline" aria-label="request timeline">
      {!last.length && <span className="muted">no requests yet</span>}
      {last.map((t, i) => (
        <span key={i} className={`dot ${t.failover ? 'failover' : ''} ${t.queued ? 'queued' : ''} ${t.status >= 400 ? 'answer' : ''}`}
              style={{ background: t.node ? colorOf(t.node) : '#6b7280' }}
              title={`${t.method} ${t.path}\n${t.node || 'no node answered'} · ${t.totalMs} ms · tried ${t.tried}${t.failover ? ' · FAILOVER' : ''} · HTTP ${t.status}`} />
      ))}
    </div>
  )
}

// ---- main -------------------------------------------------------------------
export default function ShopConsole({ snaps, onRefresh }) {
  const sdkRef = useRef(null)
  const cartIds = useRef({})            // shopper -> report id (learned from our own creates)
  const creating = useRef({})           // shopper -> in-flight create promise
  const [view, setView] = useState(null)
  const [proof, setProof] = useState(null)
  const auth = useAuth()
  const [shopperState, setShopperState] = useState(SHOPPERS[0])
  // Auth mode: the shopper IS the signed-in active persona (a real session), never a free choice of label.
  const shopper = AUTH_ENABLED ? (auth.active ?? '') : shopperState
  const setShopper = AUTH_ENABLED ? auth.setActive : setShopperState
  const shopperNames = AUTH_ENABLED ? auth.names : SHOPPERS
  const [notice, setNotice] = useState('')
  const [bot, setBot] = useState(false)
  const [busy, setBusy] = useState('')

  useEffect(() => {
    if (FIXTURES) return undefined
    const sdk = new SyncShieldClient(REGIONS, {
      apiKey: API_KEY,
      // auth mode: bearer sessions only, chosen per call (persona); no API key is ever sent
      getAuthHeader: AUTH_ENABLED
        ? (persona) => { const t = auth.tokenFor(persona); return t ? { Authorization: `Bearer ${t}` } : {} }
        : null,
    })
    sdkRef.current = sdk
    const off = sdk.subscribe(setView)
    sdk.start()
    const t = setInterval(async () => setProof(await sdk.presentOnSurvivors()), 2000)
    return () => { off(); sdk.stop(); clearInterval(t) }
  }, [])

  const snapOf = (id) => snaps.find((s) => s.id === id)
  const sdk = sdkRef.current

  // The cart of a shopper as each node sees it (from the node's own API, never recomputed).
  const cartsByNode = useMemo(() => Object.fromEntries(
    REGIONS.map((r) => [r.id, snapOf(r.id)?.ok ? findCart(snapOf(r.id).reports, shopper) : undefined]),
  ), [snaps, shopper])  // eslint-disable-line react-hooks/exhaustive-deps

  const knownCartId = (owner) => {
    if (cartIds.current[owner]) return cartIds.current[owner]
    for (const r of REGIONS) {
      const c = snapOf(r.id)?.ok ? findCart(snapOf(r.id).reports, owner) : null
      if (c) return c.id
    }
    return null
  }

  // Auth mode: ask the region for THIS persona's own carts (the list is already filtered by the server).
  async function ownCartId(owner) {
    const r = await sdk.call('GET', '/v1/reports', undefined, { persona: owner })
    if (!r.ok || !Array.isArray(r.body)) return null
    const mine = r.body.find((x) => x.state && x.state.title === owner)
    return mine ? mine.report_id : null
  }

  async function ensureCart(owner) {
    if (AUTH_ENABLED && !cartIds.current[owner]) {
      const found = await ownCartId(owner)
      if (found) cartIds.current[owner] = found
    }
    const known = knownCartId(owner)
    if (known) return known
    if (!creating.current[owner]) {
      // client-chosen report id + op_id: a retried create (failover) is applied once
      creating.current[owner] = sdk.call('POST', '/v1/reports', {
        title: owner, category: CART_CATEGORY, severity: 1, report_id: newReportId(), op_id: newOpId(),
      }, { persona: owner })
        .then((r) => {
          const id = r.body?.report?.report_id
          if (r.ok && id) cartIds.current[owner] = id
          return id || null
        })
        .finally(() => { delete creating.current[owner] })
    }
    return creating.current[owner]
  }

  async function addToCart(owner, sku) {
    if (!sdk) return
    const id = await ensureCart(owner)
    if (!id) { setNotice('Could not create the cart on any node. Saved on this device and will retry.'); return }
    const op = newOpId()
    const out = await sdk.call('POST', `/v1/reports/${id}/notes`, { text: addItemText({ sku, owner, op }), op_id: op }, { persona: owner })
    if (!out.ok) {
      // a permanent answer (401 / 403 / 404): shown as what it is, never counted as a successful add
      setNotice(`${owner}: could not add the item (${out.body?.detail?.code || out.body?.detail || `HTTP ${out.status || 'no node answered'}`}).`)
    }
    onRefresh()
  }

  async function advance(cart) {
    const next = NEXT_STATUS[cart.status]
    if (!next || !sdk) return
    const r = await sdk.call('PATCH', `/v1/reports/${cart.id}`, {
      field: 'status', value: next, expected: cart.live?.status,
    }, { persona: shopper })
    if (r.status === 409) setNotice('The cart changed on another node while you were looking. It was refreshed: check it and try again.')
    else if (!r.ok) setNotice(`Could not update the cart (HTTP ${r.status || 'no node answered'}).`)
    else setNotice('')
    onRefresh()
  }

  // auto-shoppers
  useEffect(() => {
    if (!bot || !sdk) return undefined
    const t = setInterval(() => {
      // auth mode: only members who really hold a session shop (no bypass for the bot)
      const pool = AUTH_ENABLED ? auth.live.filter((s) => s.role === 'officer').map((s) => s.persona) : SHOPPERS
      if (!pool.length) return
      const who = pool[Math.floor(Math.random() * pool.length)]
      const p = PRODUCTS[Math.floor(Math.random() * PRODUCTS.length)]
      addToCart(who, p.sku)
    }, 1200)
    return () => clearInterval(t)
  }, [bot, sdk, snaps])  // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (label, fn) => {
    setBusy(label)
    try { await fn() } catch (e) {
      const hint = e.status === 401 ? (AUTH_ENABLED ? ' (set the chaos agent key under "Judge tools")' : ' (check VITE_ADMIN_KEY)') : ''
      setNotice(`${label} failed: ${e.message}${hint}`)
    }
    setBusy('')
    onRefresh()
    sdk && sdk.probeAll()
  }

  if (FIXTURES) {
    return <div className="banner warn">The shop console needs live nodes (it fails over between them). Turn off VITE_FIXTURES.</div>
  }
  if (!view) return <div className="muted">starting…</div>

  const c = view.counters
  const myCart = cartsByNode[view.serving] ?? Object.values(cartsByNode).find(Boolean) ?? null
  const pairs = REGIONS.flatMap((a, i) => REGIONS.slice(i + 1).map((b) => [a, b]))
  // either side's own view of the link; if both are reachable and disagree, say so
  const linkState = (a, b) => {
    const x = snapOf(a.id)?.status?.peers?.[b.id]?.link
    const y = snapOf(b.id)?.status?.peers?.[a.id]?.link
    if (x && y && x !== y) return 'half cut'
    return x || y || 'unknown'
  }
  const carryUnsupported = view.nodes.some((n) => n.carrySupported === false)

  return (
    <div className="shop">
      {notice && <div className="banner warn" onClick={() => setNotice('')}>{notice} <span className="muted">(click to dismiss)</span></div>}
      <AuthPanel />

      <div className="shop-grid">
        {/* ---------- storefront ---------- */}
        <section className="panel">
          <h2>Storefront</h2>
          <div className="row-gap">
            <label>Shopper&nbsp;
              <select value={shopper} onChange={(e) => setShopper(e.target.value)}>
                {shopperNames.map((s) => <option key={s}>{s}</option>)}
              </select>
            </label>
            <label className="check"><input type="checkbox" checked={bot} onChange={(e) => setBot(e.target.checked)} /> auto-shoppers</label>
            <span className="muted">requests go to <b style={{ color: colorOf(view.serving || view.preferred) }}>{view.serving || view.preferred}</b></span>
          </div>
          <div className="products">
            {PRODUCTS.map((p) => (
              <button key={p.sku} className="product" onClick={() => addToCart(shopper, p.sku)}>
                <span>{p.name}</span><span className="muted">{rupees(p.price)}</span><span className="add">Add</span>
              </button>
            ))}
          </div>
          <h3>{shopper}'s cart <span className="muted">(as seen by {view.serving || 'a node'})</span></h3>
          {!myCart && <div className="muted">empty: add something</div>}
          {myCart && (
            <div className="cart">
              <div className="row">
                <span><Badge value={myCart.statusLabel} /> <span className="chip">{myCart.speedLabel}</span>
                  {myCart.centre && <span className="chip">{myCart.centre}</span>}</span>
                <span><b>{rupees(myCart.total)}</b></span>
              </div>
              <ul>
                {myCart.items.map((i) => <li key={i.sku}>{i.qty} x {i.name} <span className="muted">{rupees(i.line)}</span></li>)}
                {!myCart.items.length && <li className="muted">no items yet</li>}
              </ul>
              {myCart.conflicts.map((cf) => (
                <div key={cf.field + cf.rule} className="chip warn">{cf.text}</div>
              ))}
              {NEXT_STATUS[myCart.status] && <button onClick={() => advance(myCart)}>{NEXT_ACTION[myCart.status]}</button>}
            </div>
          )}
        </section>

        {/* ---------- judge panel ---------- */}
        <section className="panel">
          <h2>Judge panel</h2>
          <div className="counters">
            <Counter label="errors shown to shoppers" value={c.errorsShown} tone={c.errorsShown ? 'bad' : 'good'}
                     hint="counted only when no node answered at all" />
            <Counter label="writes acknowledged" value={c.acknowledged} />
            <Counter label="present on survivors" value={proof ? proof.present : '…'}
                     tone={proof && proof.missing.length === 0 ? 'good' : 'bad'}
                     hint={proof?.estimated ? 'estimated from node heads (events/has not available on this backend)' : 'confirmed by /v1/events/has'} />
            <Counter label="lost" value={proof ? proof.missing.length : '…'} tone={proof && proof.missing.length ? 'bad' : 'good'}
                     hint={proof ? proof.missing.join(', ') : ''} />
            <Counter label="failovers" value={c.failovers} />
            <Counter label="carried (new / dup)" value={`${c.carriedNew} / ${c.carriedDup}`} hint={`rejected: ${c.carriedRejected}`} />
          </div>
          {carryUnsupported && <div className="muted">Carry is off: this backend has no /v1/carry yet, so an unreplicated event dies with its node. The counters above show that honestly.</div>}
          {proof?.estimated && <div className="muted">"present on survivors" is estimated from node heads: this backend has no /v1/events/has yet.</div>}
          <h3>Nodes</h3>
          <div className="nodes">
            {REGIONS.map((r) => {
              const n = view.nodes.find((x) => x.id === r.id)
              const st = snapOf(r.id)?.status
              return (
                <div key={r.id} className={`node ${n.up ? '' : 'down'}`} style={{ borderTopColor: colorOf(r.id) }}>
                  <div className="row"><b>{r.id.toUpperCase()}</b>{n.up ? <Badge value={st?.level || n.level || 'HEALTHY'} /> : <span className="chip">UNKNOWN</span>}</div>
                  <div className="muted">{n.up ? `${n.latencyMs ?? '?'} ms${view.serving === r.id ? ' · serving' : ''}` : (n.lastError || 'unreachable')}</div>
                  {st && <div className="muted">heads <code>{JSON.stringify(st.heads)}</code></div>}
                  {st?.risk && <div className="muted">at risk <code>{st.risk.unreplicated}</code> · critical <code>{st.risk.critical_unreplicated}</code></div>}
                  <div className="node-actions">
                    <HoldButton label="Crash" disabled={!!busy || !n.up} onConfirm={() => act(`crash ${r.id}`, () => crashRegion(r.id))} />
                    <button disabled={!!busy || n.up} onClick={() => act(`start ${r.id}`, () => startRegion(r.id))}>Restart</button>
                  </div>
                </div>
              )
            })}
          </div>
          <h3>Request timeline <span className="muted">(dot colour = node that answered; ring = failover)</span></h3>
          <Timeline items={view.timeline} />
          <h3>Outbox <span className="chip">{view.outbox.length}</span> <span className="muted">signed events this browser still holds</span></h3>
          <div className="outbox">
            {!view.outbox.length && <span className="muted">empty: every acknowledged event is confirmed by a peer</span>}
            {view.outbox.slice(-8).map((e) => <code key={e.event_id} className="chip">{e.event_id}</code>)}
            {c.queued > 0 && <span className="chip bad">{view.pending} queued on device</span>}
          </div>
        </section>
      </div>

      {/* ---------- carts per node + network ---------- */}
      <div className="shop-grid">
        <section className="panel">
          <h2>{shopper}'s cart on each node</h2>
          <div className="nodes">
            {REGIONS.map((r) => {
              const cart = cartsByNode[r.id]
              const ok = snapOf(r.id)?.ok
              return (
                <div key={r.id} className="node" style={{ borderTopColor: colorOf(r.id) }}>
                  <b>{r.id.toUpperCase()}</b>
                  {!ok && <div className="chip">UNKNOWN</div>}
                  {ok && !cart && <div className="muted">no cart here yet</div>}
                  {cart && (
                    <>
                      <div>{cart.itemCount} items · <b>{rupees(cart.total)}</b></div>
                      <div><Badge value={cart.replication || 'LOCAL_ONLY'} /> <span className="chip">{cart.statusLabel}</span></div>
                      {cart.conflicts.map((cf) => <div key={cf.field + cf.rule} className="chip warn">{cf.text}</div>)}
                    </>
                  )}
                </div>
              )
            })}
          </div>
        </section>

        <section className="panel">
          <h2>Network</h2>
          <table>
            <thead><tr><th>link</th><th>state</th><th></th></tr></thead>
            <tbody>
              {pairs.map(([a, b]) => (
                <tr key={a.id + b.id}>
                  <td>{a.id} ↔ {b.id}</td>
                  <td><Badge value={linkState(a, b)} /></td>
                  <td className="node-actions">
                    <button className="cut" disabled={!!busy} onClick={() => act('cut', () => setPairLink(a, b, 'down'))}>Cut</button>
                    <button disabled={!!busy} onClick={() => act('slow', () => setPairLink(a, b, 'slow', { delay_ms: 500, rate_bps: 2000 }))}>Slow</button>
                    <button className="heal" disabled={!!busy} onClick={() => act('heal', () => setPairLink(a, b, 'up'))}>Heal</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="muted">Cut and Heal change both directions. Slow = 500 ms delay, 2 kB/s.</div>
        </section>
      </div>

      <div className="shop-grid">
        <ProveIt regions={REGIONS} onDone={onRefresh} />
        <TamperPanel regions={REGIONS} onDone={onRefresh} />
      </div>
    </div>
  )
}
