// Run: cd dashboard && npm test        (node's built-in test runner, no extra packages)
import test from 'node:test'
import assert from 'node:assert/strict'
import { SyncShieldClient } from './syncshieldClient.js'

// ---- fake cluster: three in-memory nodes behind a fake fetch -----------------
class FakeNode {
  constructor(id, { v11 = true } = {}) {
    this.id = id
    this.v11 = v11              // supports /healthz, /v1/carry, /v1/events/has, signed event in body
    this.up = true
    this.dropAckOnce = false    // commit, then lose the response (client sees a timeout)
    this.retryAfter = 0         // answer 503 with Retry-After
    this.seq = 0
    this.events = new Map()     // event_id -> envelope
    this.byOp = new Map()       // op_id -> event_id
    this.seen = []              // every write request received: {path, op_id}
    this.peerAcks = {}          // what peers acknowledged: origin -> seq
  }
  heads() {
    const h = {}
    for (const e of this.events.values()) h[e.origin] = Math.max(h[e.origin] || 0, e.seq)
    return h
  }
  ingest(e) {
    if (this.events.has(e.event_id)) return 'dup'
    this.events.set(e.event_id, e)
    return 'new'
  }
}

function cluster(opts = {}) {
  const nodes = { mum: new FakeNode('mum', opts), pun: new FakeNode('pun', opts), del: new FakeNode('del', opts) }
  const fetch = async (url, init = {}) => {
    const u = new URL(url)
    const node = nodes[u.hostname]
    const method = init.method || 'GET'
    const body = init.body ? JSON.parse(init.body) : undefined
    const reply = (status, json, headers = {}) => ({
      status, json: async () => json, headers: { get: (k) => headers[k] ?? null },
    })
    if (!node || !node.up) throw new Error('ECONNREFUSED')
    if (node.retryAfter && method !== 'GET') return reply(503, { error: 'busy' }, { 'Retry-After': String(node.retryAfter) })
    const p = u.pathname
    if (p === '/healthz') return node.v11 ? reply(200, { region: node.id, ok: true, level: 'HEALTHY' }) : reply(404, {})
    if (p === '/v1/health') return reply(200, { region: node.id, ok: true })
    if (p === '/v1/status') {
      const peers = {}
      for (const other of Object.values(nodes)) if (other !== node) peers[other.id] = { acked: { ...node.peerAcks } }
      return reply(200, { region: node.id, level: 'HEALTHY', heads: node.heads(), peers })
    }
    if (p === '/v1/carry') {
      if (!node.v11) return reply(404, {})
      return reply(200, { results: body.events.map((e) => ({ event_id: e.event_id, result: node.ingest(e) })) })
    }
    if (p === '/v1/events/has') {
      if (!node.v11) return reply(404, {})
      return reply(200, { missing: body.ids.filter((id) => !node.events.has(id)) })
    }
    if (p.startsWith('/v1/reports') && method !== 'GET') {
      node.seen.push({ path: p, op_id: body.op_id })
      if (body.force409) return reply(409, { live: ['x:1'] })
      let ev
      if (node.byOp.has(body.op_id)) ev = node.events.get(node.byOp.get(body.op_id))
      else {
        node.seq += 1
        ev = { event_id: `${node.id}:${node.seq}`, origin: node.id, seq: node.seq, op_id: body.op_id, hash: 'h' + node.seq, sig: 's' }
        node.events.set(ev.event_id, ev)
        node.byOp.set(body.op_id, ev.event_id)
      }
      if (node.dropAckOnce) { node.dropAckOnce = false; throw Object.assign(new Error('timeout'), { name: 'TimeoutError' }) }
      return reply(201, node.v11 ? { report: {}, event: ev } : { report: {}, event_id: ev.event_id })
    }
    return reply(404, {})
  }
  return { nodes, fetch }
}

function client(c, config = {}) {
  return new SyncShieldClient(
    [{ id: 'mum', url: 'http://mum' }, { id: 'pun', url: 'http://pun' }, { id: 'del', url: 'http://del' }],
    { fetch: c.fetch, config: { REQUEST_TIMEOUT_MS: 50, PROBE_TIMEOUT_MS: 50, ...config } },
  )
}

const add = (cl, extra = {}) => cl.call('POST', '/v1/reports/R-1/notes', { text: 'x', ...extra })

// ---- tests ---------------------------------------------------------------------
test('normal write goes to the preferred node and is tracked', async () => {
  const c = cluster(); const cl = client(c)
  const r = await add(cl)
  assert.equal(r.ok, true); assert.equal(r.node, 'mum'); assert.equal(r.tried, 0)
  assert.equal(cl.counters.acknowledged, 1)
  assert.equal(cl.snapshot().outbox.length, 1)
  assert.equal(cl.counters.errorsShown, 0)
})

test('node killed: next write fails over with the SAME op_id and shows no error', async () => {
  const c = cluster(); const cl = client(c)
  await add(cl)
  c.nodes.mum.up = false
  const r = await add(cl, { op_id: 'op-fixed' })
  assert.equal(r.ok, true); assert.equal(r.node, 'pun'); assert.equal(r.tried, 1)
  assert.equal(cl.counters.failovers, 1); assert.equal(cl.counters.errorsShown, 0)
  assert.deepEqual(c.nodes.pun.seen.map((s) => s.op_id), ['op-fixed'])
  assert.equal(cl.timeline.at(-1).failover, true)
})

test('lost ack: the retry on the next node reuses the same op_id', async () => {
  const c = cluster(); const cl = client(c)
  c.nodes.mum.dropAckOnce = true
  const r = await add(cl)
  assert.equal(r.ok, true); assert.equal(r.node, 'pun')
  const sentToMum = c.nodes.mum.seen[0].op_id
  assert.equal(c.nodes.pun.seen[0].op_id, sentToMum)
  assert.equal(r.opId, sentToMum)
})

test('zero loss: outbox is carried to the survivor before the first request there', async () => {
  const c = cluster(); const cl = client(c)
  for (let i = 0; i < 3; i++) await add(cl)               // mum:1..3 acknowledged, not replicated
  assert.equal(c.nodes.pun.events.size, 0)
  c.nodes.mum.up = false                                   // kill -9
  const r = await add(cl)
  assert.equal(r.node, 'pun')
  for (const id of ['mum:1', 'mum:2', 'mum:3']) assert.ok(c.nodes.pun.events.has(id), id)
  assert.equal(cl.counters.carriedNew, 3)
  await cl.probeAll()                                      // mum is down, so only survivors are asked
  const proof = await cl.presentOnSurvivors()
  assert.deepEqual(proof.missing, [])
  assert.equal(proof.acknowledged, proof.present)
  assert.equal(proof.acknowledged, 4)
})

test('carrying the same events again reports dup, never double-applies', async () => {
  const c = cluster(); const cl = client(c)
  await add(cl)
  c.nodes.pun.ingest(c.nodes.mum.events.get('mum:1'))     // replication already delivered it
  c.nodes.mum.up = false
  await add(cl)
  assert.equal(cl.counters.carriedDup, 1); assert.equal(cl.counters.carriedNew, 0)
  assert.equal([...c.nodes.pun.events.keys()].filter((k) => k === 'mum:1').length, 1)
})

test('4xx is an answer, not a failover case', async () => {
  const c = cluster(); const cl = client(c)
  const r = await cl.call('PATCH', '/v1/reports/R-1', { field: 'status', value: 'closed', force409: true })
  assert.equal(r.status, 409); assert.equal(r.node, 'mum'); assert.equal(r.tried, 0)
  assert.equal(cl.counters.answers4xx, 1); assert.equal(cl.counters.failovers, 0)
})

test('503 with Retry-After moves on to the next node', async () => {
  const c = cluster(); const cl = client(c)
  c.nodes.mum.retryAfter = 0.01
  const r = await add(cl)
  assert.equal(r.node, 'pun'); assert.equal(r.ok, true)
})

test('every node down: the action is queued on the device and replays later', async () => {
  const c = cluster(); const cl = client(c)
  for (const n of Object.values(c.nodes)) n.up = false
  const r = await add(cl, { op_id: 'op-queued' })
  assert.equal(r.queued, true); assert.equal(cl.snapshot().pending, 1)
  assert.equal(cl.counters.errorsShown, 1)
  c.nodes.del.up = true
  const out = await cl.flushPending()
  assert.equal(out[0].ok, true); assert.equal(out[0].node, 'del')
  assert.equal(c.nodes.del.seen[0].op_id, 'op-queued')
  assert.equal(cl.snapshot().pending, 0)
})

test('outbox drops an event once a peer acknowledged it, and is capped', async () => {
  const c = cluster(); const cl = client(c, { MAX_OUTBOX: 3 })
  for (let i = 0; i < 5; i++) await add(cl)
  assert.equal(cl.snapshot().outbox.length, 3)
  assert.equal(cl.counters.outboxEvicted, 2)
  c.nodes.mum.peerAcks = { mum: 4 }                        // peers hold mum up to seq 4
  await cl.pollStatus()
  assert.deepEqual(cl.snapshot().outbox.map((e) => e.event_id), ['mum:5'])
})

test('older backend: no /healthz, /v1/carry or /v1/events/has still works and says so', async () => {
  const c = cluster({ v11: false }); const cl = client(c)
  await cl.probeAll()
  assert.ok(cl.snapshot().nodes.every((n) => n.up))
  const r = await add(cl)
  assert.equal(r.ok, true)
  assert.equal(cl.snapshot().outbox.length, 0)             // no signed event in the body: nothing to carry
  c.nodes.mum.up = false
  const r2 = await add(cl)
  assert.equal(r2.node, 'pun')
  const proof = await cl.presentOnSurvivors()
  assert.equal(proof.estimated, true)                      // honest: derived from /v1/status heads
  assert.deepEqual(proof.missing, ['mum:1'])               // mum:1 was never replicated: the loss is visible
})

test('a recovered node is only routed to again after 3 good probes (failback)', async () => {
  const c = cluster(); const cl = client(c)
  await cl.probeAll()
  c.nodes.mum.up = false; await cl.probeAll()
  assert.equal(cl.order()[0].id, 'pun')
  c.nodes.mum.up = true
  await cl.probeAll(); await cl.probeAll()
  assert.equal(cl.order()[0].id, 'pun')                    // only 2 good probes so far
  await cl.probeAll()
  assert.equal(cl.order()[0].id, 'mum')
})

test('with nothing acknowledged yet the proof is empty and not "estimated"', async () => {
  const c = cluster({ v11: false }); const cl = client(c)
  const proof = await cl.presentOnSurvivors()
  assert.deepEqual(proof, { acknowledged: 0, present: 0, missing: [], estimated: false, survivors: ['mum', 'pun', 'del'] })
})
