// SyncShield credential profile and session constants (SYNCSHIELD ADAPTATION; not part of upstream TRUE).
// Nothing secret lives here. Imported by the issuer, the regional auth sidecar, tests and the web wallet.

// The credential type the issuer signs for SyncShield members. A different vct is never accepted.
export const SYNCSHIELD_CREDENTIAL_TYPE = 'SyncShieldMember';

// Exactly these claims are signed, selectively disclosed, and requested at login. Nothing personal.
export const SYNCSHIELD_CLAIMS = ['actor_id', 'syncshield_member', 'syncshield_role', 'credential_id'] as const;
export const SYNCSHIELD_ROLES = ['officer', 'supervisor', 'admin'] as const;
export type SyncShieldRole = (typeof SYNCSHIELD_ROLES)[number];

// Token contract between the regional sidecar (issues) and every FastAPI region (verifies locally).
export const SESSION_AUDIENCE = 'syncshield';
export const SESSION_TOKEN_TYPE = 'syncshield-session+jwt';
export const SESSION_ISSUER_PREFIX = 'syncshield-region:';
export const STATUS_TOKEN_TYPE = 'syncshield-status+jwt';

// Freshness policy (seconds). One definition, mirrored in region/trust/true_status.py and tested for parity.
export const STATUS_POLICY = {
  online: 60,    // snapshot obtained within this long ago: "online"
  cached: 600,   // up to 10 minutes old: "cached"; the limit for logins and privileged actions
  hard: 900,     // absolute bound for ordinary writes with a known, previously active credential: "stale"
} as const;

export const SESSION_TTL_SECONDS = 300;
export const CHALLENGE_TTL_SECONDS = 120;
