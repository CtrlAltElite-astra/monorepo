// Stand-in for the partner's TRUE packages in a build WITHOUT auth mode (VITE_AUTH_ENABLED is not "1").
//
// vite.config.js points `@true/credentials` and `@true/syncshield-shared` here unless auth mode is switched on,
// so the normal dashboard builds with nothing installed under collaboration/true. Nothing here can run: the auth
// UI renders nothing when auth is off, and every function refuses loudly if it is ever reached.
const off = () => {
  throw new Error('Auth mode is not enabled in this build (set VITE_AUTH_ENABLED=1 and install collaboration/true).')
}

export const SYNCSHIELD_CLAIMS = []
export const createHolderProof = off
export const createPresentation = off
export const generateHolderKey = off
export const readCredential = off
