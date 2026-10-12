// Picks the backend: VITE_MODE=mock (default, the in-browser simulator) or live (the real regions).
// A ?mode=live|mock query parameter overrides it so a demo can switch without a rebuild.
import { createLiveChaos, createLiveTransport } from './liveClient'
import { createMock, type MockApi } from './mockClient'
import type { Chaos, Mode, Transport } from './types'
import type { SimWorld } from '../sim/world'

export interface Api {
  mode: Mode
  transport: Transport
  chaos: Chaos
  /** mock mode only: the simulated world (for the demo-only controls and tests) */
  world: SimWorld | null
  /** how many nodes the demo starts with (2 lets Delhi join live) */
  startNodes: number
}

export function resolveMode(): Mode {
  const fromUrl = typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('mode') : null
  const raw = fromUrl || (import.meta.env.VITE_MODE as string | undefined) || 'mock'
  return raw === 'live' ? 'live' : 'mock'
}

export function createApi(mode: Mode = resolveMode(), startNodes = 3): Api {
  if (mode === 'live') return { mode, transport: createLiveTransport(), chaos: createLiveChaos(), world: null, startNodes: 3 }
  const mock: MockApi = createMock({ joined: startNodes === 2 ? ['mum', 'pun'] : ['mum', 'pun', 'del'] })
  return { mode, transport: mock.transport, chaos: mock.chaos, world: mock.world, startNodes }
}

export * from './types'
