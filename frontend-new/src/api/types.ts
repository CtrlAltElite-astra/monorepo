// Shapes of contracts/contracts.md v1.1. The UI renders what the API returns; it never recomputes
// status, priority or conflicts. Mock mode returns exactly these shapes from the in-browser simulator.

export type RegionId = 'mum' | 'pun' | 'del'
export type Mode = 'mock' | 'live'
export type Role = 'officer' | 'supervisor' | 'admin'

export interface RegionInfo {
  id: RegionId
  name: string
  url: string
}

export type EventType = 'CREATE' | 'SET' | 'NOTE' | 'RESOLVE'
export type SigStatus = 'verified' | 'bad' | 'unsigned'

export interface EventEnvelope {
  v: number
  event_id: string
  origin: string
  seq: number
  report_id: string
  type: EventType
  field: string | null
  value: any
  supersedes: string[]
  hlc: [number, number]
  priority: 0 | 1 | 2
  op_id: string | null
  observed: string[] | null
  actor: string
  role: string
  auth_mode: 'online' | 'cached' | 'stale'
  reason: string | null
  wall: string
  hash: string
  sig: string | null
  // history rows only
  sig_status?: SigStatus
  carried?: boolean
}

export interface Candidate {
  event_id: string
  value: any
  origin: string
  actor: string | null
  wall: string | null
}

export interface Receipt {
  field: string
  relationship: string
  candidates: Candidate[]
  rule: string | null
  policy_version: number
  provisional: any
  needs_review: boolean
  flags: string[]
}

export interface Consistency {
  replication: 'LOCAL_ONLY' | 'PARTIAL' | 'REPLICATED'
  confirmed_by: string[]
  awaiting: string[]
  stale_possible: boolean
}

export interface NoteView {
  id: string
  text: string
}

export interface ReportView {
  report_id: string
  state: {
    title: string | null
    description: string | null
    category: string | null
    location: { lat_e6: number; lon_e6: number } | null
    severity: number | null
    status: string | null
    assigned_team: string | null
    people_estimate: number | null
    notes: NoteView[]
  }
  live: Record<string, string[]>
  conflicts: Receipt[]
  incomplete: boolean
  consistency: Consistency
}

export type Level = 'HEALTHY' | 'SYNCING' | 'CONSTRAINED' | 'ISOLATED' | 'RECOVERING' | 'OVERLOADED'

export interface PeerStatus {
  link: string
  reachable: boolean
  fails: number
  last_ok_s: number | null
  last_ok_ms: number | null
  their_heads: Record<string, number>
  acked: Record<string, number>
  last_error: string | null
}

export interface Risk {
  unreplicated: number
  critical_unreplicated: number
  oldest_age_ms: number
}

export interface Status {
  region: string
  level: Level
  policy_version: number
  schema_version: number
  heads: Record<string, number>
  event_count: number
  peers: Record<string, PeerStatus>
  event_digest: string
  state_hash: string
  quarantined: number
  incomplete_reports: number
  risk: Risk | null
  queues: { P0: number; P1: number; P2: number } | null
  carry: { received: number; new: number; dup: number; rejected: number } | null
}

export interface Healthz {
  region: string
  ok: boolean
  level: Level
  ts: number
}

export interface WriteBody {
  report: ReportView
  event: EventEnvelope
  event_id: string
  consistency: Consistency
}

export interface ReviewItem {
  report_id: string
  title: string | null
  field: string
  receipt: Receipt
}

export interface CarryResult {
  event_id: string
  result: 'new' | 'dup' | 'rejected'
  reason?: 'bad_hash' | 'bad_sig' | 'unknown_origin' | 'shape'
}

export interface HttpResult<T = any> {
  status: number
  headers: Record<string, string>
  body: T
  ms: number
}

export class NetworkError extends Error {
  constructor(message: string, public kind: 'timeout' | 'refused') {
    super(message)
    this.name = 'NetworkError'
  }
}

export interface RequestOptions {
  body?: unknown
  headers?: Record<string, string>
  timeoutMs?: number
  /** which API key to send: officer (default), supervisor or admin */
  as?: Role
}

/** The one seam between the UI/SDK and a backend. Mock = in-browser simulator, live = fetch. */
export interface Transport {
  mode: Mode
  regions: RegionInfo[]
  request<T = any>(region: RegionId, method: string, path: string, opts?: RequestOptions): Promise<HttpResult<T>>
}

export interface ChaosState {
  regions: Record<string, { running?: boolean; up?: boolean; pid?: number | null }>
}

export interface Chaos {
  available: boolean
  crash(region: RegionId): Promise<void>
  start(region: RegionId): Promise<void>
  state(): Promise<ChaosState | null>
}
