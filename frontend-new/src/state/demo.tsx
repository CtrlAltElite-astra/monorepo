// The demo's state: ONE place that owns the API, the SDK, the polling, the bot and the actions.
// Every number the components show is taken from an API response held here; nothing is invented.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createApi, type Api } from '../api'
import type { CarryResult, EventEnvelope, HttpResult, Level, Mode, RegionId, RegionInfo, ReportView, ReviewItem, Role, Status } from '../api/types'
import { NetworkError } from '../api/types'
import { addNoteText, PRODUCTS, SHOPPERS, type Product, type Shopper } from '../domain/shop'
import { canonical, newReportId, sleep, uuid } from '../lib/util'
import { SyncShieldClient, type CarryNote, type SdkState } from '../sdk/syncshieldClient'
import { eventHash } from '../sim/crypto'
import { NODE_NAME } from '../components/ui'

export interface Snap { id: RegionId; ok: boolean; at: number; ms: number | null; error?: string; status?: Status; reports: ReportView[] }
export type Convergence = 'CONVERGED' | 'SYNCING' | 'UNKNOWN' | 'INCOMPATIBLE' | 'DIVERGED'
export interface Toast { id: number; kind: 'ok' | 'info' | 'warn' | 'bad'; text: string }
export interface LastResponse { method: string; path: string; status: number; body: unknown; at: number; ms: number }
export interface Heal { region: RegionId; isolatedMs: number; eventsSynced: number; conflicts: number; reviews: number; at: number }
export interface TamperRow { label: string; reason: string | undefined; result: CarryResult['result'] | 'error'; at: number }
export interface ProveStep { text: string; state: 'todo' | 'run' | 'done' | 'fail' }
export interface ProveResult { rows: { region: RegionId; event_digest: string; state_hash: string; policy_version: number; events: number }[]; verdict: Convergence; ms: number; cuts: string[]; writes: number; reviews: number }

const POLL_MS = 900
const AUTO_MS = 1100

interface Demo {
  api: Api
  mode: Mode
  regions: RegionInfo[]
  joined: RegionId[]
  sdk: SyncShieldClient
  sdkState: SdkState
  carryLog: CarryNote[]
  snaps: Partial<Record<RegionId, Snap>>
  review: Partial<Record<RegionId, ReviewItem[]>>
  last: Partial<Record<RegionId, { status?: LastResponse; reports?: LastResponse }>>
  presence: { present: number; checked: number; answered: number } | null
  convergence: Convergence
  viewRegion: RegionId | null
  heals: Heal[]
  chaosAvailable: boolean
  chaosRunning: Partial<Record<RegionId, boolean>>
  autoShoppers: boolean
  setAutoShoppers(v: boolean): void
  shopper: Shopper
  setShopper(s: Shopper): void
  cartFor(shopper: Shopper): ReportView | undefined
  pending: Record<string, boolean>
  saved: Record<string, { region: RegionId | null; ms: number; tried: number; queued: boolean; at: number }>
  toasts: Toast[]
  toast(kind: Toast['kind'], text: string): void
  dismissToast(id: number): void
  tamperRows: TamperRow[]
  proveSteps: ProveStep[]
  proveResult: ProveResult | null
  proving: boolean
  /** true when the item was saved (or kept on the device); false when it was refused */
  addToCart(shopper: Shopper, product: Product): Promise<boolean>
  writeField(shopper: Shopper, field: string, value: unknown): Promise<HttpResult | null>
  resolve(report: ReportView, field: string, value: unknown, candidates: string[], reason: string, as: Role): Promise<HttpResult>
  setLink(a: RegionId, b: RegionId, mode: 'up' | 'down' | 'slow'): Promise<void>
  crash(region: RegionId): Promise<void>
  start(region: RegionId): Promise<void>
  burst(region: RegionId, n: number): Promise<void>
  tamper(region: RegionId): Promise<void>
  proveIt(): Promise<void>
  history(region: RegionId, reportId: string): Promise<EventEnvelope[] | null>
  reset(startNodes?: number): void
}

const Ctx = createContext<Demo | null>(null)
export const useDemo = () => {
  const d = useContext(Ctx)
  if (!d) throw new Error('useDemo outside <DemoProvider>')
  return d
}

export function computeConvergence(snaps: Partial<Record<RegionId, Snap>>, joined: RegionId[]): Convergence {
  const list = joined.map((r) => snaps[r])
  if (!list.length || list.some((s) => !s || !s.ok || !s.status)) return 'UNKNOWN'
  const st = list.map((s) => s!.status!)
  if (new Set(st.map((s) => s.policy_version)).size > 1) return 'INCOMPATIBLE'
  const same = (k: 'event_digest' | 'state_hash') => new Set(st.map((s) => s[k])).size === 1
  if (same('event_digest') && same('state_hash')) return 'CONVERGED'
  const origins = new Set(st.flatMap((s) => Object.keys(s.heads)))
  const headsEqual = [...origins].every((o) => new Set(st.map((s) => s.heads[o] ?? 0)).size === 1)
  return headsEqual && !same('state_hash') ? 'DIVERGED' : 'SYNCING'
}

export function DemoProvider({ children, startNodes = 3 }: { children: ReactNode; startNodes?: number }) {
  const [nodes, setNodes] = useState(startNodes)
  const [epoch, setEpoch] = useState(0)
  const api = useMemo(() => createApi(undefined, nodes), [nodes, epoch]) // eslint-disable-line react-hooks/exhaustive-deps
  const sdk = useMemo(() => new SyncShieldClient(api.transport), [api])
  const regions = api.transport.regions
  const currentJoined = (): RegionId[] => (api.world ? [...api.world.joined] : regions.map((r) => r.id))
  const [joinedNow, setJoinedNow] = useState<RegionId[]>(currentJoined)
  useEffect(() => setJoinedNow(currentJoined()), [api]) // eslint-disable-line react-hooks/exhaustive-deps

  const [sdkState, setSdkState] = useState<SdkState>(() => sdk.getState())
  const [snaps, setSnaps] = useState<Partial<Record<RegionId, Snap>>>({})
  const [review, setReview] = useState<Partial<Record<RegionId, ReviewItem[]>>>({})
  const [last, setLast] = useState<Demo['last']>({})
  const [presence, setPresence] = useState<Demo['presence']>(null)
  const [heals, setHeals] = useState<Heal[]>([])
  const [chaosAvailable, setChaosAvailable] = useState(api.chaos.available)
  const [chaosRunning, setChaosRunning] = useState<Demo['chaosRunning']>({})
  const [autoShoppers, setAutoShoppers] = useState(false)
  const [shopper, setShopper] = useState<Shopper>('Alice')
  const [pending, setPending] = useState<Record<string, boolean>>({})
  const [saved, setSaved] = useState<Demo['saved']>({})
  const [toasts, setToasts] = useState<Toast[]>([])
  const [tamperRows, setTamperRows] = useState<TamperRow[]>([])
  const [proveSteps, setProveSteps] = useState<ProveStep[]>([])
  const [proveResult, setProveResult] = useState<ProveResult | null>(null)
  const [proving, setProving] = useState(false)
  const [carryLog, setCarryLog] = useState<CarryNote[]>([])

  const snapsRef = useRef(snaps); snapsRef.current = snaps
  const cartIds = useRef<Record<string, string>>({})
  const toastN = useRef(0)
  const levels = useRef<Partial<Record<RegionId, { level: Level | null; isoAt: number | null; events: number }>>>({})

  // reset everything when the world is rebuilt
  useEffect(() => {
    cartIds.current = {}; levels.current = {}
    setSnaps({}); setReview({}); setLast({}); setHeals([]); setTamperRows([]); setProveResult(null); setProveSteps([]); setPresence(null)
    setSdkState(sdk.getState())
    setCarryLog([])
    api.world?.start()
    sdk.start()
    const off = sdk.subscribe(() => setSdkState(sdk.getState()))
    const offCarry = sdk.onCarry((c) => {
      setCarryLog((l) => [c, ...l].slice(0, 8))
      if (c.unsupported) { toastRef.current('info', 'This node has no /v1/carry yet, so the browser cannot hand its events over.'); return }
      if (!c.sent) return
      const n = (k: string) => c.results.filter((r) => r.result === k).length
      toastRef.current(n('rejected') ? 'bad' : 'info', `Carrying ${c.sent} event${c.sent > 1 ? 's' : ''} → ${NODE_NAME[c.to]}: signatures checked, ${n('new')} new, ${n('dup')} already there${n('rejected') ? `, ${n('rejected')} rejected` : ''}.`)
    })
    return () => { off(); offCarry(); sdk.stop(); api.world?.stop() }
  }, [sdk, api]) // eslint-disable-line react-hooks/exhaustive-deps

  const toast = useCallback((kind: Toast['kind'], text: string) => {
    const id = ++toastN.current
    setToasts((t) => [...t.slice(-3), { id, kind, text }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === 'bad' ? 7000 : 4200)
  }, [])
  const dismissToast = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), [])
  const toastRef = useRef(toast); toastRef.current = toast

  // A write is only finished once the page has re-read the node: the next action's `expected` ids come from that read,
  // so acting on a snapshot older than your own write would (correctly) be refused as stale.
  const refreshRef = useRef<() => Promise<void>>(async () => {})
  const settle = () => Promise.race([refreshRef.current(), sleep(700)]).then(() => undefined) // never hold the UI for a slow or dead node

  // ---- polling: each node, as the browser sees it. Unreachable = UNKNOWN, never green. -------------------
  useEffect(() => {
    let stop = false
    let polling = false
    const runPoll = async () => {
      const members = currentJoined() // a node that joined live (Delhi) appears here
      setJoinedNow((prev) => (prev.length === members.length && prev.every((r, i) => r === members[i]) ? prev : members))
      await Promise.all(members.map(async (id) => {
        const started = Date.now()
        try {
          const [s, r] = await Promise.all([
            api.transport.request<Status>(id, 'GET', '/v1/status', { timeoutMs: 1500 }),
            api.transport.request<ReportView[]>(id, 'GET', '/v1/reports?limit=200', { timeoutMs: 1500 }),
          ])
          if (stop) return
          if (s.status !== 200 || r.status !== 200) throw new NetworkError(`HTTP ${s.status}/${r.status}`, 'refused')
          setSnaps((m) => ({ ...m, [id]: { id, ok: true, at: Date.now(), ms: Date.now() - started, status: s.body, reports: r.body } }))
          setLast((m) => ({ ...m, [id]: { status: { method: 'GET', path: '/v1/status', status: s.status, body: s.body, at: Date.now(), ms: s.ms }, reports: { method: 'GET', path: '/v1/reports?limit=200', status: r.status, body: r.body, at: Date.now(), ms: r.ms } } }))
          trackHeal(id, s.body, r.body)
        } catch (e) {
          if (stop) return
          setSnaps((m) => ({ ...m, [id]: { id, ok: false, at: Date.now(), ms: null, error: (e as Error).message, reports: [] } }))
        }
      }))
      if (stop) return
      // supervisor queue per node
      await Promise.all(members.map(async (id) => {
        try {
          const rv = await api.transport.request<ReviewItem[]>(id, 'GET', '/v1/review', { timeoutMs: 1500 })
          if (!stop && rv.status === 200) setReview((m) => ({ ...m, [id]: rv.body }))
          else if (!stop && rv.status === 404) setReview((m) => ({ ...m, [id]: [] }))
        } catch { /* a node that cannot answer shows as UNKNOWN through its snapshot */ }
      }))
      const cs = await api.chaos.state()
      if (!stop) {
        setChaosAvailable(api.chaos.available || !!cs)
        setChaosRunning(Object.fromEntries(Object.entries(cs?.regions ?? {}).map(([k, v]) => [k, v.running ?? v.up ?? false])))
      }
    }
    const poll = async () => { if (polling || stop) return; polling = true; try { await runPoll() } finally { polling = false } }
    refreshRef.current = async () => { while (polling && !stop) await sleep(40); await poll() } // wait out a poll that started before the write
    void poll()
    const t = setInterval(poll, POLL_MS)
    return () => { stop = true; clearInterval(t) }
  }, [api]) // eslint-disable-line react-hooks/exhaustive-deps

  /** Client-side heal summary (works in both modes): isolation start -> back to HEALTHY. */
  function trackHeal(id: RegionId, s: Status, reports: ReportView[]) {
    const prev = levels.current[id] ?? { level: null, isoAt: null, events: s.event_count }
    const now = Date.now()
    if (s.level === 'ISOLATED' && prev.isoAt === null) { prev.isoAt = now; prev.events = s.event_count }
    if (s.level === 'HEALTHY' && prev.isoAt !== null && now - prev.isoAt > 1500) {
      const conflicts = reports.reduce((n, r) => n + r.conflicts.length, 0)
      const reviews = reports.filter((r) => r.conflicts.some((c) => c.needs_review)).length
      setHeals((h) => [{ region: id, isolatedMs: now - prev.isoAt!, eventsSynced: Math.max(0, s.event_count - prev.events), conflicts, reviews, at: now }, ...h].slice(0, 6))
      prev.isoAt = null
    }
    prev.level = s.level
    levels.current[id] = prev
  }

  // ---- "acknowledged = present on survivors" -----------------------------------------------------------------
  useEffect(() => {
    let stop = false
    const t = setInterval(async () => {
      const p = await sdk.presentOnSurvivors().catch(() => null)
      if (!stop) setPresence(p)
    }, 1300)
    return () => { stop = true; clearInterval(t) }
  }, [sdk])

  // ---- derived -------------------------------------------------------------------------------------------------
  const convergence = useMemo(() => computeConvergence(snaps, joinedNow), [snaps, joinedNow])
  const viewRegion = useMemo<RegionId | null>(() => {
    if (sdkState.serving && snaps[sdkState.serving]?.ok) return sdkState.serving
    return joinedNow.find((r) => snaps[r]?.ok) ?? null
  }, [sdkState.serving, snaps, joinedNow])

  const cartFor = useCallback((who: Shopper): ReportView | undefined => {
    const known = cartIds.current[who]
    const pools = [viewRegion, ...joinedNow].filter((r): r is RegionId => !!r).map((r) => snapsRef.current[r]).filter((s) => s?.ok)
    for (const s of pools) {
      const hit = s!.reports.find((r) => (known ? r.report_id === known : r.state.title === who))
      if (hit) { cartIds.current[who] = hit.report_id; return hit }
    }
    return undefined
  }, [viewRegion, joinedNow])

  const markPending = (key: string, on: boolean) => setPending((p) => ({ ...p, [key]: on }))
  const remember = (who: string, res: HttpResult) => {
    const tl = sdk.getState().timeline.at(-1)
    if (tl) setSaved((m) => ({ ...m, [who]: { region: tl.region, ms: tl.ms, tried: tl.tried, queued: tl.queued, at: Date.now() } }))
    return res
  }

  /** Show a shopper-visible problem and count it. 409 / queued are answers, not errors. */
  const handleResult = (res: HttpResult, doing: string): boolean => {
    if (res.status === 202 && res.headers['x-queued-on-device']) { toast('info', 'Saved on your device. It will go through as soon as a node answers.'); return true }
    if (res.status >= 200 && res.status < 300) return true
    if (res.status === 409) { toast('warn', `${doing}: this cart changed while you were looking. It has been refreshed.`); return false }
    sdk.shopperError()
    toast('bad', `${doing} failed (${res.status}${(res.body as any)?.detail ? ': ' + (res.body as any).detail : ''}).`)
    return false
  }

  const addToCart = useCallback(async (who: Shopper, product: Product) => {
    const key = `add:${who}:${product.sku}`
    markPending(key, true)
    try {
      let cart = cartFor(who)
      let id = cart?.report_id ?? cartIds.current[who]
      if (!id) {
        id = newReportId()
        cartIds.current[who] = id
        const created = remember(who, await sdk.call('POST', '/v1/reports', { report_id: id, title: who, category: 'cart', severity: 1 }, { label: `${who}: new cart` }))
        if (!handleResult(created, 'Creating the cart')) { delete cartIds.current[who]; return false }
      }
      const res = remember(who, await sdk.call('POST', `/v1/reports/${id}/notes`, { text: addNoteText(product, who) }, { label: `${who}: add ${product.name}` }))
      const ok = handleResult(res, `Adding ${product.name}`)
      await settle()
      return ok
    } finally { markPending(key, false) }
  }, [cartFor, sdk]) // eslint-disable-line react-hooks/exhaustive-deps

  const writeField = useCallback(async (who: Shopper, field: string, value: unknown) => {
    const cart = cartFor(who)
    if (!cart) { toast('info', `${who} has no cart yet. Add something first.`); return null }
    const key = `set:${who}:${field}`
    markPending(key, true)
    try {
      const res = remember(who, await sdk.call('PATCH', `/v1/reports/${cart.report_id}`, { field, value, expected: cart.live[field] ?? [] }, { label: `${who}: ${field} → ${String(value)}` }))
      handleResult(res, 'Update')
      await settle()
      return res
    } finally { markPending(key, false) }
  }, [cartFor, sdk]) // eslint-disable-line react-hooks/exhaustive-deps

  const resolve = useCallback(async (report: ReportView, field: string, value: unknown, candidates: string[], reason: string, as: Role) => {
    const res = await sdk.call('POST', `/v1/reports/${report.report_id}/resolve`, { field, value, candidates, reason }, { as, label: `resolve ${field}` })
    if (res.status === 201 || res.status === 200) toast('ok', 'Resolved. The decision replicates like any event.')
    else if (res.status === 409) toast('warn', (res.body as any)?.error === 'no_conflict' ? 'Nothing left to resolve.' : 'Someone changed it first (409). The queue was refreshed.')
    else if (res.status === 403) toast('bad', 'Only a supervisor may resolve (403). Nothing was written.')
    else if (res.status !== 202) toast('bad', `Resolve failed (${res.status}).`)
    return res
  }, [sdk, toast])

  // ---- network and chaos ---------------------------------------------------------------------------------------------
  const setLink = useCallback(async (a: RegionId, b: RegionId, mode: 'up' | 'down' | 'slow') => {
    const body = mode === 'slow' ? { mode, delay_ms: 150, rate_bps: 2000 } : { mode }
    const results = await Promise.allSettled([[a, b], [b, a]].map(([from, to]) => api.transport.request(from as RegionId, 'POST', `/admin/links/${to}`, { body, as: 'admin', timeoutMs: 2500 })))
    const bad = results.filter((r) => r.status === 'rejected' || (r.status === 'fulfilled' && r.value.status >= 400))
    if (bad.length === 2) toast('bad', 'Could not change the link on either node. Is the admin key set and ENABLE_ADMIN on?')
    else if (bad.length) toast('warn', 'The link changed on one node only. A node that cannot be reached keeps its old setting.')
  }, [api, toast])

  const crash = useCallback(async (r: RegionId) => { try { await api.chaos.crash(r); toast('warn', `${r.toUpperCase()} was killed (kill -9).`) } catch { toast('bad', 'The chaos agent did not answer (:9000).') } }, [api, toast])
  const start = useCallback(async (r: RegionId) => { try { await api.chaos.start(r); toast('info', `Starting ${r.toUpperCase()}. It replays its log and pulls what it missed.`) } catch { toast('bad', 'The chaos agent did not answer (:9000).') } }, [api, toast])

  const burst = useCallback(async (r: RegionId, n: number) => {
    const target = cartFor(shopper)?.report_id ?? snapsRef.current[r]?.reports[0]?.report_id
    try {
      const res = await api.transport.request(r, 'POST', '/admin/burst', { body: { n, report_id: target }, as: 'admin', timeoutMs: 5000 })
      if (res.status === 404) toast('info', 'This backend has no /admin/burst yet (tier 2).')
      else if (res.status >= 400) toast('bad', `Burst refused (${res.status}).`)
      else toast('ok', `${n} bulk writes queued on ${r.toUpperCase()}.`)
    } catch { toast('bad', 'Burst did not reach the node.') }
  }, [api, cartFor, shopper, toast])

  /** Send forged events to a node through /v1/carry. They must all be rejected and change nothing. */
  const tamper = useCallback(async (region: RegionId) => {
    const donor = joinedNow.map((r) => snapsRef.current[r]).find((s) => s?.ok && s.reports.length)
    const target = donor?.reports[0]?.report_id
    if (!target || !donor) { toast('info', 'Nothing to tamper with yet.'); return }
    const hist = await api.transport.request<EventEnvelope[]>(donor.id, 'GET', `/v1/reports/${target}/history`, { timeoutMs: 1500 }).catch(() => null)
    const real = hist?.body?.filter((e) => e.sig).at(-1)
    if (!real) { toast('info', 'No signed event to forge from yet.'); return }
    const { sig_status: _a, carried: _b, ...base } = real
    const edited = { ...base, value: base.type === 'NOTE' ? { text: '{"kind":"ADD","sku":"GIFT-500","name":"Forged item","qty":99,"price":1,"user":"Mallory"}' } : base.type === 'CREATE' ? { ...base.value, title: 'Mallory' } : 'forged' }
    const stranger = { ...base, event_id: 'mallory:1', origin: 'mallory', seq: 1 }
    const forged = [
      { label: 'Edited body (hash no longer matches)', ev: edited },
      { label: 'Edited body with a recomputed hash (signature no longer matches)', ev: { ...edited, hash: await forgeHash(api.mode, edited) } },
      { label: 'Event from an origin that has no key', ev: { ...stranger, hash: await forgeHash(api.mode, stranger) } },
    ]
    const rows: TamperRow[] = []
    for (const f of forged) {
      try {
        const res = await api.transport.request<{ results: CarryResult[] }>(region, 'POST', '/v1/carry', { body: { events: [f.ev] }, timeoutMs: 2500 })
        if (res.status === 404) { toast('info', 'This backend has no /v1/carry yet.'); return }
        const r = res.body.results[0]
        rows.push({ label: f.label, reason: r?.reason, result: r?.result ?? 'error', at: Date.now() })
      } catch { rows.push({ label: f.label, reason: undefined, result: 'error', at: Date.now() }) }
    }
    setTamperRows((t) => [...rows.reverse(), ...t].slice(0, 9))
    toast(rows.every((r) => r.result === 'rejected') ? 'ok' : 'bad', rows.every((r) => r.result === 'rejected') ? 'All forged events were rejected. State unchanged.' : 'A forged event was NOT rejected!')
  }, [api, joinedNow, toast])

  const history = useCallback(async (region: RegionId, reportId: string) => {
    try {
      const res = await api.transport.request<EventEnvelope[]>(region, 'GET', `/v1/reports/${reportId}/history`, { timeoutMs: 2000 })
      return res.status === 200 ? res.body : null
    } catch { return null }
  }, [api])

  // ---- Prove it: scripted chaos, then identical digests -----------------------------------------------------------------
  const proveIt = useCallback(async () => {
    if (proving) return
    setProving(true); setProveResult(null)
    const alive = joinedNow.filter((r) => snapsRef.current[r]?.ok)
    if (alive.length < 2) { toast('info', 'Prove it needs at least two running nodes.'); setProving(false); return }
    const pairs: [RegionId, RegionId][] = joinedNow.flatMap((a, i) => joinedNow.slice(i + 1).map((b) => [a, b] as [RegionId, RegionId]))
    const steps: ProveStep[] = [
      { text: 'Create a proof cart and wait until every node holds it', state: 'todo' },
      { text: 'Cut one node off from all the others', state: 'todo' },
      { text: 'Change the same cart on both sides: one side places the order, the other adds an item and sets a different delivery speed', state: 'todo' },
      { text: 'Heal every link', state: 'todo' }, { text: 'Wait for every node to agree', state: 'todo' }, { text: 'Compare digests and state hashes', state: 'todo' },
    ]
    const mark = (i: number, state: ProveStep['state']) => { steps[i] = { ...steps[i], state }; setProveSteps([...steps]) }
    const started = Date.now()
    const cuts: string[] = []
    let writes = 0
    // These writes go straight to a chosen node (two devices on opposite sides of the cut). The SDK would send them all to ONE node.
    const direct = async (region: RegionId, method: string, path: string, body: Record<string, unknown>) => {
      const res = await api.transport.request(region, method, path, { body: { ...body, op_id: uuid() }, as: 'officer', timeoutMs: 3000 })
      if (res.status >= 400) throw new Error(`${method} ${path} on ${region}: ${res.status}`)
      writes++
      return res
    }
    const live = (region: RegionId, id: string, field: string) => snapsRef.current[region]?.reports.find((r) => r.report_id === id)?.live[field] ?? []
    try {
      mark(0, 'run')
      const [a, b] = [...alive].sort(() => Math.random() - 0.5)
      const id = newReportId()
      await direct(a, 'POST', '/v1/reports', { report_id: id, title: 'Proof cart', category: 'cart', severity: 1 })
      await direct(a, 'POST', `/v1/reports/${id}/notes`, { text: addNoteText(PRODUCTS[0], 'Proof cart') })
      for (let i = 0; i < 30 && !alive.every((r) => snapsRef.current[r]?.reports.some((x) => x.report_id === id)); i++) await sleep(300)
      if (!alive.every((r) => snapsRef.current[r]?.reports.some((x) => x.report_id === id))) throw new Error('the proof cart did not reach every node')
      mark(0, 'done'); mark(1, 'run')
      for (const other of joinedNow.filter((r) => r !== a)) { await setLink(a, other, 'down'); cuts.push(`${a}–${other}`) }
      mark(1, 'done'); mark(2, 'run')
      await sleep(400)
      await direct(a, 'PATCH', `/v1/reports/${id}`, { field: 'severity', value: 5, expected: live(a, id, 'severity') })
      await direct(a, 'PATCH', `/v1/reports/${id}`, { field: 'status', value: 'closed', expected: live(a, id, 'status') })
      await direct(b, 'POST', `/v1/reports/${id}/notes`, { text: addNoteText(PRODUCTS[1], 'Proof cart') })
      await direct(b, 'PATCH', `/v1/reports/${id}`, { field: 'severity', value: 2, expected: live(b, id, 'severity') })
      mark(2, 'done'); mark(3, 'run')
      for (const [x, y] of pairs) await setLink(x, y, 'up')
      mark(3, 'done'); mark(4, 'run')
      let verdict: Convergence = 'UNKNOWN'
      for (let i = 0; i < 40; i++) { await sleep(500); verdict = computeConvergence(snapsRef.current, joinedNow); if (verdict === 'CONVERGED') break }
      mark(4, verdict === 'CONVERGED' ? 'done' : 'fail'); mark(5, 'run')
      const rows = joinedNow.flatMap((r) => { const s = snapsRef.current[r]?.status; return s ? [{ region: r, event_digest: s.event_digest, state_hash: s.state_hash, policy_version: s.policy_version, events: s.event_count }] : [] })
      const reviews = (snapsRef.current[a]?.reports ?? []).filter((r) => r.conflicts.some((c) => c.needs_review)).length
      setProveResult({ rows, verdict, ms: Date.now() - started, cuts, writes, reviews })
      mark(5, verdict === 'CONVERGED' ? 'done' : 'fail')
    } catch (e) {
      toast('bad', `Prove it could not finish (${e instanceof Error ? e.message : 'error'}). Check the admin key and that every node is running.`)
      steps.forEach((s, i) => s.state === 'run' && mark(i, 'fail'))
    } finally {
      for (const [x, y] of pairs) await setLink(x, y, 'up').catch(() => undefined)
      setProving(false)
    }
  }, [api, joinedNow, proving, setLink, toast])

  const reset = useCallback((n?: number) => {
    if (n && n !== nodes) setNodes(n)
    setEpoch((e) => e + 1)
  }, [nodes])

  // ---- auto-shoppers -----------------------------------------------------------------------------------------------------
  const autoRef = useRef(addToCart); autoRef.current = addToCart
  useEffect(() => {
    if (!autoShoppers) return
    const t = setInterval(() => {
      const who = SHOPPERS[Math.floor(Math.random() * SHOPPERS.length)]
      void autoRef.current(who, PRODUCTS[Math.floor(Math.random() * PRODUCTS.length)])
    }, AUTO_MS)
    return () => clearInterval(t)
  }, [autoShoppers])

  const value: Demo = {
    api, mode: api.mode, regions, joined: joinedNow, sdk, sdkState, carryLog, snaps, review, last, presence, convergence, viewRegion, heals,
    chaosAvailable, chaosRunning, autoShoppers, setAutoShoppers, shopper, setShopper, cartFor, pending, saved, toasts, toast, dismissToast,
    tamperRows, proveSteps, proveResult, proving, addToCart, writeField, resolve, setLink, crash, start, burst, tamper, proveIt, history, reset,
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

/** The hash an attacker would recompute after editing an event: the simulator's in mock mode, real sha256 in live mode
 *  (canonical JSON of every key except hash and sig, exactly what the backend hashes). */
async function forgeHash(mode: Mode, ev: Partial<EventEnvelope>): Promise<string> {
  if (mode === 'mock') return eventHash(ev)
  const { hash: _h, sig: _s, ...rest } = ev as Record<string, unknown>
  const bytes = new TextEncoder().encode(canonical(rest))
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
}
