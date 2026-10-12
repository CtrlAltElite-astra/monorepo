// U1: fail-safe, explainable conflict resolution. Both versions side by side with origin, actor and time,
// the rule in plain words, the policy version, and a supervisor decision that replicates (409 if it went stale).
import { useEffect, useMemo, useRef, useState } from 'react'
import { useDemo } from '../state/demo'
import type { Receipt, RegionId, ReportView, ReviewItem, Role } from '../api/types'
import { FIELD_LABEL, FLAG_TEXT, RULE_TEXT, cartCode, describeValue } from '../domain/shop'
import { fmtClock } from '../lib/util'
import { Icon } from './icons'
import { Chip, Empty, NODE_NAME, NodeDot, Segmented } from './ui'

interface Entry { item: ReviewItem; report: ReportView; region: RegionId }

export function useReviewEntries(): Entry[] {
  const d = useDemo()
  return useMemo(() => {
    const seen = new Map<string, Entry>()
    const order = [d.viewRegion, ...d.joined].filter((r): r is RegionId => !!r)
    for (const region of order) {
      for (const item of d.review[region] ?? []) {
        const key = `${item.report_id}:${item.field}`
        const report = d.snaps[region]?.reports.find((r) => r.report_id === item.report_id)
        if (!seen.has(key) && report) seen.set(key, { item, report, region })
      }
    }
    return [...seen.values()]
  }, [d.review, d.snaps, d.viewRegion, d.joined])
}

export function ConflictCard({ receipt, compact }: { receipt: Receipt; compact?: boolean }) {
  const closeVsNew = receipt.relationship === 'CLOSE_VS_NEW_INFO'
  const sides = closeVsNew
    ? [{ title: 'Order placed', list: receipt.candidates.filter((c) => c.kind === 'close') }, { title: 'New item added elsewhere', list: receipt.candidates.filter((c) => c.kind !== 'close') }]
    : [{ title: 'Competing values', list: receipt.candidates }]
  return (
    <div className={`conflict ${compact ? 'conflict--compact' : ''}`}>
      <div className="conflict__meta">
        <Chip tone="warn" icon="scale">{FIELD_LABEL[receipt.field] ?? receipt.field}</Chip>
        <Chip tone="muted">policy v{receipt.policy_version}</Chip>
        {receipt.flags.map((f) => <Chip key={f} tone="bad" icon="alert">{FLAG_TEXT[f] ?? f}</Chip>)}
        <Chip tone="info" title="The value every node shows while this is unresolved">showing: {describeValue(receipt.field, receipt.provisional)}</Chip>
      </div>
      <div className={`conflict__sides ${sides.length > 1 ? 'is-split' : ''}`}>
        {sides.map((s) => (
          <div key={s.title} className="conflict__side">
            <h4>{s.title}</h4>
            {s.list.map((c) => (
              <div key={c.event_id} className="candidate">
                <span className="candidate__val">{describeValue(c.field ?? receipt.field, c.value)}</span>
                <span className="candidate__meta"><NodeDot id={c.origin} size={8} />{NODE_NAME[c.origin] ?? c.origin} · {c.actor ?? '—'} · {fmtClock(c.wall)} · <code>{c.event_id}</code></span>
              </div>
            ))}
          </div>
        ))}
      </div>
      {!compact && <p className="conflict__rule"><Icon name="info" size={14} /> {RULE_TEXT[receipt.rule ?? ''] ?? receipt.rule ?? 'A written rule picked the shown value.'}</p>}
    </div>
  )
}

interface Choice { id: string; label: string; value: unknown; reason: string }

function choicesFor(r: Receipt): Choice[] {
  if (r.relationship === 'CLOSE_VS_NEW_INFO') {
    return [
      { id: 'ship', label: 'Ship with the new item', value: 'closed', reason: 'Ship with the new item' },
      { id: 'refund', label: 'Refund and re-order', value: 'cancelled', reason: 'Refund and re-order' },
      { id: 'keep', label: 'Keep as is', value: 'closed', reason: 'Keep as is' },
    ]
  }
  const values = [...new Map(r.candidates.map((c) => [JSON.stringify(c.value), c.value])).values()]
  return values.map((v, i) => ({ id: String(i), label: describeValue(r.field, v), value: v, reason: '' }))
}

export function ResolveDialog({ entry, onClose }: { entry: Entry; onClose: () => void }) {
  const d = useDemo()
  const { item, report } = entry
  const choices = useMemo(() => choicesFor(item.receipt), [item])
  const [choice, setChoice] = useState(choices[0]?.id)
  const [reason, setReason] = useState(choices[0]?.reason ?? '')
  const [as, setAs] = useState<Role>('supervisor')
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<string | null>(null)
  const candidates = useRef(report.live[item.field] ?? []) // what the supervisor was LOOKING at: a stale click gets 409
  const area = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { area.current?.focus(); const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose(); window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k) }, [onClose])

  const pick = (c: Choice) => { setChoice(c.id); if (c.reason) setReason(c.reason) }
  const submit = async () => {
    const c = choices.find((x) => x.id === choice)
    if (!c || !reason.trim()) return
    setBusy(true)
    const res = await d.resolve(report, item.field, c.value, candidates.current, reason.trim(), as)
    setBusy(false)
    if (res.status === 201 || res.status === 200) onClose()
    else setOutcome(res.status === 409 ? `409: ${(res.body as { error?: string })?.error === 'no_conflict' ? 'nothing left to resolve' : 'it changed while you were deciding. Close and look again.'}` : res.status === 403 ? '403: only a supervisor may resolve. Nothing was written.' : `Refused (${res.status}).`)
  }

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label="Resolve a conflict" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal__panel">
        <header className="modal__head"><h3>Resolve · {report.state.title} <code className="muted">{cartCode(report.report_id)}</code></h3><button className="btn btn--ghost btn--sm" onClick={onClose} aria-label="Close"><Icon name="x" size={16} /></button></header>
        <ConflictCard receipt={item.receipt} />
        <div className="modal__body">
          <h4>Your decision</h4>
          <div className="choices">
            {choices.map((c) => (
              <label key={c.id} className={`choice ${choice === c.id ? 'choice--on' : ''}`}>
                <input type="radio" name="choice" checked={choice === c.id} onChange={() => pick(c)} /><span>{c.label}</span>
              </label>
            ))}
          </div>
          <label className="field"><span>Reason (kept in the history)</span>
            <textarea ref={area} className="input" rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why this decision?" /></label>
          <div className="modal__row">
            <span className="muted">Acting as</span>
            <Segmented size="sm" value={as} onChange={setAs} options={[{ id: 'supervisor', label: 'Supervisor' }, { id: 'officer', label: 'Officer (will be refused)', tone: 'warn' }]} />
          </div>
          {outcome && <p className="notice notice--bad" role="alert"><Icon name="alert" size={16} />{outcome}</p>}
        </div>
        <footer className="modal__foot">
          <button className="btn btn--ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn--primary" disabled={busy || !reason.trim()} onClick={() => void submit()}><Icon name={busy ? 'refresh' : 'check'} size={16} className={busy ? 'spin' : ''} />Resolve</button>
        </footer>
      </div>
    </div>
  )
}

export function ReviewPanel() {
  const entries = useReviewEntries()
  const [open, setOpen] = useState<Entry | null>(null)
  const current = open && entries.find((e) => e.item.report_id === open.item.report_id && e.item.field === open.item.field)
  return (
    <div className="panel">
      {!entries.length ? (
        <Empty icon="scale" title="Nothing needs a decision">When two nodes change the same thing while cut off, the safe value is shown and the disagreement lands here.</Empty>
      ) : (
        <div className="review">
          {entries.map((e) => (
            <article key={`${e.item.report_id}:${e.item.field}`} className="review__item">
              <header><strong>{e.report.state.title}</strong> <code className="muted">{cartCode(e.report.report_id)}</code>
                <Chip tone="warn" pulse icon="scale">needs a supervisor</Chip></header>
              <ConflictCard receipt={e.item.receipt} />
              <button className="btn btn--primary btn--sm" onClick={() => setOpen(e)}><Icon name="check" size={14} />Resolve…</button>
            </article>
          ))}
        </div>
      )}
      {open && current && <ResolveDialog entry={current} onClose={() => setOpen(null)} />}
    </div>
  )
}
