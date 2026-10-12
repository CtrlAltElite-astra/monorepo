import { vi } from 'vitest'
import { SimChaos, SimTransport, handle } from '../src/sim/api'
import { SimWorld, type SimOptions } from '../src/sim/world'
import { SyncShieldClient, type SdkOptions } from '../src/sdk/syncshieldClient'
import type { RegionId } from '../src/api/types'

export const CART = 'R-3f9a2c10'

/** A simulated world with a manually driven clock (fake timers): deterministic and instant. */
export function setup(opts: SimOptions = {}, sdk: Partial<SdkOptions> = {}) {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-12T14:00:00Z'))
  const world = new SimWorld(opts)
  world.start()
  const transport = new SimTransport(world)
  const chaos = new SimChaos(world)
  const client = new SyncShieldClient(transport, sdk)
  const advance = (ms: number) => vi.advanceTimersByTimeAsync(ms)
  return { world, transport, chaos, client, advance }
}

/** Drive the fake clock until a promise settles (the SDK and simulator wait on timers). */
export async function settle<T>(p: Promise<T>, advance: (ms: number) => Promise<unknown>, maxMs = 15000): Promise<T> {
  let done = false
  let value: T | undefined
  let error: unknown
  p.then((v) => { done = true; value = v }, (e) => { done = true; error = e })
  for (let t = 0; t < maxMs && !done; t += 20) await advance(20)
  if (!done) throw new Error('promise did not settle')
  if (error) throw error
  return value as T
}

export const officer =(world: SimWorld, region: RegionId, method: string, path: string, body?: any) => handle(world, region, method, path, body, 'officer')
export const supervisor = (world: SimWorld, region: RegionId, method: string, path: string, body?: any) => handle(world, region, method, path, body, 'supervisor')
export const admin = (world: SimWorld, region: RegionId, method: string, path: string, body?: any) => handle(world, region, method, path, body, 'admin')

export function newCart(world: SimWorld, region: RegionId, id = CART, owner = 'Alice') {
  return officer(world, region, 'POST', '/v1/reports', { report_id: id, op_id: `create-${id}`, title: owner, category: 'cart', severity: 1 })
}

export const addItem = (world: SimWorld, region: RegionId, id: string, sku: string, op: string) =>
  officer(world, region, 'POST', `/v1/reports/${id}/notes`, { op_id: op, text: JSON.stringify({ kind: 'ADD', sku, name: sku, qty: 1, price: 100, user: 'Alice' }) })

export const cut = (world: SimWorld, a: RegionId, b: RegionId, mode: 'down' | 'up' | 'slow' = 'down', extra: object = {}) => {
  admin(world, a, 'POST', `/admin/links/${b}`, { mode, ...extra })
  admin(world, b, 'POST', `/admin/links/${a}`, { mode, ...extra })
}

export const status = (world: SimWorld, region: RegionId) => handle(world, region, 'GET', '/v1/status', undefined, null).body

export function converged(world: SimWorld): boolean {
  const s = world.joined.filter((r) => world.node(r).up).map((r) => status(world, r))
  return s.length > 1 && s.every((x) => x.event_digest === s[0].event_digest && x.state_hash === s[0].state_hash)
}
