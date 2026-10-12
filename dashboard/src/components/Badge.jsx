const TONE = {
  REPLICATED: 'good', PARTIAL: 'warn', LOCAL_ONLY: 'bad',
  HEALTHY: 'good', SYNCING: 'warn', CONSTRAINED: 'warn', RECOVERING: 'warn', ISOLATED: 'bad', OVERLOADED: 'bad',
  up: 'good', slow: 'warn', down: 'bad',
  online: 'good', cached: 'warn', stale: 'bad',
}

// A consistency label that always says what it is. Unknown values render neutral, never green.
export default function Badge({ value, title }) {
  return <span className={`chip ${TONE[value] || ''}`} title={title}>{value}</span>
}
