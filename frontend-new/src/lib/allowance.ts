// A shopper's "requests left" (contract 8.3): the count a region last reported in X-RateLimit-Remaining, plus what has
// refilled since. The browser never invents the number; it only extrapolates the refill between two answers.

export interface Allowance {
  /** tokens in a full bucket (X-RateLimit-Limit) */
  limit: number
  /** tokens left when the region answered (X-RateLimit-Remaining) */
  remaining: number
  /** when it answered (ms since the epoch) */
  at: number
  /** tokens added per second, from the region's own status when it reports it */
  refillPerS: number
  /** after a 429: how long the region asked the shopper to wait (ms) */
  retryAfterMs?: number
}

/** Read the allowance out of a write's answer. null when the region sent no X-RateLimit headers (the limit is off there). */
export function allowanceFrom(headers: Record<string, string>, body: unknown, refillPerS: number, now: number): Allowance | null {
  const limit = Number(headers['x-ratelimit-limit'])
  const remaining = Number(headers['x-ratelimit-remaining'])
  if (headers['x-ratelimit-limit'] === undefined || headers['x-ratelimit-remaining'] === undefined) return null
  if (!Number.isFinite(limit) || !Number.isFinite(remaining) || limit <= 0) return null
  const wait = Number((body as { retry_after_ms?: unknown } | null)?.retry_after_ms)
  return { limit, remaining, at: now, refillPerS: refillPerS > 0 ? refillPerS : 1, ...(Number.isFinite(wait) && wait > 0 ? { retryAfterMs: wait } : {}) }
}

/** Whole requests a shopper can make at `now`. */
export function remainingNow(a: Allowance, now: number): number {
  if (a.limit <= 0) return 0
  const refilled = Math.max(0, now - a.at) / 1000 * a.refillPerS
  return Math.max(0, Math.min(a.limit, Math.floor(a.remaining + refilled + 1e-9)))
}

/** Whole seconds until the next request is allowed (0 when one is allowed now). */
export function secondsUntilNext(a: Allowance, now: number): number {
  if (remainingNow(a, now) >= 1) return 0
  const have = Math.min(a.limit, a.remaining + Math.max(0, now - a.at) / 1000 * a.refillPerS)
  return Math.max(1, Math.ceil((1 - have) / a.refillPerS))
}

/** A full bucket, for a shopper who has not made a request since the limit was switched on. */
export const fullAllowance = (limit: number, refillPerS: number, now: number): Allowance => ({ limit, remaining: limit, at: now, refillPerS: refillPerS > 0 ? refillPerS : 1 })
