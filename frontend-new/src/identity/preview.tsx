// Stand-alone test page for the identity panel, rendered from fixture props (no backend, no wallet).
// With `npm run dev` running in frontend-new: http://127.0.0.1:5175/src/identity/preview.html
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { IdentityPanel } from './IdentityPanel'
import { FIXTURES } from './fixtures'
import '../styles/tokens.css'
import '../styles/base.css'
import '../styles/components.css'
import '../styles/syncshield.css'

const noop = () => {}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <main style={{ maxWidth: 760, margin: '24px auto', padding: '0 16px', display: 'grid', gap: 20 }}>
      <h2>Identity panel · fixtures</h2>
      {Object.entries(FIXTURES).map(([name, props]) => (
        <div key={name} style={{ display: 'grid', gap: 6 }}>
          <span className="muted small">{name}</span>
          <IdentityPanel {...props} onSignIn={noop} onSignOut={noop} onRevoke={noop} />
        </div>
      ))}
    </main>
  </StrictMode>,
)
