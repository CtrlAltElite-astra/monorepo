import test from 'node:test'
import assert from 'node:assert/strict'
import { proveConvergence } from './prove.js'

// A tiny in-memory cluster: each node holds a set of events; replication runs on every sleep().
function fake({ brokenHeal = false, dropAckedWrite = false, downNode = null } = {}) {
  const ids = ['mum', 'pun', 'del']
  const nodes = Object.fromEntries(ids.map((id) => [id, { events: new Set(), seq: 0, up: id !== downNode }]))
  const links = {}
  const key = (a, b) => [a, b].sort().join('-')
  let clock = 0
  const mkSeed = (seed) => () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296 }
  const replicate = () => {
    for (const a of ids) for (const b of ids) {
      if (a === b || links[key(a, b)] === 'down' || links[key(a, b)] === 'stuck') continue
      for (const e of nodes[a].events) nodes[b].events.add(e)
    }
  }
  const env = {
    regions: ids.map((id) => ({ id })),
    rng: mkSeed(7),
    now: () => clock,
    sleep: async (ms) => { clock += ms; replicate() },
    log: () => {},
    write: async (id, method, path) => {
      const n = nodes[id]
      if (!n.up) return { ok: false, status: 0, body: null }
      n.seq += 1
      const eid = `${id}:${n.seq}`
      if (!(dropAckedWrite && n.seq === 2)) n.events.add(eid)  // pretend-acknowledge but never store
      return { ok: true, status: 201, body: { event_id: eid } }
    },
    setPair: async (a, b, mode) => {
      links[key(a, b)] = brokenHeal && mode === 'up' ? 'stuck' : mode
    },
    status: async (id) => {
      const n = nodes[id]
      if (!n.up) return null
      const s = [...n.events].sort().join(',')
      return { level: 'HEALTHY', event_count: n.events.size, event_digest: s, state_hash: 'S' + s, policy_version: 1, quarantined: 0 }
    },
    has: async (id, list) => ({ missing: list.filter((x) => !nodes[id].events.has(x)) }),
    report: async () => ({ conflicts: [{ field: 'severity', rule: 'HIGHEST', values: [5, 4] }] }),
  }
  return { env, nodes }
}

test('passes when every node ends with the same events and every ack is present', async () => {
  const { env, nodes } = fake()
  const r = await proveConvergence(env, { rounds: 3, writesPerRound: 5 })
  assert.equal(r.ok, true, r.reason)
  assert.equal(r.partitions, 3)
  assert.ok(r.acknowledged >= 16)
  assert.deepEqual(Object.values(r.missing), [[], [], []])
  const sets = Object.values(nodes).map((n) => [...n.events].sort().join())
  assert.equal(new Set(sets).size, 1)
  assert.equal(r.conflicts[0].rule, 'HIGHEST')
})

test('fails honestly when the nodes never converge', async () => {
  const { env } = fake({ brokenHeal: true })
  const r = await proveConvergence(env, { rounds: 2, writesPerRound: 4, settleMs: 3000 })
  assert.equal(r.ok, false)
  assert.match(r.reason, /did not (converge|replicate)/)
})

test('fails when an acknowledged write is not actually stored', async () => {
  const { env } = fake({ dropAckedWrite: true })
  const r = await proveConvergence(env, { rounds: 2, writesPerRound: 6 })
  assert.equal(r.ok, false)
  assert.match(r.reason, /missing/)
  assert.ok(Object.values(r.missing).some((m) => m.length > 0))
})

test('refuses to run while a node is down and says which one', async () => {
  const { env } = fake({ downNode: 'del' })
  const r = await proveConvergence(env)
  assert.equal(r.ok, false)
  assert.match(r.reason, /del unreachable/)
  assert.equal(r.acknowledged, 0)
})
