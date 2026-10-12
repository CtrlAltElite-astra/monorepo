// MOCK mode: an in-browser simulator of three nodes behind the same Transport the live client implements.
import { newReportId } from '../lib/util'
import { addNoteText, PRODUCTS, SHOPPERS } from '../domain/shop'
import { handle, SimChaos, SimTransport } from '../sim/api'
import { SimWorld, type SimOptions } from '../sim/world'
import type { RegionId } from './types'

export interface MockApi { world: SimWorld; transport: SimTransport; chaos: SimChaos }

const HOME: Record<string, RegionId> = { Alice: 'mum', Bob: 'pun', Chitra: 'del', Dev: 'mum', Esha: 'pun' }
const BASKET: Record<string, string[]> = {
  Alice: ['FCOFF-500', 'CHAI-01'], Bob: ['EARBUD-01'], Chitra: ['KURTA-01', 'BOTTLE-01'], Dev: ['LAMP-01'], Esha: ['MAT-01', 'GIFT-500'],
}

/** Appendix B: each shopper has one partly filled cart so no screen starts empty. */
export function seed(world: SimWorld) {
  for (const who of SHOPPERS) {
    const home = HOME[who]
    if (!world.isJoined(home)) continue
    const node = world.node(home)
    const reportId = newReportId()
    handle(world, home, 'POST', '/v1/reports', { report_id: reportId, op_id: `seed-create-${who}`, title: who, category: 'cart', severity: 1 }, 'officer')
    for (const sku of BASKET[who]) {
      const p = PRODUCTS.find((x) => x.sku === sku)!
      handle(world, node.id, 'POST', `/v1/reports/${reportId}/notes`, { text: addNoteText(p, who), op_id: `seed-${who}-${sku}` }, 'officer')
    }
  }
  world.settleNow()
}

export function createMock(opts: SimOptions & { seeded?: boolean } = {}): MockApi {
  const { seeded = true, ...simOpts } = opts
  const world = new SimWorld(simOpts)
  if (seeded) seed(world)
  // the world's clock is started by whoever owns it (the demo's effect), so StrictMode double-mounts cannot leak timers
  return { world, transport: new SimTransport(world), chaos: new SimChaos(world) }
}
