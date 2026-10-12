// Shared UI primitives. Plain elements, CSS classes from styles/*.css, no UI library.
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Icon, type IconName } from './icons'
import type { Level, RegionId } from '../api/types'

export type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'muted' | 'done' | 'accent'

export function Chip({ tone = 'muted', icon, children, title, pulse, className }: { tone?: Tone; icon?: IconName; children: ReactNode; title?: string; pulse?: boolean; className?: string }) {
  return (
    <span className={`chip chip--${tone} ${pulse ? 'chip--pulse' : ''} ${className ?? ''}`} title={title}>
      {icon && <Icon name={icon} size={13} />}{children}
    </span>
  )
}

export const LEVEL_TONE: Record<string, Tone> = { HEALTHY: 'ok', SYNCING: 'info', CONSTRAINED: 'warn', ISOLATED: 'bad', RECOVERING: 'info', OVERLOADED: 'bad' }
export const LEVEL_TEXT: Record<string, string> = {
  HEALTHY: 'Every peer reachable. Full sync.',
  SYNCING: 'Reachable, but heads still differ.',
  CONSTRAINED: 'A link is slow: critical first, bulk deferred (never dropped).',
  ISOLATED: 'A peer is out of reach. Writes are still accepted.',
  RECOVERING: 'Back in contact. Catching up, then a heal summary.',
  OVERLOADED: 'Write queue full: new writes are told to retry.',
}

export function LevelChip({ level, unknown }: { level: Level | null; unknown?: boolean }) {
  if (!level || unknown) return <Chip tone="muted" icon="wifioff" title="The browser cannot reach this node. That is a UI state, not a node level.">UNKNOWN</Chip>
  return <Chip tone={LEVEL_TONE[level] ?? 'muted'} title={LEVEL_TEXT[level]} pulse={level === 'RECOVERING' || level === 'ISOLATED'}>{level}</Chip>
}

export const REPL_TONE: Record<string, Tone> = { LOCAL_ONLY: 'warn', PARTIAL: 'info', REPLICATED: 'ok' }
export function ReplBadge({ value, stale, compact }: { value: string; stale?: boolean; compact?: boolean }) {
  const label = value === 'LOCAL_ONLY' ? 'Local only' : value === 'PARTIAL' ? 'Partly copied' : 'Replicated'
  return (
    <span className="repl">
      <Chip tone={REPL_TONE[value] ?? 'muted'} icon={value === 'REPLICATED' ? 'check' : value === 'PARTIAL' ? 'layers' : 'database'}>{compact ? value.replace('_', ' ') : label}</Chip>
      {stale && <Chip tone="warn" icon="clock" title="A peer is unreachable right now, so a newer edit may exist that this node cannot see.">stale?</Chip>}
    </span>
  )
}

const NODE_COLOR: Record<string, string> = { mum: 'var(--node-mum)', pun: 'var(--node-pun)', del: 'var(--node-del)' }
export const nodeColor = (id: string | null | undefined) => (id ? NODE_COLOR[id] ?? 'var(--muted)' : 'var(--muted)')
export const NODE_NAME: Record<string, string> = { mum: 'Mumbai', pun: 'Pune', del: 'Delhi' }

export function NodeDot({ id, size = 10 }: { id: RegionId | string | null; size?: number }) {
  return <span className="node-dot" style={{ background: nodeColor(id), width: size, height: size }} />
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string; icon?: IconName; badge?: number | string | null }[]; value: T; onChange: (id: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={t.id === value} className={`tab ${t.id === value ? 'tab--on' : ''}`} onClick={() => onChange(t.id)}>
          {t.icon && <Icon name={t.icon} size={15} />}<span>{t.label}</span>
          {t.badge !== undefined && t.badge !== null && t.badge !== 0 && <span className="tab__badge">{t.badge}</span>}
        </button>
      ))}
    </div>
  )
}

export function Segmented<T extends string>({ options, value, onChange, disabled, size }: { options: { id: T; label: string; tone?: Tone }[]; value: T; onChange: (id: T) => void; disabled?: boolean; size?: 'sm' }) {
  return (
    <div className={`seg ${size === 'sm' ? 'seg--sm' : ''}`} role="group">
      {options.map((o) => (
        <button key={o.id} disabled={disabled} className={`seg__btn ${o.id === value ? `seg__btn--on seg__btn--${o.tone ?? 'info'}` : ''}`} onClick={() => onChange(o.id)}>{o.label}</button>
      ))}
    </div>
  )
}

/** Animates a number towards its target (respects reduced motion). */
export function useCountUp(target: number, ms = 600): number {
  const [shown, setShown] = useState(target)
  const from = useRef(target)
  useEffect(() => {
    const reduce = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduce || from.current === target) { setShown(target); from.current = target; return }
    const start = performance.now(), a = from.current
    let raf = 0
    const step = (t: number) => {
      const k = Math.min(1, (t - start) / ms)
      setShown(Math.round(a + (target - a) * (1 - Math.pow(1 - k, 3))))
      if (k < 1) raf = requestAnimationFrame(step); else from.current = target
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return shown
}

export function Stat({ label, value, tone, hint, suffix }: { label: string; value: number | string; tone?: Tone; hint?: string; suffix?: string }) {
  const n = useCountUp(typeof value === 'number' ? value : 0)
  return (
    <div className={`stat stat--${tone ?? 'muted'}`} title={hint}>
      <div className="stat__value">{typeof value === 'number' ? n : value}{suffix && <small>{suffix}</small>}</div>
      <div className="stat__label">{label}</div>
    </div>
  )
}

/** Press and hold to confirm: a destructive demo action must not fire on a stray click. */
export function HoldButton({ onConfirm, children, disabled, holdMs = 1100, tone = 'bad', title }: { onConfirm: () => void; children: ReactNode; disabled?: boolean; holdMs?: number; tone?: Tone; title?: string }) {
  const [progress, setProgress] = useState(0)
  const raf = useRef(0)
  const t0 = useRef(0)
  const cancel = () => { cancelAnimationFrame(raf.current); setProgress(0) }
  const begin = () => {
    if (disabled) return
    t0.current = performance.now()
    const step = (t: number) => {
      const k = Math.min(1, (t - t0.current) / holdMs)
      setProgress(k)
      if (k >= 1) { setProgress(0); onConfirm() } else raf.current = requestAnimationFrame(step)
    }
    raf.current = requestAnimationFrame(step)
  }
  useEffect(() => () => cancelAnimationFrame(raf.current), [])
  return (
    <button className={`btn btn--${tone} hold ${progress > 0 ? 'hold--on' : ''}`} disabled={disabled} title={title ?? 'Press and hold'}
      onMouseDown={begin} onMouseUp={cancel} onMouseLeave={cancel} onTouchStart={begin} onTouchEnd={cancel}
      onKeyDown={(e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) begin() }} onKeyUp={cancel}>
      <span className="hold__fill" style={{ transform: `scaleX(${progress})` }} />
      <span className="hold__label">{children}</span>
    </button>
  )
}

export function Meter({ value, max, tone, label, sub }: { value: number; max: number; tone?: Tone; label?: string; sub?: string }) {
  const pct = max <= 0 ? 0 : Math.min(100, (value / max) * 100)
  return (
    <div className="meter" role="meter" aria-valuenow={value} aria-valuemin={0} aria-valuemax={max} aria-label={label}>
      <div className="meter__bar"><div className={`meter__fill meter__fill--${tone ?? 'info'}`} style={{ width: `${pct}%` }} /></div>
      {(label || sub) && <div className="meter__text"><span>{label}</span><span>{sub}</span></div>}
    </div>
  )
}

export function Json({ value }: { value: unknown }) {
  const text = JSON.stringify(value, null, 2)
  const [copied, setCopied] = useState(false)
  return (
    <div className="json">
      <button className="btn btn--ghost btn--xs json__copy" onClick={() => { void navigator.clipboard?.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1200) }}>
        <Icon name={copied ? 'check' : 'copy'} size={13} />{copied ? 'Copied' : 'Copy'}
      </button>
      <pre>{text}</pre>
    </div>
  )
}

export function Empty({ icon = 'info', title, children }: { icon?: IconName; title: string; children?: ReactNode }) {
  return <div className="empty"><Icon name={icon} size={26} /><strong>{title}</strong>{children && <p>{children}</p>}</div>
}

export function Skeleton({ h = 14, w = '100%' }: { h?: number; w?: number | string }) {
  return <span className="skeleton" style={{ height: h, width: w }} />
}

export function useNow(ms = 1000): number {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(t) }, [ms])
  return now
}
