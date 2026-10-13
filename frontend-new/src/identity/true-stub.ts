// Stand-in for the partner's TRUE credential package in a build WITHOUT sign-in (VITE_AUTH_ENABLED is not "1").
//
// vite.config.ts points `@true/credentials` here unless sign-in is switched on, so the normal console builds and
// runs with nothing installed under collaboration/true. Nothing here can run: the identity section renders nothing
// when the backend is not in sign-in mode, and every function refuses loudly if it is ever reached.
const off = (): never => {
  throw new Error('Sign-in is not enabled in this build (set VITE_AUTH_ENABLED=1 and run `npm install` in collaboration/true).')
}

export const generateHolderKey = off
export const createHolderProof = off
export const createPresentation = off
export const readCredential = off
