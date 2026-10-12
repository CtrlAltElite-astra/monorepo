// Shop adapter (owner: Miti). Plan v1.1 section 4 and docs/shop-mapping.md.
//
// The backend stays domain-neutral (reports, severity, status, notes). This file is the ONLY place
// that speaks "cart". The UI renders what the API returns; nothing here recomputes status, priority
// or conflicts, it only relabels them.
// Catalogue from Appendix B. Mirrors fixtures/carts.json; shop.test.js fails if the two drift apart.
export const PRODUCTS = [
  { sku: 'SANDAL-01', name: 'Kolhapuri sandals', price: 1299 },
  { sku: 'LAMP-01', name: 'Desk lamp', price: 1099 },
  { sku: 'BOTTLE-01', name: 'Steel water bottle', price: 499 },
  { sku: 'BAG-01', name: 'Backpack', price: 1599 },
  { sku: 'KURTA-01', name: 'Cotton kurta', price: 899 },
  { sku: 'GIFT-500', name: 'Gift card', price: 500 },
  { sku: 'FCOFF-500', name: 'Filter coffee 500 g', price: 449 },
  { sku: 'CHAI-01', name: 'Masala chai box', price: 349 },
  { sku: 'EARBUD-01', name: 'Wireless earbuds', price: 1999 },
  { sku: 'MAT-01', name: 'Yoga mat', price: 799 },
]
export const SHOPPERS = ['Alice', 'Bob', 'Chitra', 'Dev', 'Esha']

export const SPEED_LABEL = { 1: 'Standard', 2: 'Plus', 3: 'Express', 4: 'Same-day', 5: 'Priority' }
export const STATUS_LABEL = {
  open: 'Shopping', assigned: 'Checking out', resolved: 'Paid', closed: 'Order placed', cancelled: 'Cancelled',
}
export const CENTRES = ['Bhiwandi', 'Chakan', 'Gurgaon']

// The checkout ladder the UI offers. The backend checks nothing here (write-time rules only).
export const NEXT_STATUS = { open: 'assigned', assigned: 'resolved', resolved: 'closed' }
export const NEXT_ACTION = { open: 'Start checkout', assigned: 'Pay', resolved: 'Place order' }

// Contract v1.1 adds the category "cart"; the UI hides the category either way.
export const CART_CATEGORY = 'cart'

export function newReportId() {
  return 'R-' + Array.from({ length: 8 }, () => Math.floor(Math.random() * 16).toString(16)).join('')
}

export function newOpId() {
  return 'op-' + Math.random().toString(16).slice(2, 10) + Date.now().toString(16)
}

export function addItemText({ sku, owner, op, qty = 1 }) {
  const p = PRODUCTS.find((x) => x.sku === sku)
  return JSON.stringify({ kind: 'ADD', sku, name: p.name, qty, price: p.price, user: owner, op })
}

export function parseItem(text) {
  try {
    const o = JSON.parse(text)
    return o && o.kind === 'ADD' && o.sku ? o : null
  } catch {
    return null
  }
}

const FIELD_LABEL = { severity: 'Delivery speed', assigned_team: 'Fulfilment centre', status: 'Order status',
  title: 'Cart owner', description: 'Delivery note', location: 'Delivery pin' }

export function conflictLabel(c) {
  if (c.rule === 'CLOSE_VS_NEW_INFO') return 'Order placed while an item was added elsewhere'
  if ((c.flags || []).includes('DOUBLE_DISPATCH')) return 'Double fulfilment'
  return FIELD_LABEL[c.field] || c.field
}

// One line for a cart card. The backend decided the value; this only words it.
export function conflictText(c) {
  if (c.rule === 'CLOSE_VS_NEW_INFO') return `${conflictLabel(c)}: needs review (ship with it / refund and re-order / keep)`
  const shown = (v) => (c.field === 'status' ? STATUS_LABEL[v] || v : c.field === 'severity' ? SPEED_LABEL[v] || v : v)
  const vals = (c.values || []).map(shown).join(' vs ')
  return `${conflictLabel(c)}: ${vals}${c.needs_review ? ' (needs review)' : ''}`
}

/**
 * Turn an API report view into a cart.
 * Quantity of a SKU = number of distinct operation ids among its ADD notes (times qty), so a
 * retried add never doubles an item. `op` comes from the note JSON until views expose op_id.
 */
export function cartFromReport(report) {
  const s = report.state || {}
  const bySku = new Map()
  for (const n of s.notes || []) {
    const it = parseItem(n.text)
    if (!it) continue
    const e = bySku.get(it.sku) || { sku: it.sku, name: it.name, price: it.price, ops: new Map() }
    e.ops.set(it.op || n.id, it.qty || 1)
    bySku.set(it.sku, e)
  }
  const items = [...bySku.values()].map((e) => {
    const qty = [...e.ops.values()].reduce((a, b) => a + b, 0)
    return { sku: e.sku, name: e.name, price: e.price, qty, line: qty * e.price }
  })
  return {
    id: report.report_id,
    live: report.live || {},
    owner: s.title,
    status: s.status,
    statusLabel: STATUS_LABEL[s.status] || String(s.status),
    speed: s.severity,
    speedLabel: SPEED_LABEL[s.severity] || String(s.severity),
    centre: s.assigned_team || null,
    items,
    itemCount: items.reduce((a, i) => a + i.qty, 0),
    total: items.reduce((a, i) => a + i.line, 0),
    conflicts: (report.conflicts || []).map((c) => ({ ...c, label: conflictLabel(c), text: conflictText(c) })),
    replication: report.consistency && report.consistency.replication,
    incomplete: !!report.incomplete,
  }
}

/** Find a shopper's cart among a node's reports. */
export function findCart(reports, owner) {
  const r = (reports || []).filter((x) => x.state && x.state.title === owner)
  return r.length ? cartFromReport(r[0]) : null
}

export const rupees = (n) => '₹' + Number(n || 0).toLocaleString('en-IN')
