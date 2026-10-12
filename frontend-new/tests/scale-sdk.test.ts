// The browser SDK under the v1.2 scaling rules: home regions, a 429 that is an answer, a 503 that is "busy" and never blocks,
// a carry that sends only what a region may be missing, and a fourth region that has to earn its trust.
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RegionId } from '../src/api/types'
import { handle } from '../src/sim/api'
import { newReportId } from '../src/lib/util'
import { admin, cut, settle, setup } from './helpers'

afterEach(() => vi.useRealTimers())

const config = (world: ReturnType<typeof setup>['world'], region: RegionId, body: object) => admin(world, region, 'POST', '/admin/config', body)
const noteBody = (who: string) => ({ text: JSON.stringify({ kind: 'ADD', sku: 'CHAI-01', name: 'Masala chai box', qty: 1, price: 349, user: who }) })

async function started(opts: Parameters<typeof setup>[0] = {}) {
  const ctx = setup(opts)
  ctx.client.start()
  await ctx.advance(1200) // the first probes
  const run = <T,>(p: Promise<T>) => settle(p, ctx.advance)
  const cart = async (who: string, home?: RegionId) => {
    const id = newReportId()
    const res = await run(ctx.client.call('POST', '/v1/reports', { report_id: id, title: who, category: 'cart', severity: 1 }, { label: `${who}: new cart`, home, actor: who }))
    return { id, res }
  }
  const add = (id: string, who: string, home?: RegionId) => run(ctx.client.call('POST', `/v1/reports/${id}/notes`, noteBody(who), { label: `${who}: add`, home, actor: who }))
  const last = () => ctx.client.getState().timeline.at(-1)!
  return { ...ctx, run, cart, add, last }
}

describe('home regions (H2)', () => {
  it('a home region is the first choice; the fixed order is the fallback, and a returning region waits to be trusted', async () => {
    const { client, chaos, advance, cart, last } = await started()
    await cart('Chitra', 'pun'); expect(last().region).toBe('pun')
    await cart('Esha', 'del'); expect(last().region).toBe('del')
    await cart('Alice'); expect(last().region).toBe('mum') // no home given: the preferred region
    await chaos.crash('pun'); await advance(1500)
    await cart('Dev', 'pun'); expect(last().region).toBe('mum') // Pune is down: the fixed order (Mumbai), not whichever answers faster
    expect(last().tried).toBe(0) // the probe had already seen it; no failed attempt
    await chaos.start('pun'); await advance(1300) // booted, one or two good probes: answering but not trusted yet
    await cart('Dev', 'pun'); expect(last().region).toBe('mum')
    await advance(2500) // three good probes in a row
    await cart('Dev', 'pun'); expect(last().region).toBe('pun')
    expect(client.getState().counters.errors).toBe(0)
  })

  it('counts a move away from a dead home region once, not on every call', async () => {
    const { client, chaos, advance, cart } = await started()
    await cart('Chitra', 'pun')
    await chaos.crash('pun'); await advance(1500)
    await cart('Chitra', 'pun'); await cart('Chitra', 'pun'); await cart('Chitra', 'pun')
    expect(client.getState().counters.failovers).toBe(1)
    await cart('Alice', 'mum') // a different caller, a different first choice: not a failover
    expect(client.getState().counters.failovers).toBe(1)
  })
})

describe('a 429 is an answer (R1)', () => {
  it('is not an error, not a failover, not retried elsewhere, and Alice is unaffected', async () => {
    const { client, world, transport, cart, add, last } = await started()
    for (const r of ['mum', 'pun', 'del'] as const) config(world, r, { rate_actor_burst: 4, rate_actor_refill_per_s: 1 })
    const spy = vi.spyOn(transport, 'request')
    const bob = await cart('Bob', 'mum')
    const answers = []
    for (let i = 0; i < 6; i++) answers.push(await add(bob.id, 'Bob', 'mum'))
    expect(answers.map((r) => r.status)).toEqual([201, 201, 201, 429, 429, 429]) // the create used the first of four
    expect(answers[0].headers['x-ratelimit-remaining']).toBe('2')
    expect(answers[2].headers['x-ratelimit-remaining']).toBe('0')
    expect(answers[3].body).toMatchObject({ error: 'rate_limited', scope: 'actor' })
    expect(last()).toMatchObject({ region: 'mum', status: 429, refused: true, tried: 0, queued: false })
    expect(client.getState().counters).toMatchObject({ errors: 0, failovers: 0, refused: 3, queued: 0 })
    expect(spy.mock.calls.filter(([region, method, path]) => region !== 'mum' && method === 'POST' && String(path).endsWith('/notes'))).toEqual([]) // never sent elsewhere to dodge the limit
    expect(client.getState().outbox.filter((o) => o.event.actor === 'bob')).toHaveLength(4) // only what was accepted

    const alice = await cart('Alice', 'mum')
    expect(alice.res.status).toBe(201)
    expect(alice.res.headers['x-ratelimit-remaining']).toBe('3') // Alice's own bucket, untouched by Bob
  })

  it('Spam as Bob: a rapid run is mostly refused, nothing is lost that was accepted, and errors stay 0', async () => {
    const { client, world, cart, add } = await started()
    config(world, 'mum', { rate_actor_burst: 10, rate_actor_refill_per_s: 1 })
    const bob = await cart('Bob', 'mum')
    let accepted = 0, refused = 0
    for (let i = 0; i < 25; i++) { const r = await add(bob.id, 'Bob', 'mum'); if (r.status === 201) accepted++; else if (r.status === 429) refused++ }
    expect(accepted + refused).toBe(25)
    expect(accepted).toBeLessThanOrEqual(10) // the create took one token of ten
    expect(refused).toBeGreaterThanOrEqual(15)
    expect(client.getState().counters.errors).toBe(0)
    expect(world.node('mum').view(bob.id).state.notes).toHaveLength(accepted)
  })
})

describe('a 503 is "busy", not a failure (R3)', () => {
  it('moves on at once without waiting, avoids the busy region until Retry-After has passed, then goes back', async () => {
    const { client, world, advance, cart, add, last } = await started()
    const alice = await cart('Alice', 'mum')
    await advance(1100) // the create leaves the one second window
    config(world, 'mum', { region_write_limit_per_s: 1 })
    expect((await add(alice.id, 'Alice', 'mum')).status).toBe(201); expect(last().region).toBe('mum')

    const t0 = Date.now()
    const second = await add(alice.id, 'Alice', 'mum') // Mumbai is at its limit: 503
    expect(second.status).toBe(201)
    expect(last()).toMatchObject({ region: 'pun', tried: 1, failedOn: ['mum'] })
    expect(Date.now() - t0).toBeLessThan(250) // it did not sleep through a Retry-After
    expect(client.getState().counters).toMatchObject({ errors: 0, failovers: 1 })

    const route = client.routeFor('mum')
    expect(route).toMatchObject({ region: 'pun', ideal: 'mum', state: 'busy' })
    expect(route.busyForMs).toBeGreaterThan(0)
    await add(alice.id, 'Alice', 'mum')
    expect(last()).toMatchObject({ region: 'pun', tried: 0 }) // Mumbai is avoided while busy: no second bounce

    config(world, 'mum', { region_write_limit_per_s: 0 })
    await advance(2300) // Retry-After (2 s) has passed
    expect(client.routeFor('mum')).toMatchObject({ region: 'mum', state: 'ok' })
    await add(alice.id, 'Alice', 'mum')
    expect(last()).toMatchObject({ region: 'mum', tried: 0 })
    expect(world.node('mum').view(alice.id).state.notes).toHaveLength(4) // all four acknowledged writes are there
    await advance(6000)
    expect(world.node('pun').view(alice.id).state.notes).toHaveLength(4)
  })

  it('refusals that follow one another back off: 2 s, 4 s, then 5 s at most; a good answer starts again at 2 s', async () => {
    const { client, world, advance, cart, add } = await started()
    const alice = await cart('Alice', 'mum')
    await advance(1100)
    config(world, 'mum', { region_write_limit_per_s: 1 })
    await add(alice.id, 'Alice', 'mum') // the one write Mumbai takes this second
    const busy = () => client.routeFor('mum').busyForMs
    const refused = async () => { // another client keeps Mumbai at its limit, so this attempt is refused as well
      handle(world, 'mum', 'POST', `/v1/reports/${alice.id}/notes`, { text: 'x', op_id: `other-${Math.random()}` }, 'officer', 'someone-else')
      await add(alice.id, 'Alice', 'mum')
      return busy()
    }
    const waits = [await refused()] // the first refusal: the region's Retry-After, 2 s
    for (let i = 0; i < 4; i++) { await advance(busy() + 60); waits.push(await refused()) }
    const near = (got: number, want: number) => got <= want && got > want - 400
    expect(waits.map((w, i) => near(w, [2000, 4000, 5000, 5000, 5000][i]))).toEqual([true, true, true, true, true])

    config(world, 'mum', { region_write_limit_per_s: 0 })
    await advance(busy() + 60)
    await add(alice.id, 'Alice', 'mum') // Mumbai answers again
    expect(busy()).toBe(0)
    config(world, 'mum', { region_write_limit_per_s: 1 })
    await advance(1100)
    await add(alice.id, 'Alice', 'mum')
    expect(near(await refused(), 2000)).toBe(true) // the streak was forgotten
  })

  it('a move because the region is busy is a relay, not a recovery', async () => {
    const { client, world, advance, cart, add } = await started()
    const alice = await cart('Alice', 'mum')
    await advance(1100)
    config(world, 'mum', { region_write_limit_per_s: 1 })
    await add(alice.id, 'Alice', 'mum')
    await add(alice.id, 'Alice', 'mum') // 503 on Mumbai, served by Pune after the cart was handed to it
    const c = client.getState().counters
    expect(c.carried).toBeGreaterThanOrEqual(1)
    expect(c.relayed).toBe(c.carried)
    expect(c.recovered).toBe(0) // nobody died
    expect(client.getState().lastCarry).toMatchObject({ to: 'pun', recovered: 0 })
  })

  it('when every region is busy the action waits on the device and goes through once one is free', async () => {
    const { client, world, advance, cart, add, run } = await started()
    const alice = await cart('Alice', 'mum')
    await advance(1100)
    for (const r of ['mum', 'pun', 'del'] as const) config(world, r, { region_write_limit_per_s: 1 })
    await add(alice.id, 'Alice', 'mum'); await add(alice.id, 'Alice', 'mum'); await add(alice.id, 'Alice', 'mum') // one write per region
    const kept = await add(alice.id, 'Alice', 'mum') // all three are at their limit now
    expect(kept.status).toBe(202)
    expect(client.getState().deviceQueue).toBe(1)
    expect(client.getState().counters.errors).toBe(0)
    for (const r of ['mum', 'pun', 'del'] as const) config(world, r, { region_write_limit_per_s: 0 })
    await advance(3000)
    expect(client.getState().deviceQueue).toBe(0)
    await run(Promise.resolve())
    expect(Math.max(...(['mum', 'pun', 'del'] as const).map((r) => world.node(r).view(alice.id).state.notes.length))).toBe(4)
  })
})

describe('the route line (R3)', () => {
  it('says why the first choice is not serving: down, recovering or busy', async () => {
    const { client, chaos, advance } = await started()
    expect(client.routeFor('pun')).toMatchObject({ region: 'pun', ideal: 'pun', state: 'ok', busyForMs: 0 })
    expect(client.routeFor()).toMatchObject({ region: 'mum', ideal: 'mum', state: 'ok' }) // no home: the preferred region
    await chaos.crash('pun'); await advance(1500)
    expect(client.routeFor('pun')).toMatchObject({ region: 'mum', ideal: 'pun', state: 'down' })
    await chaos.start('pun'); await advance(1700) // booted, and the first probe after that has been answered
    expect(client.routeFor('pun')).toMatchObject({ region: 'mum', ideal: 'pun', state: 'recovering' })
    await advance(2500)
    expect(client.routeFor('pun')).toMatchObject({ region: 'pun', state: 'ok' })
  })
})

describe('what the browser hands over (carry)', () => {
  it('nothing to hand over means no request at all', async () => {
    const { transport, cart, advance } = await started()
    const spy = vi.spyOn(transport, 'request')
    await cart('Chitra', 'pun'); await advance(6000) // replicated everywhere, the outbox empties
    await cart('Alice', 'mum'); await advance(6000)
    await cart('Esha', 'del')
    expect(spy.mock.calls.filter(([, , path]) => path === '/v1/carry')).toEqual([])
  })

  it('only the cart being written, while its origin is fine: unrelated carts stay where they are', async () => {
    const { client, world, transport, cart, add, advance } = await started()
    cut(world, 'mum', 'pun'); cut(world, 'mum', 'del') // nothing replicates from Mumbai: the browser is the only other holder
    const a = await cart('Alice', 'mum'); await add(a.id, 'Alice', 'mum')
    const b = await cart('Bob', 'mum'); await add(b.id, 'Bob', 'mum')
    const spy = vi.spyOn(transport, 'request')
    await advance(50)
    await add(a.id, 'Alice', 'pun') // Alice's next write is served by Pune, which has never heard of her cart
    const carried = spy.mock.calls.filter(([region, , path]) => region === 'pun' && path === '/v1/carry')
    expect(carried).toHaveLength(1)
    const events = (carried[0][3] as { body: { events: { report_id: string }[] } }).body.events
    expect(events.length).toBe(2) // her CREATE and her first note
    expect(new Set(events.map((e) => e.report_id))).toEqual(new Set([a.id]))
    expect(world.node('pun').view(a.id).state.notes).toHaveLength(2)
    expect(world.node('pun').eventsOf(b.id)).toHaveLength(0)
    expect(client.getState().counters).toMatchObject({ relayed: 2, recovered: 0 })
  })

  it('every event whose origin stopped answering, whichever cart: a real recovery', async () => {
    const { client, world, chaos, cart, add, advance, run } = await started()
    cut(world, 'mum', 'pun'); cut(world, 'mum', 'del')
    const a = await cart('Alice', 'mum'); await add(a.id, 'Alice', 'mum')
    const b = await cart('Bob', 'mum'); await add(b.id, 'Bob', 'mum')
    await chaos.crash('mum'); await advance(1500)
    await add(a.id, 'Alice', 'mum') // falls to Pune
    expect(client.getState().counters).toMatchObject({ recovered: 4, relayed: 0 })
    expect(world.node('pun').view(b.id).state.notes).toHaveLength(1) // Bob's cart was never written again, yet it survived
    expect(await run(client.presentOnSurvivors())).toMatchObject({ present: 5, checked: 5 })
  })
})

describe('a region that is added (H1, H3)', () => {
  it('Bengaluru is not part of the SDK until the console says so, then it must earn its trust before it serves anyone', async () => {
    const { client, world, chaos, advance, cart, last } = await started()
    expect(client.getState().members).toEqual(['mum', 'pun', 'del'])
    expect(client.getState().regions.map((r) => r.id)).toEqual(['mum', 'pun', 'del'])
    await chaos.start('blr')
    client.setMembers(['mum', 'pun', 'del', 'blr'])
    expect(client.getState().regions.find((r) => r.id === 'blr')).toMatchObject({ up: false, trusted: false })
    await advance(1500) // booted (0.8 s), answering, but only a probe or two
    expect(client.getState().regions.find((r) => r.id === 'blr')).toMatchObject({ up: true, trusted: false })
    await cart('Farah', 'blr'); expect(last().region).toBe('mum') // not trusted yet: the first choice is skipped
    await advance(3000)
    expect(client.getState().regions.find((r) => r.id === 'blr')).toMatchObject({ up: true, trusted: true })
    await cart('Farah', 'blr'); expect(last().region).toBe('blr')
    await advance(8000)
    expect(world.node('blr').events.size).toBe(world.node('mum').events.size) // it caught up
    expect(client.getState().counters.errors).toBe(0)
  })

  it('ignores regions the transport does not have', () => {
    const { client } = setup()
    client.setMembers(['mum', 'pun', 'del', 'zzz' as RegionId])
    expect(client.getState().members).toEqual(['mum', 'pun', 'del'])
  })
})

describe('the simulator answers the way the SDK expects', () => {
  it('a shopper name given to the transport is the actor of the event', async () => {
    const { world, cart } = await started()
    const bob = await cart('Bob', 'pun')
    expect(world.node('pun').eventsOf(bob.id)[0].actor).toBe('bob')
    expect(handle(world, 'pun', 'GET', `/v1/reports/${bob.id}`, undefined, null).status).toBe(200)
  })
})
