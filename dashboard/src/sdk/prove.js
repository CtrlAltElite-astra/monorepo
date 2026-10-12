// "Prove it" (owner: Miti). Plan v1.1 appendix A step 6, acceptance A8.
//
// Runs a short chaos script against the REAL nodes, then checks the claim we make:
// after random partitions and concurrent writes on different nodes, once the links are healed
//   1. every node reports the same event digest, state hash and policy version, and
//   2. every acknowledged write is present on every node.
//
// No backend change is needed: it only uses public endpoints. It is deliberately independent of
// the UI so it can be unit-tested with a fake cluster (prove.test.js).
//
// env = {
//   regions: [{id}],
//   write(nodeId, method, path, body)  -> {ok, status, body}   direct to ONE node, no failover
//   setPair(aId, bId, mode, extra)     -> Promise              both directions
//   status(nodeId)                     -> /v1/status body | null (unreachable)
//   has(nodeId, ids)                   -> {missing: [...]} | null
//   report(nodeId, reportId)           -> report view | null
//   sleep(ms), now(), rng(), log(msg)
// }
const SKUS = ['SANDAL-01', 'LAMP-01', 'BOTTLE-01', 'BAG-01', 'KURTA-01', 'GIFT-500', 'FCOFF-500', 'CHAI-01']
const CENTRES = ['Bhiwandi', 'Chakan', 'Gurgaon']
const STATUSES = ['open', 'assigned', 'resolved', 'closed']

const pick = (rng, xs) => xs[Math.floor(rng() * xs.length)]
const hex8 = (rng) => Array.from({ length: 8 }, () => Math.floor(rng() * 16).toString(16)).join('')

export const DEFAULT_OPTS = { rounds: 3, writesPerRound: 6, settleMs: 25000, pollMs: 400 }

export async function proveConvergence(env, options = {}) {
  const o = { ...DEFAULT_OPTS, ...options }
  const { regions, sleep, now, rng, log } = env
  const ids = regions.map((r) => r.id)
  const pairs = ids.flatMap((a, i) => ids.slice(i + 1).map((b) => [a, b]))
  const acked = []
  const out = {
    ok: false, reason: '', rounds: o.rounds, partitions: 0, writes: 0, rejected: 0, acknowledged: 0,
    convergedMs: null, nodes: {}, missing: {}, conflicts: [], quarantinedDelta: 0, log: [],
  }
  const say = (m) => { out.log.push(m); log && log(m) }

  const statuses = async () => Object.fromEntries(await Promise.all(ids.map(async (id) => [id, await env.status(id)])))
  const healAll = async () => { for (const [a, b] of pairs) await env.setPair(a, b, 'up') }
  const same = (st, k) => new Set(ids.map((id) => st[id] && st[id][k])).size === 1

  // 1. everything must be reachable to prove anything
  let st = await statuses()
  const down = ids.filter((id) => !st[id])
  if (down.length) {
    out.reason = `${down.join(', ')} unreachable: restart it first, then run the proof`
    say(out.reason)
    return out
  }
  const quarantined0 = ids.reduce((n, id) => n + (st[id].quarantined || 0), 0)

  // 2. start from a healed, converged state
  say('healing every link')
  await healAll()
  const waitConverged = async (budget) => {
    const t0 = now()
    for (;;) {
      st = await statuses()
      const reach = ids.every((id) => st[id])
      if (reach && same(st, 'event_digest') && same(st, 'state_hash') && same(st, 'policy_version')) return now() - t0
      if (now() - t0 > budget) return null
      await sleep(o.pollMs)
    }
  }
  if ((await waitConverged(o.settleMs)) === null) {
    out.reason = 'nodes did not converge even before the test started'
    say(out.reason)
    return out
  }

  // 3. one shared cart, created on the first node and waited for everywhere
  const rid = 'R-' + hex8(rng)
  const created = await env.write(ids[0], 'POST', '/v1/reports', {
    title: 'Prove-it ' + rid.slice(2), category: 'cart', severity: 1, report_id: rid, op_id: 'prove-' + rid,
  })
  if (!created.ok) {
    out.reason = `could not create the test cart on ${ids[0]} (HTTP ${created.status})`
    say(out.reason)
    return out
  }
  acked.push(created.body.event_id)
  out.acknowledged += 1
  say(`created test cart ${rid} on ${ids[0]}`)
  if ((await waitConverged(o.settleMs)) === null) {
    out.reason = 'the test cart did not replicate before the first partition'
    return out
  }

  // 4. partitions + concurrent writes on different nodes
  const writeOnce = async (nodeId) => {
    const r = rng()
    let res
    if (r < 0.3) res = await env.write(nodeId, 'PATCH', `/v1/reports/${rid}`, { field: 'severity', value: 1 + Math.floor(rng() * 5), op_id: 'p-' + hex8(rng) })
    else if (r < 0.5) res = await env.write(nodeId, 'PATCH', `/v1/reports/${rid}`, { field: 'status', value: pick(rng, STATUSES), op_id: 'p-' + hex8(rng) })
    else if (r < 0.65) res = await env.write(nodeId, 'PATCH', `/v1/reports/${rid}`, { field: 'assigned_team', value: pick(rng, CENTRES), op_id: 'p-' + hex8(rng) })
    else {
      const sku = pick(rng, SKUS)
      const op = 'p-' + hex8(rng)
      res = await env.write(nodeId, 'POST', `/v1/reports/${rid}/notes`, {
        text: JSON.stringify({ kind: 'ADD', sku, name: sku, qty: 1, price: 0, user: 'prove-it', op }), op_id: op,
      })
    }
    out.writes += 1
    if (res.ok) { acked.push(res.body.event_id); out.acknowledged += 1 } else out.rejected += 1
  }

  let lastHealAt = now()
  for (let round = 1; round <= o.rounds; round++) {
    const [a, b] = pick(rng, pairs)
    const mode = rng() < 0.3 ? 'slow' : 'down'
    say(`round ${round}/${o.rounds}: ${mode === 'down' ? 'cut' : 'slow'} ${a} <-> ${b}`)
    await env.setPair(a, b, mode, mode === 'slow' ? { delay_ms: 400, rate_bps: 4000 } : {})
    out.partitions += 1
    await sleep(600)
    for (let i = 0; i < o.writesPerRound; i++) {
      // bias towards the two ends of the cut so both sides really diverge
      const node = rng() < 0.7 ? pick(rng, [a, b]) : pick(rng, ids)
      await writeOnce(node)
      await sleep(80)
    }
    say(`  ${o.writesPerRound} writes done; healing`)
    await healAll()
    lastHealAt = now()
    if (round < o.rounds) await sleep(300)  // the last round is NOT padded: its convergence time is measured
  }

  // 5. heal everything and wait for convergence
  say('all links healed, waiting for convergence')
  await healAll()
  const waited = await waitConverged(o.settleMs)
  const ms = waited === null ? null : now() - lastHealAt   // from the real last heal, not from this poll
  out.convergedMs = ms
  st = await statuses()
  for (const id of ids) {
    out.nodes[id] = st[id]
      ? { level: st[id].level, event_count: st[id].event_count, event_digest: st[id].event_digest,
          state_hash: st[id].state_hash, policy_version: st[id].policy_version, quarantined: st[id].quarantined }
      : null
  }
  out.quarantinedDelta = ids.reduce((n, id) => n + ((st[id] && st[id].quarantined) || 0), 0) - quarantined0

  // 6. completeness: every acknowledged write must be present on every node
  for (const id of ids) {
    const r = await env.has(id, acked)
    out.missing[id] = r ? r.missing : null
  }

  // 7. what the policy did with the concurrent edits (informational)
  const rep = await env.report(ids[0], rid)
  if (rep) out.conflicts = (rep.conflicts || []).map((c) => ({ field: c.field, rule: c.rule, values: c.values }))

  const converged = ms !== null
  const complete = ids.every((id) => Array.isArray(out.missing[id]) && out.missing[id].length === 0)
  out.ok = converged && complete && out.quarantinedDelta === 0
  out.reason = out.ok
    ? 'all nodes hold identical events and state; every acknowledged write is present everywhere'
    : !converged ? 'nodes did not converge within the time budget'
      : !complete ? 'some acknowledged writes are missing on some node'
        : 'events were quarantined during the run'
  say(out.ok ? 'PASS' : 'FAIL: ' + out.reason)
  return out
}
