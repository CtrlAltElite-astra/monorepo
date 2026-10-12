// The simulator implements contract v1.2 (sections 8.1 to 8.4), so the console can be built and shown without lane D's backend.
// These tests are the simulator's acceptance: they check the contract's words, not the simulator's internals.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NetworkError } from '../src/api/types'
import { handle } from '../src/sim/api'
import { COMMIT_FIXED_MS, POWER_BATCH, serviceMs } from '../src/sim/world'
import { admin, CART, converged, newCart, officer, setup, settle, status } from './helpers'

afterEach(() => vi.useRealTimers())

type Ctx = ReturnType<typeof setup>
const note = (world: Ctx['world'], who: string | undefined, op: string, region: 'mum' | 'pun' | 'del' | 'blr' = 'mum') =>
  handle(world, region, 'POST', `/v1/reports/${CART}/notes`, { text: JSON.stringify({ kind: 'ADD', sku: 'CHAI-01', name: 'Masala chai box', qty: 1, price: 349, user: who ?? 'x' }), op_id: op }, 'officer', who)
const config = (world: Ctx['world'], body: object, region: 'mum' | 'pun' | 'del' | 'blr' = 'mum') => admin(world, region, 'POST', '/admin/config', body)
const left = (r: { headers?: Record<string, string> }) => Number(r.headers?.['x-ratelimit-remaining'])

describe('the per-shopper limit (contract 8.3)', () => {
  it('is off by default: no headers, no refusals, whatever the pace; the status says so', () => {
    const { world } = setup()
    newCart(world, 'mum')
    const answers = Array.from({ length: 30 }, (_, i) => note(world, 'bob', `op-${i}`))
    expect(answers.every((r) => r.status === 201 && !('x-ratelimit-limit' in (r.headers ?? {})) && !('x-ratelimit-remaining' in (r.headers ?? {})))).toBe(true)
    expect(status(world, 'mum').rate).toEqual({ actor_burst: 0, actor_refill_per_s: 1, region_limit_per_s: 0, refused_actor: 0, refused_region: 0 })
  })

  it('Bob is refused after his burst while Alice is accepted, and only Bob\'s meter empties', () => {
    const { world } = setup()
    newCart(world, 'mum')
    expect(config(world, { rate_actor_burst: 3, rate_actor_refill_per_s: 1 }).status).toBe(200)
    const bob = [1, 2, 3].map((i) => note(world, 'bob', `b${i}`))
    expect(bob.map((r) => r.status)).toEqual([201, 201, 201])
    expect(bob.map(left)).toEqual([2, 1, 0])
    expect(bob.every((r) => r.headers!['x-ratelimit-limit'] === '3')).toBe(true)

    const refused = note(world, 'bob', 'b4')
    expect(refused.status).toBe(429)
    expect(refused.body).toEqual({ error: 'rate_limited', scope: 'actor', retry_after_ms: 1000 }) // contract 8.3
    expect(refused.headers).toMatchObject({ 'retry-after': '1', 'x-ratelimit-limit': '3', 'x-ratelimit-remaining': '0' })

    const alice = note(world, 'alice', 'a1')
    expect(alice.status).toBe(201)
    expect(left(alice)).toBe(2) // Alice's allowance was never touched
    expect(world.node('mum').seq).toBe(1 + 3 + 1) // the refused write stored nothing and used no sequence number
    expect(status(world, 'mum').rate).toMatchObject({ actor_burst: 3, refused_actor: 1 })
  })

  it('the bucket refills with time, never beyond the burst, and a refusal is free', async () => {
    const { world, advance } = setup()
    newCart(world, 'mum')
    config(world, { rate_actor_burst: 2, rate_actor_refill_per_s: 2 })
    note(world, 'bob', 'b1'); note(world, 'bob', 'b2')
    for (let i = 0; i < 20; i++) expect(note(world, 'bob', `spam-${i}`).status).toBe(429) // hammering while empty builds no debt
    expect(note(world, 'bob', 'late').body.retry_after_ms).toBeLessThanOrEqual(500)
    await advance(500)
    expect(note(world, 'bob', 'b3').status).toBe(201) // one token is back after half a second at 2 per second
    expect(note(world, 'bob', 'b4').status).toBe(429)
    await advance(60_000)
    expect([note(world, 'bob', 'b5'), note(world, 'bob', 'b6')].map(left)).toEqual([1, 0]) // a long break fills the bucket, not more
  })

  it('a replay is answered before the limiter: never refused, never counted', () => {
    const { world } = setup()
    newCart(world, 'mum')
    config(world, { rate_actor_burst: 2, rate_actor_refill_per_s: 1 })
    expect(left(note(world, 'bob', 'same'))).toBe(1)
    const again = Array.from({ length: 6 }, () => note(world, 'bob', 'same'))
    expect(again.every((r) => r.status === 200 && r.headers!['idempotent-replay'] === 'true' && left(r) === 1)).toBe(true)
    expect(left(note(world, 'bob', 'other'))).toBe(0) // the second token was still there
    expect(note(world, 'bob', 'third').status).toBe(429)
    expect(note(world, 'bob', 'same').status).toBe(200) // and a retry is still answered with the bucket empty
    expect(handle(world, 'mum', 'POST', `/v1/reports/${CART}/notes`, { text: 'different', op_id: 'same' }, 'officer', 'bob').body).toEqual({ error: 'op_id_reuse' })
  })

  it('reads are never limited', () => {
    const { world } = setup()
    newCart(world, 'mum')
    config(world, { rate_actor_burst: 1, rate_actor_refill_per_s: 1 })
    note(world, 'bob', 'b1')
    expect(note(world, 'bob', 'b2').status).toBe(429)
    for (let i = 0; i < 50; i++) for (const path of ['/v1/reports', `/v1/reports/${CART}`, '/v1/status', '/v1/review', '/healthz']) {
      const r = handle(world, 'mum', 'GET', path, undefined, null, 'bob')
      expect(r.status).toBe(200)
      expect(r.headers).not.toHaveProperty('x-ratelimit-remaining')
    }
  })

  it('a write refused for its own reason was charged, and still says what is left', () => {
    const { world } = setup()
    newCart(world, 'mum')
    config(world, { rate_actor_burst: 3, rate_actor_refill_per_s: 1 })
    const stale = handle(world, 'mum', 'PATCH', `/v1/reports/${CART}`, { field: 'severity', value: 2, expected: ['mum:99'] }, 'officer', 'bob')
    expect(stale.status).toBe(409)
    expect(left(stale)).toBe(2)
    const missing = handle(world, 'mum', 'POST', '/v1/reports/R-00000000/notes', { text: 'x' }, 'officer', 'bob')
    expect(missing.status).toBe(404)
    expect(left(missing)).toBe(1)
  })

  it('validation and authentication errors never reach the limiter', () => {
    const { world } = setup()
    config(world, { rate_actor_burst: 1, rate_actor_refill_per_s: 1 })
    for (let i = 0; i < 5; i++) {
      expect(handle(world, 'mum', 'POST', '/v1/reports', { title: '', category: 'cart', severity: 1 }, 'officer', 'bob').status).toBe(422)
      expect(handle(world, 'mum', 'POST', '/v1/reports', { title: 'x', category: 'cart', severity: 1 }, null, 'bob').status).toBe(401)
      expect(handle(world, 'mum', 'POST', '/v1/reports', { title: 'x', category: 'cart', severity: 1 }, 'admin', 'bob').status).toBe(403)
    }
    expect(newCart(world, 'mum').status).toBe(201) // the one token is still there (this create has no actor: the role's own account)
    expect(handle(world, 'mum', 'POST', '/v1/reports', { title: 'bob', category: 'cart', severity: 1 }, 'officer', 'bob').status).toBe(201)
  })

  it('each region keeps its own buckets: a shopper who moves starts with a full allowance there', () => {
    const { world } = setup()
    newCart(world, 'mum')
    config(world, { rate_actor_burst: 1, rate_actor_refill_per_s: 1 }, 'mum')
    config(world, { rate_actor_burst: 1, rate_actor_refill_per_s: 1 }, 'pun')
    note(world, 'bob', 'b1')
    expect(note(world, 'bob', 'b2').status).toBe(429)
    world.settleNow()
    expect(note(world, 'bob', 'b3', 'pun').status).toBe(201)
  })
})

describe('the region limit (contract 8.3)', () => {
  it('over the limit a write gets 503 overloaded with Retry-After, costs the shopper nothing, and lifting it restores service', async () => {
    const { world, advance } = setup()
    newCart(world, 'mum')
    await advance(1100) // the cart itself was a client write: let it leave the one second window
    config(world, { rate_actor_burst: 5, rate_actor_refill_per_s: 1, region_write_limit_per_s: 2 })
    expect([note(world, 'bob', 'b1').status, note(world, 'bob', 'b2').status]).toEqual([201, 201])
    const over = note(world, 'bob', 'b3')
    expect(over.status).toBe(503)
    expect(over.body).toEqual({ error: 'overloaded', scope: 'region' })
    expect(Number(over.headers!['retry-after'])).toBeGreaterThanOrEqual(1)
    expect(left(note(world, 'alice', 'a1'))).toBeNaN() // 503 again, so no rate headers on it
    expect(status(world, 'mum').rate).toMatchObject({ region_limit_per_s: 2, refused_region: 2, refused_actor: 0 })
    await advance(1100)
    // Bob was charged for b1 and b2 only (5 - 2 = 3, plus 1.1 refilled, minus b4 = 3 left): the write the region refused kept his token
    expect(left(note(world, 'bob', 'b4'))).toBe(3)
    config(world, { region_write_limit_per_s: 0 })
    expect(Array.from({ length: 6 }, (_, i) => note(world, 'carol', `c${i}`).status).every((s) => s === 201 || s === 429)).toBe(true)
  })

  it('a replay is never limited, and peer replication and reads are untouched', async () => {
    const { world, advance } = setup()
    newCart(world, 'mum')
    await advance(3000)
    config(world, { region_write_limit_per_s: 1 }, 'pun')
    expect(note(world, 'bob', 'b1', 'pun').status).toBe(201)
    expect(note(world, 'bob', 'b2', 'pun').status).toBe(503)
    expect(note(world, 'bob', 'b1', 'pun').status).toBe(200) // replay of the stored write
    note(world, 'bob', 'm1', 'mum'); note(world, 'bob', 'm2', 'mum'); note(world, 'bob', 'm3', 'mum')
    await advance(4000)
    expect(world.node('pun').events.has('mum:4')).toBe(true) // the limited region still receives its peers' events
    expect(handle(world, 'pun', 'GET', '/v1/reports', undefined, null).status).toBe(200)
    expect(converged(world)).toBe(true)
  })
})

describe('power (contract 8.1)', () => {
  it('the three presets save 1, 8 and 32 writes per batch, and the status reports the batch', () => {
    const { world } = setup()
    expect(status(world, 'mum').load).toMatchObject({ power: 'medium', batch: 8, queue_depth: 0 })
    for (const [power, batch] of [['low', 1], ['high', 32], ['medium', 8]] as const) {
      expect(config(world, { power }).body.power).toBe(power)
      expect(status(world, 'mum').load).toMatchObject({ power, batch })
      expect(POWER_BATCH[power]).toBe(batch)
    }
    expect(status(world, 'pun').load.power).toBe('medium') // per region
  })

  it('a burst takes longer and queues deeper at low power; throughput rises steeply, then flattens', () => {
    const { world } = setup()
    const drain = (region: 'mum' | 'pun' | 'del', power: 'low' | 'medium' | 'high') => {
      const node = world.node(region) // a node of its own per power: an idle commit loop each time
      node.power = power
      const now = Date.now()
      let last = 0
      for (let i = 0; i < 300; i++) last = node.commit(now)
      return { last, peak: node.queueDepth(now) }
    }
    const low = drain('mum', 'low'), medium = drain('pun', 'medium'), high = drain('del', 'high')
    expect(low.last).toBeGreaterThan(medium.last)
    expect(medium.last).toBeGreaterThan(high.last)
    expect(low.peak).toBe(300)
    const speedup = (a: number, b: number) => a / b
    expect(speedup(low.last, medium.last)).toBeGreaterThan(speedup(medium.last, high.last)) // diminishing returns
    expect(serviceMs(32)).toBeGreaterThan(0)
    expect(serviceMs(1) / serviceMs(32)).toBeLessThan(32) // never free: one commit still costs
  })

  it('an idle write costs one whole commit at any power, and the queue empties again', async () => {
    const { world, advance } = setup()
    for (const [region, power] of [['pun', 'low'], ['del', 'medium'], ['blr', 'high']] as const) {
      const idle = world.node(region) // a node of its own per power: an idle commit loop each time
      idle.power = power
      expect(idle.commit(Date.now())).toBeCloseTo(COMMIT_FIXED_MS + 1.5, 5)
    }
    newCart(world, 'mum')
    for (let i = 0; i < 40; i++) note(world, 'bob', `q${i}`)
    expect(status(world, 'mum').load.queue_depth).toBeGreaterThan(0)
    await advance(5000)
    expect(status(world, 'mum').load.queue_depth).toBe(0)
    expect(status(world, 'mum').load.writes_per_s).toBe(0) // measured over the last two seconds
  })

  it('writes per second is measured from accepted writes, not invented', async () => {
    const { world, advance } = setup()
    newCart(world, 'mum')
    for (let i = 0; i < 10; i++) note(world, 'bob', `w${i}`)
    expect(status(world, 'mum').load.writes_per_s).toBe(5.5) // 11 writes over a two second window
    await advance(2500)
    expect(status(world, 'mum').load.writes_per_s).toBe(0)
  })
})

describe('POST /admin/config (contract 8.1)', () => {
  it('returns the full current set, round-trips, and changes only what it is given', () => {
    const { world } = setup()
    expect(config(world, {}).body).toEqual({ rate_actor_burst: 0, rate_actor_refill_per_s: 1, region_write_limit_per_s: 0, power: 'medium' })
    expect(config(world, { rate_actor_burst: 10 }).body).toEqual({ rate_actor_burst: 10, rate_actor_refill_per_s: 1, region_write_limit_per_s: 0, power: 'medium' })
    expect(config(world, { power: 'high', region_write_limit_per_s: 5, rate_actor_refill_per_s: 0.5 }).body).toEqual({ rate_actor_burst: 10, rate_actor_refill_per_s: 0.5, region_write_limit_per_s: 5, power: 'high' })
    expect(config(world, {}, 'pun').body.power).toBe('medium') // each region has its own settings
  })

  it('an unknown key is 422, so is a bad value, and nothing changes', () => {
    const { world } = setup()
    for (const bad of [{ colour: 'red' }, { rate_actor_burst: -1 }, { rate_actor_burst: 1.5 }, { rate_actor_burst: '3' }, { rate_actor_refill_per_s: 0 }, { region_write_limit_per_s: -2 }, { power: 'turbo' }]) {
      expect(config(world, bad).status).toBe(422)
    }
    expect(config(world, { rate_actor_burst: 4, colour: 'red' }).status).toBe(422)
    expect(config(world, {}).body).toEqual({ rate_actor_burst: 0, rate_actor_refill_per_s: 1, region_write_limit_per_s: 0, power: 'medium' })
  })

  it('is for the admin key only', () => {
    const { world } = setup()
    expect(officer(world, 'mum', 'POST', '/admin/config', { power: 'low' }).status).toBe(403)
    expect(handle(world, 'mum', 'POST', '/admin/config', { power: 'low' }, null).status).toBe(401)
    expect(status(world, 'mum').load.power).toBe('medium')
  })
})

describe('Bengaluru (contract 8.2)', () => {
  it('is configured but not part of a default mesh', () => {
    const { world, transport } = setup()
    expect(world.joined).toEqual(['mum', 'pun', 'del'])
    expect(transport.regions.map((r) => r.id)).toContain('blr')
    expect(world.isJoined('blr')).toBe(false)
  })

  it('joins empty, catches up from the others and converges; nothing is lost when it dies afterwards', async () => {
    const { world, chaos, advance } = setup()
    newCart(world, 'mum')
    for (let i = 0; i < 5; i++) note(world, 'bob', `n${i}`)
    await advance(6000)
    expect(converged(world)).toBe(true)
    await chaos.start('blr')
    expect(world.joined).toEqual(['mum', 'pun', 'del', 'blr'])
    expect(world.node('blr').events.size).toBe(0)
    await advance(12_000)
    expect(world.node('blr').events.size).toBe(world.node('mum').events.size)
    expect(converged(world)).toBe(true)
    expect(status(world, 'blr').load).toMatchObject({ power: 'medium', batch: 8 })
    expect(handle(world, 'blr', 'GET', `/v1/reports/${CART}`, undefined, null).body.state.notes).toHaveLength(5)
    await chaos.crash('blr')
    expect(world.node('mum').events.size).toBe(world.node('pun').events.size)
  })

  it('writes made on Bengaluru replicate to the other regions', async () => {
    const { world, chaos, advance } = setup()
    newCart(world, 'mum')
    await advance(3000)
    await chaos.start('blr')
    await advance(12_000)
    expect(note(world, 'esha', 'from-blr', 'blr').status).toBe(201)
    await advance(8000)
    expect(world.node('mum').events.has('blr:1')).toBe(true)
    expect(converged(world)).toBe(true)
  })
})

describe('the transport', () => {
  it('names the shopper to the bucket, holds the acknowledgement back while the commit queue is busy, and times out honestly', async () => {
    const { world, transport, advance } = setup()
    newCart(world, 'mum')
    config(world, { rate_actor_burst: 2, rate_actor_refill_per_s: 1, power: 'low' })
    const send = (actor: string, op: string, timeoutMs = 600) => transport.request('mum', 'POST', `/v1/reports/${CART}/notes`, { body: { text: 'x', op_id: op }, actor, timeoutMs })
    const settleP = <T,>(p: Promise<T>) => settle(p, advance)
    expect((await settleP(send('Bob', 'b1'))).headers['x-ratelimit-remaining']).toBe('1') // names are case-insensitive: "Bob" is the actor "bob"
    expect((await settleP(send('bob', 'b2'))).headers['x-ratelimit-remaining']).toBe('0')
    expect((await settleP(send('bob', 'b3'))).status).toBe(429)
    expect((await settleP(send('alice', 'a1'))).status).toBe(201)

    // pile up a backlog far longer than the caller will wait: the write is committed, the caller sees a timeout
    const node = world.node('mum')
    for (let i = 0; i < 400; i++) node.commit(Date.now())
    const before = node.events.size
    await expect(settleP(send('carol', 'c1'))).rejects.toBeInstanceOf(NetworkError)
    expect(node.events.size).toBe(before + 1)
    expect((await settleP(send('carol', 'c1', 10_000))).headers['idempotent-replay']).toBe('true') // the retry with the same op_id finds it
  })
})
