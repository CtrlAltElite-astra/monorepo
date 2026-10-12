// Product illustrations drawn as inline SVG so the demo needs no image assets or CDN.
import type { ReactNode } from 'react'

const GLYPH: Record<string, ReactNode> = {
  sandal: <><path d="M10 40c8-1 16-8 24-15l8 7c4 3 10 4 12 6v5H10z" /><path d="M26 33l5 4M33 29l5 4" /></>,
  bottle: <><rect x="23" y="20" width="18" height="34" rx="6" /><path d="M27 20v-5h10v5M23 33h18" /></>,
  shirt: <path d="M22 12l-12 8 6 9 5-3v25h22V26l5 3 6-9-12-8c-1 4-5 6-10 6s-9-2-10-6z" />,
  cup: <><path d="M14 26h30v12a10 10 0 01-10 10H24a10 10 0 01-10-10z" /><path d="M44 29h4a5 5 0 010 10h-4M24 12c0 4 4 4 4 8M32 12c0 4 4 4 4 8" /></>,
  earbud: <><circle cx="22" cy="24" r="8" /><path d="M22 32v18" /><circle cx="42" cy="24" r="8" /><path d="M42 32v18" /></>,
  lamp: <><path d="M22 14l-8 20h36l-8-20z" /><path d="M32 34v16M22 52h20" /></>,
  bag: <><rect x="14" y="22" width="36" height="32" rx="7" /><path d="M24 22v-4a8 8 0 0116 0v4M14 36h36" /></>,
  gift: <><rect x="12" y="26" width="40" height="26" rx="3" /><path d="M10 20h44v8H10zM32 20v32M32 20c-4-8-12-8-12-3s8 3 12 3zM32 20c4-8 12-8 12-3s-8 3-12 3z" /></>,
  chai: <><path d="M12 24h40l-4 28H16z" /><path d="M12 24l6-8h28l6 8M32 30c-5 4-5 10 0 14 5-4 5-10 0-14z" /></>,
  mat: <><rect x="10" y="36" width="44" height="12" rx="6" /><circle cx="48" cy="42" r="6" /><path d="M16 36V26" /></>,
}

export function ProductArt({ glyph, hue, size = 72 }: { glyph: string; hue: number; size?: number }) {
  const id = `pa-${glyph}`
  return (
    <svg className="product-art" width={size} height={size} viewBox="0 0 64 64" role="img" aria-label={glyph}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={`hsl(${hue} 80% 62%)`} /><stop offset="1" stopColor={`hsl(${(hue + 38) % 360} 78% 48%)`} />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill={`url(#${id})`} />
      <g fill="rgba(255,255,255,.16)" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">{GLYPH[glyph]}</g>
    </svg>
  )
}
