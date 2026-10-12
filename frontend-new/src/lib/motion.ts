// Motion preference. The demo animates by default: it is a showcase, and several important signals (a region going
// down, a region healing) are animated. The browser's "reduce motion" setting used to switch every animation off with no
// way back, so people with animations disabled in the operating system saw a static page. Now the choice is explicit and
// visible: the Motion switch in the navigation turns animation off (or back on) and the choice is remembered.
export type Motion = 'full' | 'reduced'
const KEY = 'syncshield-motion'

export function readMotion(): Motion {
  try { return localStorage.getItem(KEY) === 'reduced' ? 'reduced' : 'full' } catch { return 'full' }
}

export function applyMotion(motion: Motion, remember = true): void {
  document.documentElement.dataset.motion = motion
  if (!remember) return
  try { localStorage.setItem(KEY, motion) } catch { /* private mode: the choice just is not remembered */ }
}

export function initMotion(): void {
  applyMotion(readMotion(), false)
}
