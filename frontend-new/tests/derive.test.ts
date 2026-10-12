import { describe, expect, it } from 'vitest'
import { derive } from '../src/sim/derive'
import { eventHash } from '../src/sim/crypto'
import type { EventEnvelope } from '../src/api/types'
import { canonical } from '../src/lib/util'

let clock = 1_000
function ev(origin: string, seq: number, type: EventEnvelope['type'], over: Partial<EventEnvelope> = {}): EventEnvelope {
  const e: EventEnvelope = {
    v: 1, event_id: `${origin}:${seq}`, origin, seq, report_id: 'R-aaaaaaaa', type, field: null, value: null, supersedes: [],
    hlc: [clock++, 0], priority: 1, op_id: null, observed: null, actor: 'a', role: 'officer', auth_mode: 'online', reason: null,
    wall: '2026-10-12T14:00:00Z', hash: '', sig: null, ...over,
  }
  e.hash = eventHash(e)
  return e
}
const CREATE = ev('mum', 1, 'CREATE', { value: { title: 'Alice', category: 'cart', severity: 1, status: 'open', assigned_team: null, location: null, description: '', people_estimate: null } })
const set = (origin: string, seq: number, field: string, value: any, supersedes: string[], over: Partial<EventEnvelope> = {}) => ev(origin, seq, 'SET', { field, value, supersedes, ...over })
const note = (origin: string, seq: number, text: string, over: Partial<EventEnvelope> = {}) => ev(origin, seq, 'NOTE', { value: { text }, ...over })

describe('derive v1 policy', () => {
  it('a write that saw the old value is an update, never a conflict', () => {
    const v = derive([CREATE, set('mum', 2, 'severity', 3, ['mum:1']), set('mum', 3, 'severity', 4, ['mum:2'])])
    expect(v.conflicts).toEqual([])
    expect(v.state.severity).toBe(4)
    expect(v.live.severity).toEqual(['mum:3'])
  })

  it('concurrent severity: the higher value wins, as a receipt only', () => {
    const v = derive([CREATE, set('mum', 2, 'severity', 5, ['mum:1']), set('pun', 1, 'severity', 4, ['mum:1'])])
    expect(v.state.severity).toBe(5)
    expect(v.conflicts).toHaveLength(1)
    expect(v.conflicts[0]).toMatchObject({ field: 'severity', rule: 'SEVERITY_HIGHEST', needs_review: false, provisional: 5 })
  })

  it('concurrent status: the most active wins and goes to review', () => {
    const v = derive([CREATE, set('mum', 2, 'status', 'closed', ['mum:1']), set('pun', 1, 'status', 'assigned', ['mum:1'])])
    expect(v.state.status).toBe('assigned')
    expect(v.conflicts[0]).toMatchObject({ rule: 'STATUS_MOST_ACTIVE', needs_review: true })
    expect(v.conflicts[0].candidates.map((c) => c.value).sort()).toEqual(['assigned', 'closed'])
  })

  it('closed and cancelled tie on the ladder: the latest of the two wins', () => {
    const v = derive([CREATE, set('mum', 2, 'status', 'closed', ['mum:1']), set('pun', 1, 'status', 'cancelled', ['mum:1'])])
    expect(v.state.status).toBe('cancelled')
  })

  it('concurrent fulfilment centres: the earliest holds and the double dispatch is flagged', () => {
    const v = derive([CREATE, set('mum', 2, 'assigned_team', 'Bhiwandi', ['mum:1']), set('pun', 1, 'assigned_team', 'Chakan', ['mum:1'])])
    expect(v.state.assigned_team).toBe('Bhiwandi')
    expect(v.conflicts[0]).toMatchObject({ rule: 'TEAM_EARLIEST', needs_review: true, flags: ['DOUBLE_DISPATCH'] })
  })

  it('concurrent titles: the latest wins with no review', () => {
    const v = derive([CREATE, set('mum', 2, 'title', 'Alicia', ['mum:1']), set('pun', 1, 'title', 'Alice B', ['mum:1'])])
    expect(v.state.title).toBe('Alice B')
    expect(v.conflicts[0]).toMatchObject({ rule: 'LATEST_HLC', needs_review: false })
  })

  it('people_estimate is the highest and never summed', () => {
    const v = derive([CREATE, set('mum', 2, 'people_estimate', 20, ['mum:1']), set('pun', 1, 'people_estimate', 25, ['mum:1'])])
    expect(v.state.people_estimate).toBe(25)
  })

  it('far apart delivery pins go to review, near ones do not', () => {
    const near = derive([CREATE, set('mum', 2, 'location', { lat_e6: 19076000, lon_e6: 72877000 }, ['mum:1']), set('pun', 1, 'location', { lat_e6: 19076100, lon_e6: 72877100 }, ['mum:1'])])
    const far = derive([CREATE, set('mum', 2, 'location', { lat_e6: 19076000, lon_e6: 72877000 }, ['mum:1']), set('pun', 1, 'location', { lat_e6: 19200000, lon_e6: 72877000 }, ['mum:1'])])
    expect(near.conflicts[0].needs_review).toBe(false)
    expect(far.conflicts[0].needs_review).toBe(true)
  })

  it('concurrent but equal values are not a conflict, and different fields never conflict', () => {
    expect(derive([CREATE, set('mum', 2, 'severity', 4, ['mum:1']), set('pun', 1, 'severity', 4, ['mum:1'])]).conflicts).toEqual([])
    const v = derive([CREATE, set('mum', 2, 'status', 'closed', ['mum:1']), set('pun', 1, 'severity', 5, ['mum:1'])])
    expect(v.conflicts).toEqual([])
    expect(v.state.status).toBe('closed')
    expect(v.state.severity).toBe(5)
  })

  it('a SET that arrives before its CREATE leaves the report incomplete', () => {
    expect(derive([set('pun', 1, 'severity', 4, ['mum:1'])]).incomplete).toBe(true)
    expect(derive([CREATE]).incomplete).toBe(false)
  })

  it('RESOLVE supersedes exactly its candidates', () => {
    const a = set('mum', 2, 'status', 'closed', ['mum:1'])
    const b = set('pun', 1, 'status', 'assigned', ['mum:1'])
    const r = ev('mum', 3, 'RESOLVE', { field: 'status', value: 'closed', supersedes: ['mum:2', 'pun:1'], reason: 'ok' })
    const v = derive([CREATE, a, b, r])
    expect(v.conflicts).toEqual([])
    expect(v.live.status).toEqual(['mum:3'])
    const late = set('del', 1, 'status', 'assigned', ['pun:1']) // never saw the resolution
    expect(derive([CREATE, a, b, r, late]).conflicts.map((c) => c.field)).toEqual(['status'])
  })

  it('is independent of arrival order and duplicates (convergence)', () => {
    const events = [
      CREATE, set('mum', 2, 'severity', 5, ['mum:1']), set('pun', 1, 'severity', 4, ['mum:1']), set('pun', 2, 'assigned_team', 'Chakan', ['mum:1']),
      set('mum', 3, 'assigned_team', 'Bhiwandi', ['mum:1']), note('pun', 3, 'x'), set('mum', 4, 'status', 'assigned', ['mum:1']),
    ]
    const want = canonical(derive(events))
    let seed = 7
    const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff)
    for (let i = 0; i < 60; i++) {
      const shuffled = [...events, ...events.filter(() => rnd() < 0.4)].sort(() => rnd() - 0.5)
      expect(canonical(derive(shuffled))).toBe(want)
    }
  })
})

describe('op_id group rule (a retry counts once)', () => {
  it('two notes carrying the same op_id collapse to one item', () => {
    const v = derive([CREATE, note('mum', 2, 'coffee', { op_id: 'op-1' }), note('pun', 1, 'coffee', { op_id: 'op-1' }), note('pun', 2, 'tea', { op_id: 'op-2' })])
    expect(v.state.notes.map((n) => n.text)).toEqual(['coffee', 'tea'])
  })

  it('two CREATEs of the same cart with the same op_id are one write, with no conflict', () => {
    const c1 = ev('mum', 1, 'CREATE', { op_id: 'op-c', value: { title: 'Alice', category: 'cart', severity: 1, status: 'open', assigned_team: null, location: null, description: '', people_estimate: null } })
    const c2 = ev('pun', 1, 'CREATE', { op_id: 'op-c', value: c1.value })
    const v = derive([c1, c2])
    expect(v.conflicts).toEqual([])
    expect(v.live.title).toHaveLength(1)
  })

  it('superseding one member of a group supersedes all of them', () => {
    const a = ev('mum', 2, 'SET', { field: 'severity', value: 3, supersedes: ['mum:1'], op_id: 'op-s' })
    const b = ev('pun', 1, 'SET', { field: 'severity', value: 3, supersedes: ['mum:1'], op_id: 'op-s' })
    const next = ev('mum', 3, 'SET', { field: 'severity', value: 4, supersedes: ['mum:2'] }) // saw only mum:2
    const v = derive([CREATE, a, b, next])
    expect(v.live.severity).toEqual(['mum:3'])
    expect(v.conflicts).toEqual([])
  })
})

describe('CLOSE_VS_NEW_INFO (an addition is never buried under a close)', () => {
  it('Order placed on one node and a new item on another opens a review, and the status stays closed', () => {
    const close = set('mum', 2, 'status', 'closed', ['mum:1'], { observed: ['mum:1'] })
    const gift = note('pun', 1, 'Gift card', { observed: ['mum:1'] }) // pun never saw the close
    const v = derive([CREATE, close, gift])
    expect(v.state.status).toBe('closed')
    expect(v.conflicts).toHaveLength(1)
    expect(v.conflicts[0]).toMatchObject({ field: 'status', relationship: 'CONCURRENT', rule: 'CLOSE_VS_NEW_INFO', needs_review: true, flags: ['CLOSE_VS_NEW_INFO'] })
    // the engine's shape: the close first, then the change it did not see (its value is the note text)
    expect(v.conflicts[0].candidates.map((c) => c.event_id)).toEqual(['mum:2', 'pun:1'])
    expect(v.conflicts[0].candidates.map((c) => c.value)).toEqual(['closed', expect.stringContaining('Gift card')])
  })

  it('a note written after seeing the close, or already seen by the close, does not trigger it', () => {
    const seenByClose = set('mum', 3, 'status', 'closed', ['mum:1'], { observed: ['pun:1'] })
    expect(derive([CREATE, note('pun', 1, 'x', { observed: ['mum:1'] }), seenByClose]).conflicts).toEqual([])
    const after = note('pun', 2, 'late', { observed: ['mum:3'] })
    expect(derive([CREATE, seenByClose, after]).conflicts).toEqual([])
  })

  it('a severity of 4 or more counts as new information; a lower one does not', () => {
    const close = set('mum', 2, 'status', 'closed', ['mum:1'], { observed: ['mum:1'] })
    expect(derive([CREATE, close, set('pun', 1, 'severity', 4, ['mum:1'], { observed: ['mum:1'] })]).conflicts.map((c) => c.rule)).toEqual(['CLOSE_VS_NEW_INFO'])
    expect(derive([CREATE, close, set('pun', 1, 'severity', 3, ['mum:1'], { observed: ['mum:1'] })]).conflicts).toEqual([])
  })

  it('a supervisor resolution that observed the new information clears it', () => {
    const close = set('mum', 2, 'status', 'closed', ['mum:1'], { observed: ['mum:1'] })
    const gift = note('pun', 1, 'Gift card', { observed: ['mum:1'] })
    const resolve = ev('mum', 3, 'RESOLVE', { field: 'status', value: 'closed', supersedes: ['mum:2'], reason: 'ship with it', observed: ['pun:1', 'mum:1'] })
    expect(derive([CREATE, close, gift, resolve]).conflicts).toEqual([])
  })

  it('events without evidence (older basic-build events) never trigger it', () => {
    const close = set('mum', 2, 'status', 'closed', ['mum:1']) // observed: null
    expect(derive([CREATE, close, note('pun', 1, 'x')]).conflicts).toEqual([])
  })
})
