// A small inline icon set (24x24, stroke). No icon font, no external assets.
import type { CSSProperties, ReactNode } from 'react'

const P: Record<string, ReactNode> = {
  shield: <path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6z" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>,
  moon: <path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" />,
  server: <><rect x="3" y="4" width="18" height="6" rx="2" /><rect x="3" y="14" width="18" height="6" rx="2" /><path d="M7 7h.01M7 17h.01" /></>,
  link: <><path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1" /><path d="M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1" /></>,
  unlink: <><path d="M10 14a4 4 0 005.7 0l1-1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7" /><path d="M4 4l16 16" /></>,
  bolt: <path d="M13 2L4 14h7l-1 8 9-12h-7z" />,
  cart: <><path d="M3 4h2.2l2.2 11h10.4L20 7H6.2" /><circle cx="9" cy="19.5" r="1.4" /><circle cx="17" cy="19.5" r="1.4" /></>,
  check: <path d="M4 12.5l5 5L20 6.5" />,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  alert: <><path d="M12 3l10 18H2z" /><path d="M12 10v5M12 18h.01" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  key: <><circle cx="8" cy="15" r="4" /><path d="M11 12l9-9M16 7l3 3M14 9l2 2" /></>,
  lock: <><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V8a4 4 0 018 0v3" /></>,
  play: <path d="M7 4l13 8-13 8z" />,
  pause: <path d="M7 4h3v16H7zM14 4h3v16h-3z" />,
  refresh: <path d="M20 11a8 8 0 00-14.5-3.5M4 4v4.5h4.5M4 13a8 8 0 0014.5 3.5M20 20v-4.5h-4.5" />,
  power: <><path d="M12 3v9" /><path d="M6.3 6.3a8 8 0 1011.4 0" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  eye: <><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></>,
  flask: <path d="M9 3h6M10 3v6l-5.5 9.5A2 2 0 006.2 21h11.6a2 2 0 001.7-2.5L14 9V3M7.5 15h9" />,
  chart: <path d="M4 20V10M10 20V4M16 20v-8M22 20H2" />,
  code: <path d="M8 8l-5 4 5 4M16 8l5 4-5 4M14 5l-4 14" />,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6" /></>,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  chevron: <path d="M6 9l6 6 6-6" />,
  copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 012-2h9" /></>,
  history: <><path d="M3 12a9 9 0 109-9 9 9 0 00-7 3.3" /><path d="M3 4v4h4M12 7v5l3 2" /></>,
  package: <path d="M3 8l9-5 9 5v8l-9 5-9-5zM3 8l9 5 9-5M12 13v8" />,
  truck: <><path d="M2 6h11v10H2zM13 10h4l3 3v3h-7" /><circle cx="6.5" cy="17.5" r="1.8" /><circle cx="16.5" cy="17.5" r="1.8" /></>,
  wifi: <path d="M2 9a15 15 0 0120 0M5 12.5a10 10 0 0114 0M8.5 16a5 5 0 017 0M12 19.5h.01" />,
  wifioff: <path d="M3 3l18 18M2 9a15 15 0 015-2.8M10 5.2A15 15 0 0122 9M5 12.5a10 10 0 014-2.4M14.5 10.3a10 10 0 014.5 2.2M8.5 16a5 5 0 013-1.5M12 19.5h.01" />,
  database: <><ellipse cx="12" cy="6" rx="8" ry="3" /><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></>,
  activity: <path d="M3 12h4l3-8 4 16 3-8h4" />,
  skull: <><path d="M12 3a8 8 0 00-5 14.2V20h10v-2.8A8 8 0 0012 3z" /><circle cx="9" cy="11.5" r="1.3" /><circle cx="15" cy="11.5" r="1.3" /><path d="M10 20v-2.5M14 20v-2.5" /></>,
  box: <><rect x="3" y="3" width="18" height="18" rx="3" /></>,
  scale: <path d="M12 3v18M5 21h14M5 7h14M5 7l-3 7a3.5 3.5 0 006 0zM19 7l-3 7a3.5 3.5 0 006 0z" />,
  gauge: <><path d="M4 18a9 9 0 1116 0" /><path d="M12 18l4-6" /></>,
  layers: <path d="M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5" />,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7.5h.01" /></>,
}

export type IconName = keyof typeof P

export function Icon({ name, size = 18, className, style }: { name: IconName; size?: number; className?: string; style?: CSSProperties }) {
  return (
    <svg className={`icon ${className ?? ''}`} style={style} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{P[name]}</svg>
  )
}

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg className="logo-mark" width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="var(--teal)" /><stop offset="1" stopColor="var(--blue)" /></linearGradient></defs>
      <path d="M32 4 56 13v17c0 15-10 26-24 30C18 56 8 45 8 30V13z" fill="url(#lg)" />
      <circle cx="22" cy="31" r="4.5" fill="#fff" /><circle cx="42" cy="22" r="4.5" fill="#fff" /><circle cx="42" cy="40" r="4.5" fill="#fff" />
      <path d="M25.5 29.5 38.5 23M25.5 32.5 38.5 38.5" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  )
}
