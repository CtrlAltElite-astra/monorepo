import { afterEach, describe, expect, it, vi } from 'vitest'
import { handle } from '../src/sim/api'
import { eventHash, sign } from '../src/sim/crypto'
import type { EventEnvelope } from '../src/api/types'
import { addItem, admin, CART, converged, cut, newCart, officer, setup, status, supervisor } from './helpers'

afterEach(() => vi.useRealTimers())

const report = (w: ReturnType<typeof setup>['world'], r: 'mum' | 'pun' | 'del', id = CART) => handle(w, r, 'GET', `/v1/reports/${id}`, undefined, null).body

describe('replication and honest badges (A1)', () => {
  it('a write is LOCAL_ONLY, then PARTIAL, then REPLICATED, and every node converges', async () => {
    const { world, advance } = setup()
    newCart(world, 'mum')
    expect(report(world, 'mum').consistency.replication).toBe('LOCAL_ONLY')
    await advance(1000)
    expect(report(world, 'mum').consistency).toMatchObject({ replication: 'PARTIAL', confirmed_by: ['pun'], awaiting: ['del'] })
    await advance(2500)
    expect(report(world, 'mum').consistency).toMatchObject({ replication: 'REPLICATED', confirmed_by: ['del', 'pun'], awaiting: [], stale_possible: false })
    expect(converged(world)).toBe(true)
  })

  it('a retried write is one event: the replay answers 200 with the original event', () => {
    const { world } = setup()
    const first = newCart(world, 'mum')
    const again = newCart(world, 'mum')
    expect(first.status).toBe(201)
    expect(again.status).toBe(200)
    expect(again.headers).toEqual({ 'idempotent-replay': 'true' })
    expect(again.body.event.event_id).toBe(first.body.event.event_id)
    expect(world.node('mum').seq).toBe(1)
    expect(officer(world, 'mum', 'POST', '/v1/reports', { report_id: 'R-0000aaaa', op_id: `create-${CART}`, title: 'x', category: 'cart', severity: 1 }).body).toEqual({ error: 'op_id_reuse' })
  })

  it('a second CREATE for an existing cart with a new op_id is report_exists', () => {
    const { world } = setup()
    newCart(world, 'mum')
    expect(officer(world, 'mum', 'POST', '/v1/reports', { report_id: CART, op_id: 'other', title: 'x', category: 'cart', severity: 1 }).body).toEqual({ error: 'report_exists' })
  })

  it('every event is signed by its origin and verifies on every node', async () => {
    const { world, advance } = setup()
    const ev = newCart(world, 'mum').body.event as EventEnvelope
    await advance(3000)
    for (const r of ['mum', 'pun', 'del'] as const) {
      const h = handle(world, r, 'GET', `/v1/reports/${CART}/history`, undefined, null).body
      expect(h[0]).toMatchObject({ event_id: ev.event_id, sig_status: 'verified', carried: false })
    }
  })
})

describe('roles and errors', () => {
  it('officers may not resolve (A9) or use /admin; unknown keys are 401', () => {
    const { world } = setup()
    newCart(world, 'mum')
    expect(officer(world, 'mum', 'POST', `/v1/reports/${CART}/resolve`, { field: 'status', value: 'closed', candidates: ['mum:1'], reason: 'x' }).status).toBe(403)
    expect(officer(world, 'mum', 'POST', '/admin/links/pun', { mode: 'down' }).status).toBe(403)
    expect(handle(world, 'mum', 'POST', '/v1/reports', {}, null).status).toBe(401)
  })

  it('validation answers 422 and nothing is stored', () => {
    const { world } = setup()
    for (const bad of [{ title: '', category: 'cart', severity: 1 }, { title: 'x', category: 'nope', severity: 1 }, { title: 'x', category: 'cart', severity: 9 }, { title: 'x', category: 'cart', severity: 1, report_id: 'R-mum-1' }]) {
      expect(officer(world, 'mum', 'POST', '/v1/reports', bad).status).toBe(422)
    }
    expect(world.node('mum').events.size).toBe(0)
  })

  it('a stale expected is 409 with the live ids', () => {
    const { world } = setup()
    newCart(world, 'mum')
    expect(officer(world, 'mum', 'PATCH', `/v1/reports/${CART}`, { field: 'severity', value: 3, expected: ['mum:1'] }).status).toBe(200)
    const stale = officer(world, 'mum', 'PATCH', `/v1/reports/${CART}`, { field: 'severity', value: 4, expected: ['mum:1'] })
    expect(stale.status).toBe(409)
    expect(stale.body).toEqual({ error: 'stale_edit', live: ['mum:2'] })
  })
})

describe('partition, conflict, review and resolve (A4, A5, T4, T6, T7)', () => {
  it('both sides keep writing while cut, then converge to the same state with the safe values', async () => {
    const { world, advance } = setup({ joined: ['mum', 'pun'] })
    newCart(world, 'mum')
    await advance(2500)
    cut(world, 'mum', 'pun')
    expect(officer(world, 'mum', 'PATCH', `/v1/reports/${CART}`, { field: 'severity', value: 5 }).status).toBe(200)
    expect(officer(world, 'pun', 'PATCH', `/v1/reports/${CART}`, { field: 'severity', value: 4 }).status).toBe(200)
    expect(officer(world, 'mum', 'PATCH', `/v1/reports/${CART}`, { field: 'assigned_team', value: 'Bhiwandi' }).status).toBe(200)
    expect(officer(world, 'pun', 'PATCH', `/v1/reports/${CART}`, { field: 'assigned_team', value: 'Chakan' }).status).toBe(200)
    expect(officer(world, 'mum', 'PATCH', `/v1/reports/${CART}`, { field: 'status', value: 'closed' }).status).toBe(200)
    expect(officer(world, 'pun', 'POST', `/v1/reports/${CART}/notes`, { text: JSON.stringify({ kind: 'ADD', sku: 'GIFT-500', name: 'Gift card', qty: 1, price: 500, user: 'Alice' }) }).status).toBe(201)
    await advance(4000)
    expect(converged(world)).toBe(false) // nothing crossed while cut
    cut(world, 'mum', 'pun', 'up')
    await advance(4000)
    expect(converged(world)).toBe(true)
    const a = report(world, 'mum'), b = report(world, 'pun')
    expect(a.state).toEqual(b.state)
    expect(a.conflicts).toEqual(b.conflicts)
    expect(a.state).toMatchObject({ severity: 5, assigned_team: 'Bhiwandi', status: 'closed' })
    expect(a.conflicts.map((c: any) => c.field).sort()).toEqual(['assigned_team', 'severity', 'status'].filter((f) => f !== 'status').concat('status'))
    expect(a.conflicts.find((c: any) => c.field === 'status')).toMatchObject({ relationship: 'CLOSE_VS_NEW_INFO' })
    expect(a.conflicts.find((c: any) => c.field === 'assigned_team').flags).toEqual(['DOUBLE_DISPATCH'])
  })

  it('a supervisor resolves; a stale resolve is 409; the decision replicates', async () => {
    const { world, advance } = setup({ joined: ['mum', 'pun'] })
    newCart(world, 'mum')
    await advance(2500)
    cut(world, 'mum', 'pun')
    officer(world, 'mum', 'PATCH', `/v1/reports/${CART}`, { field: 'assigned_team', value: 'Bhiwandi' })
    officer(world, 'pun', 'PATCH', `/v1/reports/${CART}`, { field: 'assigned_team', value: 'Chakan' })
    cut(world, 'mum', 'pun', 'up')
    await advance(4000)
    const review = handle(world, 'mum', 'GET', '/v1/review', undefined, null).body
    expect(review).toHaveLength(1)
    const live = review[0].receipt.candidates.map((c: any) => c.event_id)
    const stale = supervisor(world, 'mum', 'POST', `/v1/reports/${CART}/resolve`, { field: 'assigned_team', value: 'Chakan', candidates: [live[0]], reason: 'x' })
    expect(stale.status).toBe(409)
    expect(stale.body.error).toBe('stale_edit')
    const done = supervisor(world, 'mum', 'POST', `/v1/reports/${CART}/resolve`, { field: 'assigned_team', value: 'Chakan', candidates: live, reason: 'closer to the customer' })
    expect(done.status).toBe(201)
    expect(done.body.event).toMatchObject({ type: 'RESOLVE', priority: 0, supersedes: expect.arrayContaining(live) })
    await advance(3000)
    expect(report(world, 'pun').state.assigned_team).toBe('Chakan')
    expect(handle(world, 'pun', 'GET', '/v1/review', undefined, null).body).toEqual([])
    const liveNow = report(world, 'mum').live.assigned_team // just the RESOLVE event now
    expect(liveNow).toEqual([done.body.event_id])
    expect(supervisor(world, 'mum', 'POST', `/v1/reports/${CART}/resolve`, { field: 'assigned_team', value: 'Chakan', candidates: liveNow, reason: 'x' }).body).toEqual({ error: 'no_conflict' })
  })

  it('CLOSE_VS_NEW_INFO: the order stays placed until a supervisor decides, then it clears', async () => {
    const { world, advance } = setup({ joined: ['mum', 'pun'] })
    newCart(world, 'mum')
    await advance(2500)
    cut(world, 'mum', 'pun')
    officer(world, 'mum', 'PATCH', `/v1/reports/${CART}`, { field: 'status', value: 'closed' })
    addItem(world, 'pun', CART, 'GIFT-500', 'op-gift')
    cut(world, 'mum', 'pun', 'up')
    await advance(4000)
    const [item] = handle(world, 'pun', 'GET', '/v1/review', undefined, null).body
    expect(item.receipt.relationship).toBe('CLOSE_VS_NEW_INFO')
    expect(report(world, 'pun').state.status).toBe('closed')
    const closeId = item.receipt.candidates.find((c: any) => c.kind === 'close').event_id
    expect(supervisor(world, 'pun', 'POST', `/v1/reports/${CART}/resolve`, { field: 'status', value: 'closed', candidates: [closeId], reason: 'ship with the new item' }).status).toBe(201)
    expect(handle(world, 'pun', 'GET', '/v1/review', undefined, null).body).toEqual([])
  })

  it('three nodes: a cut between two of them heals through the third (relay)', async () => {
    const { world, advance } = setup()
    newCart(world, 'mum')
    await advance(3000)
    cut(world, 'mum', 'pun')
    addItem(world, 'mum', CART, 'CHAI-01', 'op-chai')
    await advance(5000)
    expect(report(world, 'pun').state.notes).toHaveLength(1) // arrived via Delhi
    expect(converged(world)).toBe(true)
  })
})

describe('integrity: tampered and forged events are rejected, state unchanged (T10, T18)', () => {
  async function seeded() {
    const ctx = setup()
    const ev = newCart(ctx.world, 'mum').body.event as EventEnvelope
    return { ...ctx, ev }
  }

  it('an edited body fails the hash; a recomputed hash fails the signature; an unknown origin is refused', async () => {
    const { world, advance, ev } = await seeded()
    const before = status(world, 'pun')
    const edited = { ...ev, value: { ...ev.value, title: 'Mallory' } }
    const rehashed = { ...edited, hash: eventHash(edited) } // hash fixed, signature stale
    const ghost = { ...ev, event_id: 'ghost:1', origin: 'ghost', seq: 1 }
    const reasons = handle(world, 'pun', 'POST', '/v1/carry', { events: [edited, rehashed, { ...ghost, hash: eventHash(ghost) }] }, 'officer').body.results.map((r: any) => r.reason)
    expect(reasons).toEqual(['bad_hash', 'bad_sig', 'unknown_origin'])
    await advance(10)
    expect(world.node('pun').events.has(ev.event_id)).toBe(false)
    expect(status(world, 'pun').event_digest).toBe(before.event_digest)
    expect(status(world, 'pun').carry).toMatchObject({ received: 3, new: 0, rejected: 3 })
    expect(status(world, 'pun').quarantined).toBe(3)
  })

  it('a duplicate is "dup" and a good event is "new" and marked carried in history', async () => {
    const { world, ev } = await seeded()
    const first = handle(world, 'pun', 'POST', '/v1/carry', { events: [ev] }, 'officer').body.results[0]
    const second = handle(world, 'pun', 'POST', '/v1/carry', { events: [ev] }, 'officer').body.results[0]
    expect([first.result, second.result]).toEqual(['new', 'dup'])
    expect(handle(world, 'pun', 'GET', `/v1/reports/${CART}/history`, undefined, null).body[0].carried).toBe(true)
  })

  it('an event signed with the wrong key is rejected', async () => {
    const { world, ev } = await seeded()
    const forged = { ...ev, sig: sign('pun', ev.hash) } // pun signs mum's event
    expect(handle(world, 'del', 'POST', '/v1/carry', { events: [forged] }, 'officer').body.results[0].reason).toBe('bad_sig')
  })

  it('carry is bounded and /v1/events/has reports what is missing', () => {
    const { world } = setup()
    expect(handle(world, 'pun', 'POST', '/v1/carry', { events: new Array(201).fill({}) }, 'officer').status).toBe(422)
    newCart(world, 'mum')
    expect(handle(world, 'pun', 'POST', '/v1/events/has', { ids: ['mum:1', 'mum:2'] }, null).body).toEqual({ missing: ['mum:1', 'mum:2'] })
    expect(handle(world, 'mum', 'POST', '/v1/events/has', { ids: ['mum:1', 'mum:2'] }, null).body).toEqual({ missing: ['mum:2'] })
  })
})

describe('the degradation ladder and data at risk (T21, T22, A3, A10)', () => {
  it('slow -> CONSTRAINED; cut -> ISOLATED; heal -> RECOVERING -> HEALTHY', async () => {
    const { world, advance } = setup({ joined: ['mum', 'pun'] })
    newCart(world, 'mum')
    await advance(2500)
    expect(world.level('mum')).toBe('HEALTHY')
    cut(world, 'mum', 'pun', 'slow', { rate_bps: 4000, delay_ms: 100 })
    expect(world.level('mum')).toBe('CONSTRAINED')
    cut(world, 'mum', 'pun', 'down')
    await advance(2000)
    expect(world.level('mum')).toBe('ISOLATED')
    expect(world.level('pun')).toBe('ISOLATED')
    cut(world, 'mum', 'pun', 'up')
    await advance(300)
    expect(world.level('mum')).toBe('RECOVERING')
    await advance(4000)
    expect(world.level('mum')).toBe('HEALTHY')
    expect(world.node('mum').lastHeal).toMatchObject({ eventsReceived: 0 })
  })

  it('data at risk: five events written while cut are all unreplicated, and none after the heal', async () => {
    const { world, advance } = setup({ joined: ['mum', 'pun'] })
    newCart(world, 'mum')
    await advance(2500)
    expect(status(world, 'mum').risk.unreplicated).toBe(0)
    cut(world, 'mum', 'pun')
    for (let i = 0; i < 5; i++) addItem(world, 'mum', CART, 'CHAI-01', `op-${i}`)
    await advance(2000)
    expect(status(world, 'mum').risk).toMatchObject({ unreplicated: 5, critical_unreplicated: 0 })
    cut(world, 'mum', 'pun', 'up')
    await advance(5000)
    expect(status(world, 'mum').risk).toMatchObject({ unreplicated: 0, oldest_age_ms: 0 })
  })

  it('a node that restarts replays its log: RECOVERING, then HEALTHY with the same state and no duplicates (A3)', async () => {
    const { world, chaos, advance } = setup()
    newCart(world, 'mum')
    addItem(world, 'mum', CART, 'CHAI-01', 'op-1')
    await advance(3000)
    await chaos.crash('mum')
    addItem(world, 'pun', CART, 'MAT-01', 'op-2')
    await advance(2500)
    expect(world.level('pun')).toBe('ISOLATED')
    await chaos.start('mum')
    await advance(1000)
    expect(world.level('mum')).toBe('RECOVERING')
    await advance(5000)
    expect(world.level('mum')).toBe('HEALTHY')
    expect(converged(world)).toBe(true)
    expect(report(world, 'mum').state.notes).toHaveLength(2)
  })

  it('Delhi joins empty and converges (A10)', async () => {
    const { world, chaos, advance } = setup({ joined: ['mum', 'pun'] })
    newCart(world, 'mum')
    await advance(2000) // Pune must hold the cart before it can take an item for it
    expect(addItem(world, 'pun', CART, 'CHAI-01', 'op-1').status).toBe(201)
    await advance(3000)
    await chaos.start('del')
    expect(world.node('del').events.size).toBe(0)
    await advance(7000)
    expect(converged(world)).toBe(true)
    expect(world.joined).toEqual(['mum', 'pun', 'del'])
    expect(report(world, 'del').state.notes).toHaveLength(1)
    expect(world.level('del')).toBe('HEALTHY')
  })
})

describe('priority-first replication on a throttled link (T14)', () => {
  it('a P0 payment overtakes a backlog of bulk notes', async () => {
    const { world, advance } = setup({ joined: ['mum', 'pun'] })
    newCart(world, 'mum')
    await advance(2500)
    cut(world, 'mum', 'pun', 'slow', { rate_bps: 2000, delay_ms: 100 })
    const arrived = new Map<string, number>()
    const t0 = Date.now()
    for (let i = 0; i < 30; i++) addItem(world, 'mum', CART, 'CHAI-01', `bulk-${i}`)
    officer(world, 'mum', 'PATCH', `/v1/reports/${CART}`, { field: 'status', value: 'resolved' }) // paid: P0
    const pay = world.node('mum').events.get('mum:32')!
    expect(pay.priority).toBe(0)
    for (let step = 0; step < 120; step++) {
      await advance(500)
      for (const id of world.node('pun').events.keys()) if (!arrived.has(id)) arrived.set(id, Date.now() - t0)
      if (arrived.has('mum:32') && [...arrived.keys()].filter((k) => k !== 'mum:32').length >= 10) break
    }
    const notes = [...arrived.entries()].filter(([id]) => id.startsWith('mum:') && id !== 'mum:32' && Number(id.split(':')[1]) > 1).map(([, ms]) => ms).sort((a, b) => a - b)
    expect(arrived.has('mum:32')).toBe(true)
    expect(notes.length).toBeGreaterThanOrEqual(10)
    expect(arrived.get('mum:32')!).toBeLessThan(notes[Math.floor(notes.length / 2)]) // P0 delay below the P2 median
    expect(arrived.get('mum:32')!).toBeLessThan(5000)
  })
})

describe('admin and metrics', () => {
  it('burst writes bulk notes, links validate their mode, metrics report', () => {
    const { world } = setup({ joined: ['mum', 'pun'] })
    newCart(world, 'mum')
    expect(admin(world, 'mum', 'POST', '/admin/burst', { n: 50, report_id: CART }).body).toEqual({ accepted: 50 })
    expect(world.node('mum').events.size).toBe(51)
    expect(admin(world, 'mum', 'POST', '/admin/links/pun', { mode: 'wobbly' }).status).toBe(422)
    expect(admin(world, 'mum', 'POST', '/admin/links/xxx', { mode: 'down' }).status).toBe(404)
    const m = handle(world, 'mum', 'GET', '/v1/metrics', undefined, null).body
    expect(m.writes).toBe(51)
    expect(m.commit_ms.p50).toBeGreaterThan(0)
  })
})
