// Mock payloads for fixture mode (VITE_FIXTURES=1). Shapes follow PDF 2 sections 5 and 6.6.
// ASSUMED (confirm with Akshit when the endpoints land): GET /v1/review -> [{report_id, title, field, receipt}],
// GET /v1/metrics -> free-form nested object, GET /v1/reports/{id}/history -> [event envelopes in HLC order].
export const receipt = {
  field: 'status',
  relationship: 'CONCURRENT',
  candidates: [
    { event_id: 'mum:4', value: 'closed', origin: 'mum', actor: 'officer-mum-1', wall: '14:31' },
    { event_id: 'pun:9', value: 'assigned', origin: 'pun', actor: 'officer-pun-2', wall: '14:33' },
  ],
  rule: 'STATUS_MOST_ACTIVE',
  policy_version: 1,
  provisional: 'assigned',
  needs_review: true,
}

export const review = [{ report_id: 'R-mum-1', title: 'Wall collapse, Kurla', field: 'status', receipt }]

export const metrics = {
  writes: 42,
  commit_ms: { p50: 3.1, p95: 9.8 },
  duplicates: 4,
  lag_s: 0.4,
  catch_up_s: 1.2,
  peers: { pun: { events_sent: 30, events_received: 12, bytes_sent: 18400, bytes_received: 7200 } },
}

export const history = [
  { event_id: 'mum:1', type: 'CREATE', field: null, value: { title: 'Wall collapse, Kurla' }, supersedes: [], actor: 'officer-mum-1', wall: '14:20', auth: { mode: 'online' } },
  { event_id: 'mum:4', type: 'SET', field: 'status', value: 'closed', supersedes: ['mum:1'], actor: 'officer-mum-1', wall: '14:31', auth: { mode: 'cached' } },
  { event_id: 'pun:9', type: 'SET', field: 'status', value: 'assigned', supersedes: ['mum:1'], actor: 'officer-pun-2', wall: '14:33', auth: { mode: 'online' } },
]
