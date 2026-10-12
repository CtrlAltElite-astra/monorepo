import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { addItemText, cartFromReport, findCart, parseItem, PRODUCTS } from './shop.js'

test('catalogue matches fixtures/carts.json', () => {
  const doc = JSON.parse(readFileSync(new URL('../../../fixtures/carts.json', import.meta.url), 'utf-8'))
  const want = Object.entries(doc.products).map(([sku, p]) => ({ sku, name: p.name, price: p.price }))
  assert.deepEqual([...PRODUCTS].sort((a, b) => a.sku.localeCompare(b.sku)), want.sort((a, b) => a.sku.localeCompare(b.sku)))
})

const report = (notes, state = {}) => ({
  report_id: 'R-1',
  state: { title: 'Alice', status: 'open', severity: 1, assigned_team: null, notes, ...state },
  conflicts: [], consistency: { replication: 'PARTIAL' },
})
const note = (id, sku, op, qty = 1) => ({ id, text: addItemText({ sku, owner: 'Alice', op, qty }) })

test('a retried add (same op) counts once', () => {
  const c = cartFromReport(report([note('mum:2', 'KURTA-01', 'op-a'), note('pun:1', 'KURTA-01', 'op-a')]))
  assert.equal(c.items.length, 1)
  assert.equal(c.items[0].qty, 1)
  assert.equal(c.total, 899)
})

test('different operations add up; totals are per line', () => {
  const c = cartFromReport(report([
    note('mum:2', 'KURTA-01', 'op-a'), note('mum:3', 'KURTA-01', 'op-b'), note('mum:4', 'CHAI-01', 'op-c', 2),
  ]))
  assert.equal(c.itemCount, 4)
  assert.equal(c.total, 899 * 2 + 349 * 2)
})

test('non-ADD notes are ignored and labels are relabelled, not recomputed', () => {
  const c = cartFromReport(report([{ id: 'x:1', text: 'hello' }], { status: 'closed', severity: 4, assigned_team: 'Chakan' }))
  assert.equal(c.items.length, 0)
  assert.equal(c.statusLabel, 'Order placed')
  assert.equal(c.speedLabel, 'Same-day')
  assert.equal(c.centre, 'Chakan')
})

test('conflicts keep the backend values and gain a shop label', () => {
  const r = report([])
  r.conflicts = [{ field: 'assigned_team', values: ['Bhiwandi', 'Chakan'], event_ids: ['mum:4', 'pun:2'] }]
  const c = cartFromReport(r)
  assert.equal(c.conflicts[0].label, 'Fulfilment centre')
  assert.deepEqual(c.conflicts[0].values, ['Bhiwandi', 'Chakan'])
})

test('v1 receipts are worded for the shop without changing the values', () => {
  const r = report([])
  r.conflicts = [
    { field: 'status', rule: 'CLOSE_VS_NEW_INFO', values: ['closed', '{}'], needs_review: true, flags: ['CLOSE_VS_NEW_INFO'] },
    { field: 'assigned_team', rule: 'EARLIEST_HLC', values: ['Bhiwandi', 'Chakan'], needs_review: true, flags: ['DOUBLE_DISPATCH'] },
    { field: 'severity', rule: 'HIGHEST', values: [5, 4], needs_review: false, flags: [] },
  ]
  const [a, b, c] = cartFromReport(r).conflicts
  assert.match(a.text, /Order placed while an item was added elsewhere: needs review/)
  assert.match(b.text, /Double fulfilment: Bhiwandi vs Chakan \(needs review\)/)
  assert.equal(c.text, 'Delivery speed: Priority vs Same-day')
})

test('findCart matches by owner; parseItem rejects junk', () => {
  assert.equal(findCart([report([])], 'Alice').id, 'R-1')
  assert.equal(findCart([report([])], 'Bob'), null)
  assert.equal(parseItem('not json'), null)
})
