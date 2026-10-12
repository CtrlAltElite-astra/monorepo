// T20 failover latency (owner: Miti). Plan v1.1 section 10: "from kill to first success on a survivor
// at most 1.5 s, p95 over 10 runs; report the real numbers".
//
//   node scripts/measure-failover.mjs [runs]        (needs: nodes on :8001/:8002 and the chaos agent on :9000)
//
// It uses the real browser SDK against real node processes:
//   A. node CRASHED (kill -9, connection refused)  -> the common case
//   B. node HUNG (accepts the connection, never answers) -> the worst case: the request timeout is paid
// and writes docs/failover-latency.md with the measured numbers.
import net from 'node:net'
import fs from 'node:fs'
import { SyncShieldClient, DEFAULTS } from '../src/sdk/syncshieldClient.js'

const RUNS = Number(process.argv[2] || 10)
const CHAOS = 'http://127.0.0.1:9000'
const ADMIN = { 'X-Admin-Key': 'dev-admin' }
const REGIONS = [{ id: 'mum', url: 'http://127.0.0.1:8001' }, { id: 'pun', url: 'http://127.0.0.1:8002' }]
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const now = () => performance.now()
const q = (a, p) => { const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.ceil(p * s.length) - 1)] }
const stats = (a) => ({ n: a.length, p50: q(a, 0.5), p95: q(a, 0.95), max: Math.max(...a), min: Math.min(...a) })
const f = (x) => (x === undefined ? '-' : x.toFixed(1))

const chaos = async (path) => (await fetch(CHAOS + path, { method: 'POST', headers: ADMIN })).json()
const up = async (u) => { try { return (await fetch(u + '/healthz', { signal: AbortSignal.timeout(500) })).ok } catch { return false } }
async function ensureBothUp() {
  for (const r of REGIONS) if (!(await up(r.url))) await chaos('/start/' + r.id)
  for (let i = 0; i < 50; i++) { if ((await up(REGIONS[0].url)) && (await up(REGIONS[1].url))) return; await sleep(200) }
  throw new Error('nodes did not come up')
}
const post = (r, path, body) => fetch(r.url + path, {
  method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': 'dev-officer' }, body: JSON.stringify(body),
}).then((x) => x.json())

// ---- A. crashed node ---------------------------------------------------------------------
const crashed = []
const crashedTried = []
for (let i = 1; i <= RUNS; i++) {
  await ensureBothUp()
  const sdk = new SyncShieldClient(REGIONS, { apiKey: 'dev-officer' })
  await sdk.probeAll()
  const cart = await sdk.call('POST', '/v1/reports', { title: 'latency ' + i, category: 'cart', severity: 1 })
  await sdk.call('POST', `/v1/reports/${cart.body.report.report_id}/notes`, { text: 'warm-up' })
  await chaos('/crash/mum')                                    // returns once the port is closed
  const t0 = now()
  const r = await sdk.call('POST', `/v1/reports/${cart.body.report.report_id}/notes`, { text: 'after the crash' })
  const dt = now() - t0
  if (!r.ok || r.node !== 'pun') throw new Error('run ' + i + ': expected success on pun, got ' + JSON.stringify({ ok: r.ok, node: r.node }))
  crashed.push(dt)
  crashedTried.push(r.tried)
  process.stdout.write(`crash run ${i}: ${dt.toFixed(0)} ms (tried ${r.tried}, carried ${sdk.counters.carriedNew + sdk.counters.carriedDup})\n`)
}
await ensureBothUp()

// ---- B. hung node (blackhole preferred) --------------------------------------------------
const hole = net.createServer((sock) => { sock.on('error', () => {}) })   // accepts, never replies
await new Promise((r) => hole.listen(9777, '127.0.0.1', r))
const hung = []
for (let i = 1; i <= RUNS; i++) {
  const sdk = new SyncShieldClient([{ id: 'hung', url: 'http://127.0.0.1:9777' }, ...REGIONS], { apiKey: 'dev-officer' })
  const t0 = now()
  const r = await sdk.call('POST', '/v1/reports', { title: 'hung ' + i, category: 'cart', severity: 1 })
  const dt = now() - t0
  if (!r.ok) throw new Error('hung run ' + i + ' failed')
  hung.push(dt)
  process.stdout.write(`hung run ${i}: ${dt.toFixed(0)} ms (answered by ${r.node}, tried ${r.tried})\n`)
}
hole.close()

const A = stats(crashed), B = stats(hung)
const md = `# Failover latency (test T20)

Measured with the real browser SDK (\`dashboard/src/sdk/syncshieldClient.js\`) against real node processes on one
laptop (Windows, Python ${'3.14'}, localhost). Script: \`dashboard/scripts/measure-failover.mjs\`. ${RUNS} runs each.
Plan target: **first success on a survivor within 1.5 s, p95**.

| scenario | what happens | p50 | p95 | max | verdict |
|---|---|---|---|---|---|
| A. node crashed (kill -9, connection refused) | the SDK is refused at once and retries on the next node | ${f(A.p50)} ms | ${f(A.p95)} ms | ${f(A.max)} ms | ${A.p95 <= 1500 ? 'meets 1.5 s' : 'MISSES 1.5 s'} |
| B. node hung (accepts, never answers) | the SDK waits one request timeout (${DEFAULTS.REQUEST_TIMEOUT_MS} ms) then retries | ${f(B.p50)} ms | ${f(B.p95)} ms | ${f(B.max)} ms | ${B.p95 <= 1500 ? 'meets 1.5 s' : 'MISSES 1.5 s'} |

Runs in scenario A needed on average ${(crashedTried.reduce((a, b) => a + b, 0) / crashedTried.length).toFixed(1)} failed attempt(s) before success.
The measured time starts when the crash completes (the port is closed) and ends at the first successful write on the
survivor, including the carry of the browser's outbox.

Honest limits: localhost has no real network latency; a hung node costs one request timeout (${DEFAULTS.REQUEST_TIMEOUT_MS} ms)
per failed attempt, so the worst case with N dead nodes ahead of a live one is about N x ${DEFAULTS.REQUEST_TIMEOUT_MS} ms plus one round trip.
`
fs.writeFileSync(new URL('../../docs/failover-latency.md', import.meta.url), md)
console.log('\nA crashed:', JSON.stringify(A), '\nB hung   :', JSON.stringify(B))
console.log('wrote docs/failover-latency.md')
