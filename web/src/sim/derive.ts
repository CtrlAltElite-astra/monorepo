// derive() v1.1 for MOCK MODE ONLY: the in-browser stand-in for the backend's pure derive function
// (plan section 6.5 and 6.6). Live mode never calls this; the UI renders what the API returns.
import type { Candidate, EventEnvelope, Receipt } from '../api/types'
import { canonical } from '../lib/util'

export const POLICY_VERSION = 1

const FIELDS = ['title', 'description', 'category', 'location', 'severity', 'status', 'assigned_team', 'people_estimate'] as const
type Field = (typeof FIELDS)[number]

/** open < assigned < resolved < closed = cancelled: the lowest (most active) wins a status conflict. */
const LADDER: Record<string, number> = { open: 0, assigned: 1, resolved: 2, closed: 3, cancelled: 3 }

export interface Derived {
  state: Record<string, any> & { notes: { id: string; text: string }[] }
  live: Record<string, string[]>
  conflicts: Receipt[]
  incomplete: boolean
  policy_version: number
}

/** Total order for tie-breaks: (hlc, origin, seq). Clocks break ties; they never decide safety. */
export function order(a: EventEnvelope, b: EventEnvelope): number {
  return a.hlc[0] - b.hlc[0] || a.hlc[1] - b.hlc[1] || (a.origin < b.origin ? -1 : a.origin > b.origin ? 1 : 0) || a.seq - b.seq
}

const valueOf = (e: EventEnvelope, f: string) => (e.type === 'CREATE' ? e.value?.[f] : e.value)

function metres(a: { lat_e6: number; lon_e6: number }, b: { lat_e6: number; lon_e6: number }): number {
  const rad = (v: number) => (v / 1e6) * (Math.PI / 180)
  const dLat = rad(b.lat_e6 - a.lat_e6)
  const dLon = rad(b.lon_e6 - a.lon_e6)
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat_e6)) * Math.cos(rad(b.lat_e6)) * Math.sin(dLon / 2) ** 2
  return 2 * 6371000 * Math.asin(Math.sqrt(h))
}

interface Choice { value: any; rule: string; review: boolean; flags: string[] }

/** Policy v1 (plan 6.6): the provisional value of a field whose live writes disagree. Pure and deterministic. */
function resolveField(field: Field, live: EventEnvelope[]): Choice {
  const sorted = [...live].sort(order)
  const latest = sorted[sorted.length - 1]
  switch (field) {
    case 'severity':
      return { value: Math.max(...live.map((e) => valueOf(e, field))), rule: 'SEVERITY_HIGHEST', review: false, flags: [] }
    case 'status': {
      let best = sorted[0]
      for (const e of sorted) {
        const rank = LADDER[valueOf(e, field)] ?? 0
        const bestRank = LADDER[valueOf(best, field)] ?? 0
        if (rank < bestRank || (rank === bestRank && order(e, best) > 0)) best = e
      }
      return { value: valueOf(best, field), rule: 'STATUS_MOST_ACTIVE', review: true, flags: [] }
    }
    case 'assigned_team': {
      const named = sorted.filter((e) => valueOf(e, field))
      return { value: valueOf((named[0] ?? sorted[0]), field), rule: 'TEAM_EARLIEST', review: true, flags: ['DOUBLE_DISPATCH'] }
    }
    case 'people_estimate': {
      const nums = live.map((e) => valueOf(e, field)).filter((v) => typeof v === 'number')
      return { value: nums.length ? Math.max(...nums) : null, rule: 'PEOPLE_HIGHEST', review: true, flags: [] }
    }
    case 'location': {
      const pins = live.map((e) => valueOf(e, field)).filter(Boolean)
      let far = false
      for (let i = 0; i < pins.length; i++) for (let j = i + 1; j < pins.length; j++) if (metres(pins[i], pins[j]) >= 200) far = true
      return { value: valueOf(latest, field), rule: 'LOCATION_LATEST', review: far, flags: far ? ['PINS_FAR_APART'] : [] }
    }
    default:
      return { value: valueOf(latest, field), rule: 'LATEST_HLC', review: false, flags: [] }
  }
}

const candidate = (e: EventEnvelope, f: string, kind?: string): Candidate & { kind?: string; field?: string } => ({
  event_id: e.event_id, value: valueOf(e, f), origin: e.origin, actor: e.actor, wall: e.wall, ...(kind ? { kind, field: f } : {}),
})

export function derive(input: EventEnvelope[]): Derived {
  const byId = new Map<string, EventEnvelope>()
  for (const e of input) byId.set(e.event_id, e)
  const evs = [...byId.values()]

  // op_id groups: events sharing an op_id are ONE write (a retry that produced two events).
  const groups = new Map<string, string[]>()
  for (const e of evs) if (e.op_id) groups.set(e.op_id, [...(groups.get(e.op_id) ?? []), e.event_id])
  const groupOf = (id: string) => {
    const op = byId.get(id)?.op_id
    return op && (groups.get(op)?.length ?? 0) > 1 ? groups.get(op)! : [id]
  }
  const expand = (ids: Iterable<string>) => {
    const out = new Set<string>()
    for (const id of ids) for (const member of groupOf(id)) out.add(member)
    return out
  }
  const onePerGroup = (list: EventEnvelope[]) => {
    const seen = new Set<string>()
    return list.filter((e) => {
      const key = e.op_id && (groups.get(e.op_id)?.length ?? 0) > 1 ? 'op:' + e.op_id : e.event_id
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }

  const out: Derived = {
    state: { notes: [] } as any, live: {}, conflicts: [], policy_version: POLICY_VERSION,
    incomplete: !evs.some((e) => e.type === 'CREATE'),
  }
  const liveEvents: Record<string, EventEnvelope[]> = {}
  for (const f of FIELDS) {
    const cands = evs.filter((e) => (e.type === 'CREATE' && e.value && f in e.value) || ((e.type === 'SET' || e.type === 'RESOLVE') && e.field === f))
    const gone = expand(cands.flatMap((e) => e.supersedes))
    const live = onePerGroup(cands.filter((e) => !gone.has(e.event_id)).sort(order))
    liveEvents[f] = live
    out.live[f] = live.map((e) => e.event_id)
    if (!live.length) { out.state[f] = null; continue }
    const distinct = new Set(live.map((e) => canonical(valueOf(e, f))))
    if (distinct.size === 1) { out.state[f] = valueOf(live[0], f); continue }
    const choice = resolveField(f, live)
    out.state[f] = choice.value
    out.conflicts.push({
      field: f, relationship: 'CONCURRENT', candidates: live.map((e) => candidate(e, f)), rule: choice.rule,
      policy_version: POLICY_VERSION, provisional: choice.value, needs_review: choice.review, flags: choice.flags,
    })
  }
  out.state.notes = onePerGroup(evs.filter((e) => e.type === 'NOTE').sort(order)).map((e) => ({ id: e.event_id, text: e.value?.text ?? '' }))

  // CLOSE_VS_NEW_INFO: a live close and a NOTE / severity >= 4 write that did not observe each other.
  const closes = liveEvents.status.filter((e) => ['closed', 'cancelled'].includes(valueOf(e, 'status')) && e.observed !== null)
  const info = [...evs.filter((e) => e.type === 'NOTE'), ...liveEvents.severity.filter((e) => valueOf(e, 'severity') >= 4)]
  const unseen = new Map<string, EventEnvelope[]>()
  for (const c of closes) {
    for (const i of info) {
      if (!c.observed!.includes(i.event_id) && !(i.observed ?? []).includes(c.event_id)) {
        unseen.set(c.event_id, [...(unseen.get(c.event_id) ?? []), i])
      }
    }
  }
  if (unseen.size) {
    const closing = closes.filter((c) => unseen.has(c.event_id))
    const news = onePerGroup([...new Map([...unseen.values()].flat().map((i) => [i.event_id, i])).values()].sort(order))
    const extra = [...closing.map((c) => candidate(c, 'status', 'close')), ...news.map((i) => ({
      event_id: i.event_id, value: i.type === 'NOTE' ? i.value?.text : i.value, origin: i.origin, actor: i.actor, wall: i.wall,
      kind: 'new_info', field: i.type === 'NOTE' ? 'notes' : 'severity',
    }))]
    const existing = out.conflicts.find((c) => c.field === 'status')
    if (existing) {
      existing.flags = [...existing.flags, 'CLOSE_VS_NEW_INFO']
      existing.needs_review = true
      existing.candidates.push(...extra.filter((x) => !existing.candidates.some((c) => c.event_id === x.event_id)))
    } else {
      out.conflicts.push({
        field: 'status', relationship: 'CLOSE_VS_NEW_INFO', candidates: extra, rule: 'CLOSE_VS_NEW_INFO',
        policy_version: POLICY_VERSION, provisional: out.state.status, needs_review: true, flags: ['CLOSE_VS_NEW_INFO'],
      })
    }
  }
  return out
}
