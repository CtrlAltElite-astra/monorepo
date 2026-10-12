// Fixture props for the identity panel: used by the preview page and the tests. Synthetic people, made-up ids.
import type { IdentityPanelProps } from './IdentityPanel'

const NOW = 1_800_000_000_000
const holds = (age: boolean, member: boolean) => ({
  actor_id: 'act_a11ce0000000a11c', syncshield_member: true, syncshield_role: 'officer',
  credential_id: 'cred_a11ce0000000000000000a11', age_over_18: age, member,
})
const session = (claims: ('age_over_18' | 'member')[], disclosed: string[]) => ({
  region: 'mum', actorId: 'act_a11ce0000000a11c', credentialId: 'cred_a11ce0000000000000000a11', role: 'officer',
  authMode: 'online', claims, disclosed, expiresAt: NOW / 1000 + 252,
})

export const FIXTURES: Record<string, IdentityPanelProps> = {
  'No credential yet': { shopper: 'Esha', holds: null, session: null, now: NOW },
  'Holds a credential, not signed in': { shopper: 'Alice', holds: holds(true, true), session: null, now: NOW },
  'Proved over 18': { shopper: 'Alice', holds: holds(true, true), session: session(['age_over_18'], ['age_over_18']), now: NOW },
  'Under 18: disclosed, not proven': { shopper: 'Chitra', holds: holds(false, true), session: session([], ['age_over_18']), now: NOW },
  'Ordinary sign-in': { shopper: 'Dev', holds: holds(true, true), session: session([], []), now: NOW },
  Revoked: { shopper: 'Bob', holds: holds(true, false), session: session([], []), revoked: true, now: NOW },
  'Session expired': { shopper: 'Alice', holds: holds(true, true), session: { ...session(['age_over_18'], ['age_over_18']), expiresAt: NOW / 1000 - 1 }, now: NOW },
}
