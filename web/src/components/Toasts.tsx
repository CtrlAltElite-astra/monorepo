import { useDemo } from '../state/demo'
import { Icon, type IconName } from './icons'

const ICON: Record<string, IconName> = { ok: 'check', info: 'info', warn: 'alert', bad: 'alert' }

export function Toasts() {
  const { toasts, dismissToast } = useDemo()
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast--${t.kind}`} role={t.kind === 'bad' ? 'alert' : 'status'}>
          <Icon name={ICON[t.kind]} size={16} /><span>{t.text}</span>
          <button onClick={() => dismissToast(t.id)} aria-label="Dismiss"><Icon name="x" size={14} /></button>
        </div>
      ))}
    </div>
  )
}
