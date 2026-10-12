import { afterEach, describe, expect, it, vi } from 'vitest'
import { NetworkError, type HttpResult, type RegionId, type RequestOptions, type Status, type Transport } from '../src/api/types'
import { SyncShieldClient } from '../src/sdk/syncshieldClient'
import { newReportId } from '../src/lib/util'
import { CART, settle, setup } from './helpers'

afterEach(() => vi.useRealTimers())

const note = (sku: string) => ({ text: JSON.stringify({ kind: 'ADD', sku, name: sku, qty: 1, price: 100, user: 'Alice' }) })

async function shop() {
  const ctx = setup()
  ctx.client.start()
  await ctx.advance(1200) // first probes
  const run = <T>(p: Promise<T>) => settle(p, ctx.advance)
  await run(ctx.client.call('POST', '/v1/reports', { report_id: CART, title: 'Alice', category: 'cart', severity: 1 }, { label: 'create cart' }))
  const add = (sku: string) => run(ctx.client.call('POST', `/v1/reports/${CART}/notes`, note(sku), { label: `add ${sku}` }))
  return { ...ctx, run, add }
}

describe('the happy path', () => {
  it('keeps the signed event in the outbox until a peer has it, then lets it go (T19)', async () => {
    const { client, add, advance } = await shop()
    await add('CHAI-01')
    expect(client.getState().outbox.map((o) => o.event.event_id)).toEqual(['mum:1', 'mum:2'])
    expect(client.getState().counters).toMatchObject({ requests: 2, acknowledged: 2, errors: 0, failovers: 0 })
    expect(client.getState().timeline.at(-1)).toMatchObject({ region: 'mum', tried: 0, queued: false, status: 201 })
    await advance(4000) // replication + a status poll
    expect(client.getState().outbox).toEqual([])
  })

  it('probes order the nodes by latency and report the ladder level', async () => {
    const { client } = await shop()
    const regions = client.getState().regions
    expect(regions.every((r) => r.up && r.ms !== null)).toBe(true)
    expect(regions.find((r) => r.id === 'mum')!.level).toBe('HEALTHY')
  })

  it('gives each add an op_id of its own and sends the same one on every retry', async () => {
    const { client, run } = await shop()
    const seen: string[] = []
    const real = (client as any).t.request.bind((client as any).t)
    ;(client as any).t.request = (r: RegionId, m: string, p: string, o?: RequestOptions) => { if (p.endsWith('/notes')) seen.push((o!.body as any).op_id); return real(r, m, p, o) }
    await run(client.call('POST', `/v1/reports/${CART}/notes`, note('A')))
    await run(client.call('POST', `/v1/reports/${CART}/notes`, note('B')))
    expect(new Set(seen).size).toBe(2)
  })
})

describe('zero-loss failover (T17, T20, A2)', () => {
  it('kill -9 a node holding unreplicated writes: the browser carries them, nothing is lost, no error is shown', async () => {
    const { world, chaos, client, add, run } = await shop()
    for (const sku of ['CHAI-01', 'MAT-01', 'BAG-01']) await add(sku)
    expect(world.node('mum').unreplicated(['pun', 'del']).length).toBe(4) // the node holds writes no peer has yet
    expect(world.node('pun').events.size).toBe(0)

    await chaos.crash('mum') // hard kill: the in-memory push queue dies too
    const killedAt = Date.now()
    const res = await add('LAMP-01')
    const failoverMs = Date.now() - killedAt

    expect(res.status).toBe(201)
    expect(client.getState().counters).toMatchObject({ errors: 0, failovers: 1 })
    expect(client.getState().counters.carried).toBeGreaterThanOrEqual(4)
    expect(failoverMs).toBeLessThanOrEqual(1500) // T20: from kill to the first success on a survivor
    expect(client.getState().timeline.at(-1)).toMatchObject({ region: expect.stringMatching(/pun|del/), tried: 1, failedOn: ['mum'] })

    const proof = await run(client.presentOnSurvivors())
    expect(proof).toEqual({ present: 5, checked: 5, answered: 2 }) // acknowledged = present on survivors
    const survivor = world.node(client.getState().serving!)
    const cart = survivor.view(CART)
    expect(cart.state.notes).toHaveLength(4)
    expect(cart.incomplete).toBe(false)
  })

  it('a retried add that already reached the survivor by carry is one add, not two', async () => {
    const { world, chaos, client, run } = await shop()
    const body = { ...note('CHAI-01'), op_id: 'fixed-op' }
    await run(client.call('POST', `/v1/reports/${CART}/notes`, body))
    await chaos.crash('mum')
    const again = await run(client.call('POST', `/v1/reports/${CART}/notes`, body)) // same op_id, now on a survivor
    expect(again.status).toBe(200)
    expect(again.headers['idempotent-replay']).toBe('true')
    const survivor = world.node(client.getState().serving!)
    expect(survivor.view(CART).state.notes).toHaveLength(1)
  })

  it('the count is honest: events no survivor and no outbox holds are reported as missing', async () => {
    const { world, chaos, client, run } = await shop()
    expect(client.getState().outbox.length).toBe(1)
    await chaos.crash('mum') // the only copy is on mum's disk and in the outbox
    const proof = await run(client.presentOnSurvivors())
    expect(proof).toMatchObject({ present: 0, checked: 1 }) // not yet carried: shown, never hidden
    expect(world.node('pun').events.size).toBe(0)
  })

  it('fails back to the preferred node after enough good probes', async () => {
    const { chaos, client, add, advance } = await shop()
    await add('CHAI-01')
    await chaos.crash('mum')
    await add('MAT-01')
    expect(client.getState().serving).not.toBe('mum')
    expect(client.getState().routePreferred).toBe(false)
    await chaos.start('mum')
    await advance(3000) // ~0.9 s to boot, then three good probes 0.5 s apart
    expect(client.getState().routePreferred).toBe(true)
    await add('BAG-01')
    expect(client.getState().serving).toBe('mum')
  })

  it('keeps the shoppers action on the device while every node is down, then sends it once', async () => {
    const { world, chaos, client, run, advance } = await shop()
    for (const r of ['mum', 'pun', 'del'] as const) await chaos.crash(r)
    const res = await run(client.call('POST', `/v1/reports/${CART}/notes`, note('CHAI-01')))
    expect(res.status).toBe(202)
    expect(client.getState()).toMatchObject({ deviceQueue: 1, counters: expect.objectContaining({ queued: 1, errors: 0 }) })
    await chaos.start('mum')
    await advance(4000)
    expect(client.getState().deviceQueue).toBe(0)
    expect(world.node('mum').view(CART).state.notes).toHaveLength(1)
  })
})

describe('a probe that finds the node dead first is still a failover', () => {
  it('the next write goes straight to the survivor, carries first, and is counted once', async () => {
    const { client, chaos, add, advance } = await shop()
    await add('CHAI-01')
    await chaos.crash('mum')
    await advance(4000) // the health probe notices before the shopper taps again
    const res = await add('MAT-01')
    expect(res.status).toBe(201)
    expect(client.getState().counters).toMatchObject({ errors: 0, failovers: 1 })
    expect(client.getState().timeline.at(-1)).toMatchObject({ tried: 0, queued: false }) // no failed attempt: the node was already known down
    expect(client.getState().timeline.at(-1)!.region).not.toBe('mum')
  })
})

describe('the failover order is fixed, not a latency lottery', () => {
  it('Mumbai, then Pune, then Delhi, even when a later region answers faster; a region just back waits its turn', async () => {
    vi.useFakeTimers()
    const down = new Set<RegionId>()
    const latency: Record<string, number> = { mum: 2, pun: 9, del: 3 } // Delhi answers faster than Pune
    const t: Transport = {
      mode: 'mock',
      regions: (['mum', 'pun', 'del'] as RegionId[]).map((id) => ({ id, name: id, url: '' })),
      async request(region, _method, path): Promise<HttpResult> {
        if (down.has(region)) throw new NetworkError('refused', 'refused')
        return path === '/healthz' ? { status: 200, body: { ok: true, level: 'HEALTHY' }, headers: {}, ms: latency[region] } : { status: 201, body: { event: ev(region, 1) }, headers: {}, ms: latency[region] }
      },
    }
    const client = new SyncShieldClient(t)
    client.start()
    await vi.advanceTimersByTimeAsync(1200)
    const serve = async () => { const p = client.call('POST', '/v1/reports', { title: 'x' }); await vi.advanceTimersByTimeAsync(100); await p; return client.getState().timeline.at(-1)!.region }

    expect(await serve()).toBe('mum')
    down.add('mum'); await vi.advanceTimersByTimeAsync(1500)
    expect(await serve()).toBe('pun') // not Delhi, although Delhi is faster
    down.add('pun'); await vi.advanceTimersByTimeAsync(1500)
    expect(await serve()).toBe('del')
    down.delete('mum'); await vi.advanceTimersByTimeAsync(600) // one good probe: Mumbai answers but is not trusted yet
    expect(await serve()).toBe('del')
    await vi.advanceTimersByTimeAsync(2500) // three good probes: it goes first again
    expect(await serve()).toBe('mum')
    client.stop()
  })
})

describe('what is an answer and what is a failure', () => {
  it('403, 409 and 422 are answers: no failover, no extra requests', async () => {
    const { client, transport, run } = await shop()
    const spy = vi.spyOn(transport, 'request')
    const stale = await run(client.call('PATCH', `/v1/reports/${CART}`, { field: 'severity', value: 3, expected: ['nope:1'] }))
    const invalid = await run(client.call('PATCH', `/v1/reports/${CART}`, { field: 'severity', value: 99 }))
    const forbidden = await run(client.call('POST', `/v1/reports/${CART}/resolve`, { field: 'status', value: 'closed', candidates: ['mum:1'], reason: 'x' }))
    expect([stale.status, invalid.status, forbidden.status]).toEqual([409, 422, 403])
    expect(spy).toHaveBeenCalledTimes(3)
    expect(client.getState().counters.failovers).toBe(0)
  })
})

// ---- stub transports for the cases the simulator does not produce ---------------------------------------
class Stub implements Transport {
  mode = 'mock' as const
  regions = [{ id: 'mum' as RegionId, name: 'Mumbai', url: '' }, { id: 'pun' as RegionId, name: 'Pune', url: '' }]
  log: string[] = []
  constructor(private respond: (r: RegionId, method: string, path: string, n: number) => HttpResult | NetworkError) {}
  async request(region: RegionId, method: string, path: string): Promise<HttpResult> {
    this.log.push(`${region} ${method} ${path}`)
    const out = this.respond(region, method, path, this.log.length)
    if (out instanceof NetworkError) throw out
    return out
  }
}
const okRes = (status: number, body: any, headers: Record<string, string> = {}): HttpResult => ({ status, body, headers, ms: 5 })
const ev = (origin: string, seq: number) => ({ event_id: `${origin}:${seq}`, origin, seq, report_id: CART, type: 'NOTE' })

describe('backpressure, carry support and the outbox limits', () => {
  it('503 with Retry-After waits, then moves on to the next node (and shows the shopper no error)', async () => {
    vi.useFakeTimers()
    const t = new Stub((r, _m, p) => p === '/healthz' ? okRes(200, { ok: true, level: 'HEALTHY' }) : r === 'mum' ? okRes(503, null, { 'retry-after': '1' }) : okRes(201, { event: ev('pun', 1) }))
    const client = new SyncShieldClient(t, { preferred: 'mum' })
    const p = client.call('POST', '/v1/reports', { title: 'x' })
    await vi.advanceTimersByTimeAsync(1100)
    const res = await p
    expect(res.status).toBe(201)
    expect(client.getState().timeline.at(-1)).toMatchObject({ region: 'pun', tried: 1, failedOn: ['mum'] })
    expect(client.getState().counters.errors).toBe(0)
  })

  it('a node with no /healthz is probed through /v1/health instead of being called down', async () => {
    vi.useFakeTimers()
    const t = new Stub((_r, _m, p) => p === '/healthz' ? okRes(404, null) : p === '/v1/health' ? okRes(200, { ok: true }) : okRes(404, null))
    const client = new SyncShieldClient(t)
    await client.probeOnce()
    expect(client.getState().regions.every((r) => r.up)).toBe(true)
    expect(t.log).toContain('mum GET /v1/health')
  })

  it('a node without /v1/carry is reported, and requests still go through', async () => {
    vi.useFakeTimers()
    let mumDead = false
    let n = 0
    const t = new Stub((r, _m, p) => {
      if (p === '/v1/carry') return okRes(404, null)
      if (r === 'mum' && mumDead) return new NetworkError('down', 'refused')
      return okRes(201, { event: ev(r, ++n) })
    })
    const client = new SyncShieldClient(t)
    await client.call('POST', '/v1/reports', { title: 'x' })
    mumDead = true
    const p = client.call('POST', '/v1/reports', { title: 'y' })
    await vi.advanceTimersByTimeAsync(50)
    expect((await p).status).toBe(201)
    expect(client.getState().carrySupported).toBe(false)
    expect(client.getState().lastCarry).toMatchObject({ unsupported: true })
  })

  it('the outbox is bounded and prunes by the plan rule (T19)', async () => {
    vi.useFakeTimers()
    const t = new Stub((_r, _m, _p, n) => okRes(201, { event: ev('mum', n) }))
    const client = new SyncShieldClient(t, { maxOutbox: 3, outboxTtlMin: 1 })
    for (let i = 0; i < 5; i++) await client.call('POST', '/v1/reports', { title: String(i) })
    expect(client.getState().outbox).toHaveLength(3)
    expect(client.getState().counters.overflowed).toBe(2)
    const s = (heads: Record<string, number>, acked: Record<string, number> = {}) => ({ heads, peers: { pun: { acked } } }) as unknown as Status
    const seqs = () => client.getState().outbox.map((o) => o.event.seq)
    client.prune('pun', s({ mum: 0 }))
    expect(seqs()).toHaveLength(3) // nobody has them: they stay
    const last = Math.max(...seqs())
    client.prune('pun', s({ mum: last - 1 })) // a node other than the origin holds all but the newest
    expect(seqs()).toEqual([last])
    client.prune('mum', s({ mum: last }, { mum: last })) // a peer acknowledged it
    expect(seqs()).toEqual([])
  })

  it('an event that nobody confirmed is dropped after the TTL, and counted', async () => {
    vi.useFakeTimers()
    const t = new Stub(() => okRes(201, { event: ev('mum', 1) }))
    const client = new SyncShieldClient(t, { outboxTtlMin: 1 })
    await client.call('POST', '/v1/reports', { title: 'x' })
    await vi.advanceTimersByTimeAsync(61_000)
    client.prune('mum', { heads: { mum: 0 }, peers: {} } as unknown as Status)
    expect(client.getState().outbox).toEqual([])
    expect(client.getState().counters.expired).toBe(1)
  })
})

describe('ids', () => {
  it('client report ids match the contract pattern', () => {
    for (let i = 0; i < 50; i++) expect(newReportId()).toMatch(/^R-[0-9a-f]{8}$/)
  })
})
