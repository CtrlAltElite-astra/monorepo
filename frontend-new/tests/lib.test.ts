import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { allowanceFrom, fullAllowance, remainingNow, secondsUntilNext } from '../src/lib/allowance'
import { compareSentence, cellOf, parseScaleResult } from '../src/lib/scale'
import { addNoteText, cartItems, HOME_REGION, parseNote, PRODUCTS, RESTRICTED_PRODUCT, SHOPPERS } from '../src/domain/shop'

describe('a shopper\'s requests left', () => {
  const at = 1_000_000
  it('reads X-RateLimit-* from a write\'s answer, and nothing when the limit is off', () => {
    expect(allowanceFrom({ 'x-ratelimit-limit': '10', 'x-ratelimit-remaining': '7' }, null, 1, at)).toEqual({ limit: 10, remaining: 7, at, refillPerS: 1 })
    expect(allowanceFrom({}, null, 1, at)).toBeNull()
    expect(allowanceFrom({ 'x-ratelimit-limit': '10' }, null, 1, at)).toBeNull()
    expect(allowanceFrom({ 'x-ratelimit-limit': '0', 'x-ratelimit-remaining': '0' }, null, 1, at)).toBeNull()
    expect(allowanceFrom({ 'x-ratelimit-limit': 'x', 'x-ratelimit-remaining': '3' }, null, 1, at)).toBeNull()
  })

  it('keeps the wait a 429 asked for', () => {
    expect(allowanceFrom({ 'x-ratelimit-limit': '10', 'x-ratelimit-remaining': '0' }, { error: 'rate_limited', retry_after_ms: 800 }, 1, at)).toMatchObject({ remaining: 0, retryAfterMs: 800 })
  })

  it('extrapolates the refill between two answers, never beyond the bucket, never below zero', () => {
    const a = { limit: 10, remaining: 2, at, refillPerS: 1 }
    expect(remainingNow(a, at)).toBe(2)
    expect(remainingNow(a, at + 3500)).toBe(5)
    expect(remainingNow(a, at + 60_000)).toBe(10)
    expect(remainingNow(a, at - 5000)).toBe(2) // a clock that went backwards refills nothing
    expect(remainingNow({ ...a, remaining: 0 }, at)).toBe(0)
    expect(remainingNow({ limit: 0, remaining: 0, at, refillPerS: 1 }, at)).toBe(0)
    expect(remainingNow({ ...a, refillPerS: 0.5 }, at + 4000)).toBe(4)
  })

  it('says how many whole seconds until the next request is allowed', () => {
    const empty = { limit: 10, remaining: 0, at, refillPerS: 1 }
    expect(secondsUntilNext(empty, at)).toBe(1)
    expect(secondsUntilNext(empty, at + 400)).toBe(1)
    expect(secondsUntilNext(empty, at + 1000)).toBe(0)
    expect(secondsUntilNext({ ...empty, refillPerS: 0.25 }, at)).toBe(4)
    expect(secondsUntilNext({ limit: 10, remaining: 3, at, refillPerS: 1 }, at)).toBe(0)
  })

  it('an allowance that never refills says so: nothing comes back and the wait is endless', () => {
    const a = allowanceFrom({ 'x-ratelimit-limit': '3', 'x-ratelimit-remaining': '0' }, { retry_after_ms: 3_600_000 }, 0, at)!
    expect(a.refillPerS).toBe(0)
    expect(remainingNow(a, at + 86_400_000)).toBe(0)
    expect(secondsUntilNext(a, at + 86_400_000)).toBe(Infinity)
    expect(secondsUntilNext({ ...a, remaining: 2 }, at)).toBe(0)
    expect(remainingNow(fullAllowance(3, 0, at), at + 60_000)).toBe(3)
  })

  it('a shopper who has not written since the limit went on has a full bucket', () => {
    expect(remainingNow(fullAllowance(10, 1, at), at + 5000)).toBe(10)
  })
})

describe('the measured scaling result', () => {
  const good = {
    measured_at: '2026-10-13T16:00:00+05:30', machine: 'laptop, 8 cores', writes: 300,
    vertical: [{ power: 'low', writes_per_s: 210, p50_ms: 40, p95_ms: 90 }, { power: 'medium', writes_per_s: 640, p50_ms: 14, p95_ms: 30 }, { power: 'high', writes_per_s: 810, p50_ms: 11, p95_ms: 24 }],
    horizontal: [{ regions: 1, writes_per_s: 210, p95_ms: 90 }, { regions: 2, writes_per_s: 390, p95_ms: 70 }, { regions: 3, writes_per_s: 480, p95_ms: 66 }],
    compare: { one_high: { writes_per_s: 810, p50_ms: 11, p95_ms: 24 }, three_low: { writes_per_s: 480, p95_ms: 66 } },
  }

  it('reads every number from the file and none from anywhere else', () => {
    const r = parseScaleResult(good)!
    expect(r).toMatchObject({ measuredAt: '2026-10-13T16:00:00+05:30', machine: 'laptop, 8 cores', writes: 300 })
    expect(r.vertical.map((v) => v.power)).toEqual(['low', 'medium', 'high'])
    expect(r.vertical[1].cell).toEqual({ writes_per_s: 640, p50_ms: 14, p95_ms: 30 })
    expect(r.horizontal.map((h) => h.regions)).toEqual([1, 2, 3])
    expect(r.horizontal[2].cell).toEqual({ writes_per_s: 480, p50_ms: null, p95_ms: 66 })
    expect(r.oneHigh).toEqual({ writes_per_s: 810, p50_ms: 11, p95_ms: 24 })
  })

  it('a missing file (the dev server\'s HTML shell, a 404, a stray value) is "not measured yet", never a number', () => {
    for (const raw of [null, undefined, 'x', 42, [], {}, { hello: 1 }, '<!doctype html>']) expect(parseScaleResult(raw)).toBeNull()
  })

  it('a mode the benchmark could not run is "unsupported", and so is a placeholder zero', () => {
    const r = parseScaleResult({ vertical: 'unsupported', horizontal: [{ regions: 1, writes_per_s: 0 }, { regions: 3, writes_per_s: 300 }], compare: { one_high: 'unsupported', three_low: { writes_per_s: 300 } } })!
    expect(r.vertical.map((v) => v.cell)).toEqual(['unsupported', 'unsupported', 'unsupported'])
    expect(r.horizontal.map((h) => h.cell === 'unsupported')).toEqual([true, false])
    expect(r.oneHigh).toBe('unsupported')
    expect(compareSentence(r)).toBeNull() // nothing to compare
    expect(cellOf({ unsupported: true, writes_per_s: 500 })).toBe('unsupported')
    expect(cellOf({ status: 'unsupported' })).toBe('unsupported')
    expect(cellOf({ writes_per_s: -3 })).toBe('unsupported')
    expect(cellOf({ writes_per_s: 'fast' })).toBe('unsupported')
  })

  it('says what the numbers say, whichever way they fall', () => {
    expect(compareSentence(parseScaleResult(good)!)).toBe('One region at high power was 1.7 times faster than three regions at low power.')
    const flipped = parseScaleResult({ ...good, compare: { one_high: { writes_per_s: 300 }, three_low: { writes_per_s: 600 } } })!
    expect(compareSentence(flipped)).toBe('Three regions at low power were 2.0 times faster than one region at high power.')
    const tie = parseScaleResult({ ...good, compare: { one_high: { writes_per_s: 500 }, three_low: { writes_per_s: 510 } } })!
    expect(compareSentence(tie)).toBe('One strong region and three ordinary ones came out about the same.')
  })
})

describe('the result file that is in the repository', () => {
  // whatever the benchmark last wrote: today's file says power was unsupported, a later run fills it in. The console must show exactly that.
  const raw = JSON.parse(readFileSync(new URL('../public/scale-result.json', import.meta.url), 'utf-8'))
  const r = parseScaleResult(raw)!
  const says = (row: { status?: string; writes_per_s?: number | null }) => (row.status === 'unsupported' || !(typeof row.writes_per_s === 'number' && row.writes_per_s > 0) ? 'unsupported' : row.writes_per_s)

  it('is read as a result file', () => {
    expect(r).not.toBeNull()
    expect(r.writes).toBe(raw.writes)
    expect(r.machine).toBe(raw.machine)
  })

  it('every cell is the number in the file, or "unsupported" where the file says it could not be measured', () => {
    expect(r.vertical.map((v) => v.power)).toEqual(raw.vertical.map((v: { power: string }) => v.power))
    raw.vertical.forEach((row: any, i: number) => expect(r.vertical[i].cell === 'unsupported' ? 'unsupported' : (r.vertical[i].cell as { writes_per_s: number }).writes_per_s).toBe(says(row)))
    expect(r.horizontal.map((h) => h.regions)).toEqual(raw.horizontal.map((h: { regions: number }) => h.regions))
    raw.horizontal.forEach((row: any, i: number) => expect(r.horizontal[i].cell === 'unsupported' ? 'unsupported' : (r.horizontal[i].cell as { writes_per_s: number }).writes_per_s).toBe(says(row)))
    for (const [key, cell] of [['one_high', r.oneHigh], ['three_low', r.threeLow]] as const) {
      expect(cell === 'unsupported' ? 'unsupported' : (cell as { writes_per_s: number }).writes_per_s).toBe(says(raw.compare[key]))
    }
  })
})

describe('the shop hooks for the joining steps', () => {
  it('every shopper has a home region: Alice and Bob Mumbai, Chitra and Dev Pune, Esha Delhi', () => {
    expect(HOME_REGION).toEqual({ Alice: 'mum', Bob: 'mum', Chitra: 'pun', Dev: 'pun', Esha: 'del' })
    expect(SHOPPERS.every((s) => s in HOME_REGION)).toBe(true)
  })

  it('the restricted item is never one the auto-shoppers or the seed can pick, and it names its claim in the signed note', () => {
    expect(PRODUCTS.some((p) => p.sku === RESTRICTED_PRODUCT.sku || p.requires)).toBe(false)
    const text = addNoteText(RESTRICTED_PRODUCT, 'Alice')
    expect(JSON.parse(text)).toMatchObject({ kind: 'ADD', sku: 'BEER-01', requires: 'age_over_18' })
    expect(parseNote(text)).toMatchObject({ sku: 'BEER-01' })
    expect('requires' in JSON.parse(addNoteText(PRODUCTS[0], 'Alice'))).toBe(false) // an ordinary item is unchanged
    expect(cartItems({ state: { notes: [{ id: 'mum:2', text }] } } as never)).toHaveLength(1)
  })
})
