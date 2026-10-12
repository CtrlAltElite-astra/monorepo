// Light and dark themes. The choice is remembered; with no saved choice the system preference decides.
// index.html sets data-theme before first paint so there is no flash of the wrong theme.
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { Icon } from './icons'

export type Theme = 'light' | 'dark'
const KEY = 'syncshield-theme'

const ThemeCtx = createContext<{ theme: Theme; toggle: () => void }>({ theme: 'light', toggle: () => undefined })
export const useTheme = () => useContext(ThemeCtx)

const initial = (): Theme => {
  const set = typeof document !== 'undefined' ? document.documentElement.getAttribute('data-theme') : null
  return set === 'dark' ? 'dark' : 'light'
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(initial)
  const [saved, setSaved] = useState(() => { try { return !!localStorage.getItem(KEY) } catch { return false } })

  useEffect(() => { document.documentElement.setAttribute('data-theme', theme) }, [theme])

  // follow the system until the user picks one
  useEffect(() => {
    if (saved) return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const on = (e: MediaQueryListEvent) => setTheme(e.matches ? 'dark' : 'light')
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [saved])

  const toggle = useCallback(() => {
    setTheme((t) => {
      const next: Theme = t === 'dark' ? 'light' : 'dark'
      try { localStorage.setItem(KEY, next) } catch { /* private mode: the choice just is not remembered */ }
      return next
    })
    setSaved(true)
  }, [])

  return <ThemeCtx.Provider value={{ theme, toggle }}>{children}</ThemeCtx.Provider>
}

export function ThemeToggle() {
  const { theme, toggle } = useTheme()
  return (
    <button className="theme-toggle" onClick={toggle} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>
      <span className={`theme-toggle__icon ${theme === 'dark' ? 'is-dark' : ''}`}>
        <Icon name="sun" size={18} className="theme-toggle__sun" />
        <Icon name="moon" size={18} className="theme-toggle__moon" />
      </span>
    </button>
  )
}
