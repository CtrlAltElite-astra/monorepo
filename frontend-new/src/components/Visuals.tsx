import { useEffect, useState, type ReactNode, type RefObject } from 'react'
import { Link } from 'react-router-dom'
import { Icon } from './icons'
import { applyMotion, readMotion, type Motion } from '../lib/motion'

export function Brand() {
  return <Link to="/" className="ss-brand" aria-label="SyncShield home"><span><Icon name="shield" size={30} /><Icon name="check" size={14} /></span>SyncShield<span className="brand-period">.</span></Link>
}
export function ServerStack({ state = 'healthy', small = false }: { state?: string; small?: boolean }) {
  return <div className={`server-stack ${small ? 'server-stack--small' : ''} server-stack--${state}`} aria-hidden="true">{[0, 1, 2].map(i => <div className="server-slab" key={i}><span className="server-port" /><div><i /><i /><i /><i /></div><span className="server-led" /></div>)}</div>
}
export function ProductPhoto({ sku, className = '' }: { sku: string; className?: string }) {
  const positions: Record<string, [number, number]> = { 'FCOFF-500': [0, 0], 'BOTTLE-01': [50, 0], 'KURTA-01': [100, 0], 'EARBUD-01': [0, 100], 'LAMP-01': [50, 100], 'BAG-01': [100, 100] }
  if (!positions[sku]) return <div className={`product-photo product-photo--unknown ${className}`} role="img" aria-label="Other cart item"><Icon name="package" size={18} /></div>
  const p = positions[sku]
  return <div className={`product-photo ${className}`} style={{ backgroundPosition: `${p[0]}% ${p[1]}%` }} role="img" aria-label="Product photograph" />
}

/** A status light. healthy: steady green with a slow ring; recovering / degraded: amber pulse; starting: amber spin;
 *  crashing: fast red; down: solid red for two seconds, then it blinks (see motion.css). */
export type Phase = 'healthy' | 'recovering' | 'degraded' | 'starting' | 'crashing' | 'down' | 'unknown'
export function Light({ phase, size = 14 }: { phase: Phase; size?: number }) {
  return <span className={`light light--${phase}`} style={{ width: size, height: size }} aria-hidden="true"><i /></span>
}

/** Staggered reveal. Anything with data-reveal inside `root` fades up the first time it scrolls into view. */
export function useScrollReveal(root: RefObject<HTMLElement | null>, deps: unknown[] = []) {
  useEffect(() => {
    const host = root.current
    if (!host) return
    const io = new IntersectionObserver(entries => entries.forEach(e => { if (e.isIntersecting) { e.target.setAttribute('data-in', ''); io.unobserve(e.target) } }), { root: host, threshold: 0.1 })
    host.querySelectorAll('[data-reveal]:not([data-in])').forEach(el => io.observe(el))
    return () => io.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}

export function MotionToggle() {
  const [motion, setMotion] = useState<Motion>(readMotion)
  const flip = () => { const next: Motion = motion === 'full' ? 'reduced' : 'full'; setMotion(next); applyMotion(next) }
  return (
    <button className="motion-toggle" onClick={flip} aria-pressed={motion === 'full'} title={motion === 'full' ? 'Animations are on. Click to turn them off.' : 'Animations are off. Click to turn them on.'}>
      <Icon name="bolt" size={15} /><span className="motion-label">Motion</span><b>{motion === 'full' ? 'On' : 'Off'}</b>
    </button>
  )
}

export function Reveal({ children, className = '', delay = 0 }: { children: ReactNode; className?: string; delay?: number }) {
  return <div className={`reveal ${className}`} style={delay ? { transitionDelay: `${delay}ms` } : undefined}>{children}</div>
}
export function useReveal() {
  useEffect(() => {
    const observer = new IntersectionObserver(entries => entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add('revealed'); observer.unobserve(e.target) } }), { threshold: .12 })
    document.querySelectorAll('.reveal').forEach(el => observer.observe(el))
    return () => observer.disconnect()
  }, [])
}
