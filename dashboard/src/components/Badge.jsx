const TONE = {
  REPLICATED: 'good', PARTIAL: 'warn', LOCAL_ONLY: 'bad',
  HEALTHY: 'good', SYNCING: 'warn', ISOLATED: 'bad', OVERLOADED: 'bad',
  online: 'good', cached: 'warn', stale: 'bad',
}

// A consistency label that always says what it is. Unknown values render neutral, never green.
export default function Badge({ value, title }) {
  return <span className={`chip ${TONE[value] || ''}`} title={title}>{value}</span>
}
