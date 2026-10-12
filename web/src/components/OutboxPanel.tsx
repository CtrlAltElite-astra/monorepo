// The SDK's outbox: signed events the browser still holds because no node besides their origin has confirmed them.
// This is what makes a node's death survivable: on failover the browser carries these to the next node.
import { useDemo } from '../state/demo'
import { describeValue, FIELD_LABEL } from '../domain/shop'
import { fmtAge } from '../lib/util'
import { Icon } from './icons'
import { Chip, Empty, NODE_NAME, NodeDot, useNow } from './ui'

const PRIORITY = ['P0 critical', 'P1 normal', 'P2 bulk']

export function OutboxPanel() {
  const d = useDemo()
  const { outbox, deviceQueue, carrySupported, counters } = d.sdkState
  const now = useNow(1000)
  return (
    <div className="panel outbox">
      <div className="outbox__top">
        <Chip tone={outbox.length ? 'info' : 'ok'} icon="package">{outbox.length} signed event{outbox.length === 1 ? '' : 's'} held</Chip>
        <Chip tone={deviceQueue ? 'warn' : 'muted'} icon="clock">{deviceQueue} kept on the device</Chip>
        <Chip tone="muted" icon="check">{counters.carried} carried so far</Chip>
        {!carrySupported && <Chip tone="bad" icon="alert">carry endpoint missing on a node</Chip>}
      </div>
      <p className="muted small">An event leaves the outbox when a node other than its origin holds it, or any peer has acknowledged it. Nothing is dropped before that (TTL 10 min, at most 500).</p>

      {!outbox.length ? <Empty icon="package" title="Outbox is empty">Every acknowledged write is already on another node.</Empty> : (
        <ul className="outbox__list">
          {outbox.map((o) => (
            <li key={o.event.event_id} className={o.carriedTo.length ? 'is-carried' : ''}>
              <span className="mono"><NodeDot id={o.event.origin} size={8} />{o.event.event_id}</span>
              <span>{o.event.type}{o.event.field ? ` · ${FIELD_LABEL[o.event.field] ?? o.event.field}` : ''}{o.event.type === 'SET' ? ` → ${describeValue(o.event.field ?? '', o.event.value)}` : ''}</span>
              <Chip tone={o.event.priority === 0 ? 'bad' : o.event.priority === 2 ? 'muted' : 'info'}>{PRIORITY[o.event.priority]}</Chip>
              <span className="muted">{fmtAge(now - o.addedAt)}</span>
              {o.carriedTo.map((r) => <Chip key={r} tone="ok" icon="package">carried → {NODE_NAME[r]}</Chip>)}
            </li>
          ))}
        </ul>
      )}

      <h4 className="outbox__log-title"><Icon name="history" size={15} /> Carry log</h4>
      {!d.carryLog.length ? <p className="muted small">Nothing carried yet. Kill a node that holds unreplicated writes and watch this fill.</p> : (
        <ul className="carrylog">
          {d.carryLog.map((c) => (
            <li key={c.at}>
              <span className="mono">{new Date(c.at).toLocaleTimeString()}</span>
              <span>→ <NodeDot id={c.to} size={8} />{NODE_NAME[c.to]}</span>
              {c.unsupported ? <Chip tone="bad">no /v1/carry</Chip> : <span>{c.sent} sent: {c.results.filter((r) => r.result === 'new').length} new · {c.results.filter((r) => r.result === 'dup').length} dup · {c.results.filter((r) => r.result === 'rejected').length} rejected</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
