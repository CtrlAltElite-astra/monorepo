// The measured scaling comparison (master plan V2). `public/scale-result.json` is written by `scripts/bench_scale.py`; this
// console only reads it. A number is shown only when the file has one: a mode the benchmark could not run is "unsupported",
// and with no file at all the page says "not measured yet". Nothing here is ever typed in by hand.

export interface ScalePoint { writes_per_s: number; p50_ms: number | null; p95_ms: number | null }
export type ScaleCell = ScalePoint | 'unsupported'

export interface ScaleResult {
  measuredAt: string | null
  machine: string | null
  writes: number | null
  vertical: { power: string; cell: ScaleCell }[]
  horizontal: { regions: number; cell: ScaleCell }[]
  oneHigh: ScaleCell | null
  threeLow: ScaleCell | null
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const text = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null)

/** One measurement. Anything without a real, positive writes-per-second figure is "unsupported", never a made-up number. */
export function cellOf(raw: unknown): ScaleCell {
  if (raw === 'unsupported' || raw === null || typeof raw !== 'object') return 'unsupported'
  const o = raw as Record<string, unknown>
  if (o.unsupported === true || o.status === 'unsupported') return 'unsupported'
  const perS = num(o.writes_per_s)
  if (perS === null || perS <= 0) return 'unsupported'
  return { writes_per_s: perS, p50_ms: num(o.p50_ms), p95_ms: num(o.p95_ms) }
}

/** The file's contents, or null when it is not a result file at all (the dev server answers a missing file with the HTML shell). */
export function parseScaleResult(raw: unknown): ScaleResult | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const o = raw as Record<string, unknown>
  const hasAny = ['vertical', 'horizontal', 'compare'].some((k) => k in o)
  if (!hasAny) return null
  const rows = <T,>(v: unknown, make: (r: Record<string, unknown>) => T | null): T[] =>
    Array.isArray(v) ? v.map((r) => (r && typeof r === 'object' ? make(r as Record<string, unknown>) : null)).filter((r): r is T => r !== null) : []
  const vertical = o.vertical === 'unsupported'
    ? ['low', 'medium', 'high'].map((power) => ({ power, cell: 'unsupported' as ScaleCell }))
    : rows(o.vertical, (r) => (text(r.power) ? { power: text(r.power)!, cell: cellOf(r) } : null))
  const horizontal = rows(o.horizontal, (r) => (num(r.regions) !== null ? { regions: num(r.regions)!, cell: cellOf(r) } : null))
  const compare = o.compare && typeof o.compare === 'object' ? (o.compare as Record<string, unknown>) : null
  return {
    measuredAt: text(o.measured_at), machine: text(o.machine), writes: num(o.writes),
    vertical, horizontal,
    oneHigh: compare && 'one_high' in compare ? cellOf(compare.one_high) : null,
    threeLow: compare && 'three_low' in compare ? cellOf(compare.three_low) : null,
  }
}

/** What the file says about the comparison, in a sentence that stays true whichever way the numbers fall. */
export function compareSentence(r: ScaleResult): string | null {
  if (r.oneHigh === null || r.threeLow === null || r.oneHigh === 'unsupported' || r.threeLow === 'unsupported') return null
  const a = r.oneHigh.writes_per_s, b = r.threeLow.writes_per_s
  const ratio = (Math.max(a, b) / Math.min(a, b)).toFixed(1)
  if (Math.abs(a - b) / Math.max(a, b) < 0.05) return 'One strong region and three ordinary ones came out about the same.'
  return a > b ? `One region at high power was ${ratio} times faster than three regions at low power.` : `Three regions at low power were ${ratio} times faster than one region at high power.`
}
