// The shop adapter (plan section 4): the backend stays domain neutral, so only this file speaks "shop".
// Engine field -> shop label. The UI renders what the API returns; it never recomputes status, priority or conflicts.
import type { RegionId, ReportView } from '../api/types'

export interface Product {
  sku: string; name: string; price: number; hue: number; glyph: string
  /** a claim the buyer must have proven (contract 8.5, "age_over_18"); the write path answers 403 claim_required until then */
  requires?: string
}

/** Appendix B seed data: synthetic products only. */
export const PRODUCTS: Product[] = [
  { sku: 'SANDAL-01', name: 'Kolhapuri sandals', price: 1299, hue: 24, glyph: 'sandal' },
  { sku: 'BOTTLE-01', name: 'Steel water bottle', price: 499, hue: 200, glyph: 'bottle' },
  { sku: 'KURTA-01', name: 'Cotton kurta', price: 899, hue: 340, glyph: 'shirt' },
  { sku: 'FCOFF-500', name: 'Filter coffee 500 g', price: 449, hue: 28, glyph: 'cup' },
  { sku: 'EARBUD-01', name: 'Wireless earbuds', price: 1999, hue: 262, glyph: 'earbud' },
  { sku: 'LAMP-01', name: 'Desk lamp', price: 1099, hue: 48, glyph: 'lamp' },
  { sku: 'BAG-01', name: 'Backpack', price: 1599, hue: 150, glyph: 'bag' },
  { sku: 'GIFT-500', name: 'Gift card', price: 500, hue: 310, glyph: 'gift' },
  { sku: 'CHAI-01', name: 'Masala chai box', price: 349, hue: 12, glyph: 'chai' },
  { sku: 'MAT-01', name: 'Yoga mat', price: 799, hue: 175, glyph: 'mat' },
]

export const SHOPPERS = ['Alice', 'Bob', 'Chitra', 'Dev', 'Esha'] as const
export type Shopper = (typeof SHOPPERS)[number]

/** Each shopper's home region: where their requests go first (master plan H2). The fixed failover order is the fallback. */
export const HOME_REGION: Record<Shopper, RegionId> = { Alice: 'mum', Bob: 'mum', Chitra: 'pun', Dev: 'pun', Esha: 'del' }
/** With Bengaluru in the mesh the five shoppers spread over four regions: this is how added capacity takes load. */
export const HOME_REGION_WITH_BLR: Record<Shopper, RegionId> = { Alice: 'mum', Bob: 'pun', Chitra: 'del', Dev: 'blr', Esha: 'blr' }
export function homeOf(who: Shopper, joined: readonly RegionId[]): RegionId {
  return joined.includes('blr') ? HOME_REGION_WITH_BLR[who] : HOME_REGION[who]
}

/** An item for adults only. It is NOT in PRODUCTS (auto-shoppers and the seed never pick it) and it is hidden unless the flag is on:
 *  the claim check that refuses it belongs to the identity lane and joins in the dependent plan (J4). */
export const RESTRICTED_PRODUCT: Product = { sku: 'BEER-01', name: 'Craft beer sampler', price: 1299, hue: 38, glyph: 'cup', requires: 'age_over_18' }
export function restrictedProductEnabled(): boolean {
  try {
    if (typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('restricted') === '1') return true
  } catch { /* no URL to read */ }
  return (import.meta.env.VITE_RESTRICTED_PRODUCT as string | undefined) === '1'
}

export const SPEEDS: Record<number, string> = { 1: 'Standard', 2: 'Plus', 3: 'Express', 4: 'Same-day', 5: 'Priority' }
export const CENTRES = ['Bhiwandi', 'Chakan', 'Gurgaon'] as const

/** status as the engine stores it -> shop label and a tone the chips use */
export const STATUS_LABEL: Record<string, { label: string; tone: 'info' | 'warn' | 'ok' | 'done' | 'bad' }> = {
  open: { label: 'Shopping', tone: 'info' },
  assigned: { label: 'Checking out', tone: 'warn' },
  resolved: { label: 'Paid', tone: 'ok' },
  closed: { label: 'Order placed', tone: 'done' },
  cancelled: { label: 'Cancelled', tone: 'bad' },
}
export const statusLabel = (s: string | null | undefined) => STATUS_LABEL[s ?? '']?.label ?? '—'

/** engine field -> the words the shop uses for it */
export const FIELD_LABEL: Record<string, string> = {
  title: 'Cart owner', description: 'Delivery note', category: 'Type', location: 'Delivery pin', severity: 'Delivery speed',
  status: 'Order status', assigned_team: 'Fulfilment centre', people_estimate: 'People',
}

export const RULE_TEXT: Record<string, string> = {
  LATEST_HLC: 'The latest edit wins; the other version is kept in history.',
  SEVERITY_HIGHEST: 'The faster delivery wins: a lost upgrade is worse than a false one.',
  STATUS_MOST_ACTIVE: 'The most active status wins, so a live order is never closed by mistake.',
  TEAM_EARLIEST: 'The earliest assignment holds; only a person can redirect it.',
  PEOPLE_HIGHEST: 'The higher count wins and counts are never added together.',
  LOCATION_LATEST: 'The latest pin wins; pins far apart go to a supervisor.',
  CLOSE_VS_NEW_INFO: 'An order was closed on one node while a new item arrived on another. Nothing is hidden: a supervisor decides.',
  'v0-latest': 'The latest edit wins (basic engine).',
}

export const FLAG_TEXT: Record<string, string> = {
  DOUBLE_DISPATCH: 'Double fulfilment: two centres were assigned',
  CLOSE_VS_NEW_INFO: 'Order placed while a new item was added elsewhere',
  PINS_FAR_APART: 'Delivery pins are far apart',
}

export interface CartItem { sku: string; name: string; qty: number; price: number; user: string | null; noteIds: string[] }

export interface AddNote { kind: 'ADD'; sku: string; name: string; qty: number; price: number; user: string; requires?: string }

export const addNoteText = (p: Product, user: string, qty = 1): string =>
  JSON.stringify({ kind: 'ADD', sku: p.sku, name: p.name, qty, price: p.price, user, ...(p.requires ? { requires: p.requires } : {}) } satisfies AddNote)

export function parseNote(text: string): AddNote | null {
  try {
    const n = JSON.parse(text)
    return n && n.kind === 'ADD' && typeof n.sku === 'string' ? (n as AddNote) : null
  } catch { return null }
}

/**
 * Items in a cart. The server already collapses events that share an op_id into ONE note, so a retried add is
 * counted once here: quantity of a SKU = sum of qty over its (already de-duplicated) ADD notes.
 */
export function cartItems(report: Pick<ReportView, 'state'>): CartItem[] {
  const bySku = new Map<string, CartItem>()
  for (const note of report.state.notes ?? []) {
    const add = parseNote(note.text)
    if (!add) continue
    const hit = bySku.get(add.sku)
    if (hit) { hit.qty += add.qty; hit.noteIds.push(note.id) }
    else bySku.set(add.sku, { sku: add.sku, name: add.name, qty: add.qty, price: add.price, user: add.user, noteIds: [note.id] })
  }
  return [...bySku.values()].filter((i) => i.qty > 0) // removals are notes with a negative quantity; nothing is ever deleted
}

export const cartTotal = (items: CartItem[]) => items.reduce((sum, i) => sum + i.qty * i.price, 0)
export const cartCount = (items: CartItem[]) => items.reduce((sum, i) => sum + i.qty, 0)

/** A short cart code for display: C-xxxx from R-xxxxxxxx */
export const cartCode = (reportId: string) => 'C-' + reportId.replace(/^R-/, '').slice(0, 4)

export const isDone = (status: string | null | undefined) => status === 'closed' || status === 'cancelled'

/** Plain-words value of a candidate for the conflict panel. */
/** A CLOSE_VS_NEW_INFO receipt (contract 3.4): rule and flag say so; `relationship` stays CONCURRENT. The close is listed first. */
export const isCloseVsNew = (r: { rule: string | null; flags: string[] }): boolean => r.rule === 'CLOSE_VS_NEW_INFO' || r.flags.includes('CLOSE_VS_NEW_INFO')
export const isStatusValue = (v: unknown): boolean => typeof v === 'string' && v in STATUS_LABEL

/** What an unseen change in a CLOSE_VS_NEW_INFO receipt says: a note's text, or a severity value. */
export function describeInfo(value: any): string {
  const n = typeof value === 'string' ? parseNote(value) : null
  if (n) return `added ${n.name}`
  if (typeof value === 'number') return `delivery set to ${SPEEDS[value] ?? value}`
  return String(value)
}

export function describeValue(field: string, value: any): string {
  if (value === null || value === undefined || value === '') return 'none'
  if (field === 'status') return statusLabel(value)
  if (field === 'severity') return `${SPEEDS[value] ?? value} (${value})`
  if (field === 'notes') { const n = typeof value === 'string' ? parseNote(value) : null; return n ? `added ${n.name}` : String(value) }
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
