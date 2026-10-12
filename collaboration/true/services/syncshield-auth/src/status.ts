// Signed status snapshot handling and the ONE freshness policy (mirrored in Python: region/trust/true_status.py).
// A snapshot is only ever accepted after its signature verified against the PINNED issuer key.
import { jwtVerify, importJWK } from 'jose';
import { STATUS_POLICY, STATUS_TOKEN_TYPE } from '@true/shared';
import type { Jwk } from '@true/credentials';

export type Snapshot = { ver: number; issued_at: number; next_update: number; statuses: Record<string, 'a' | 'r'>; jws: string };

export async function verifySnapshot(jws: string, issuerId: string, issuerPublicJwk: Jwk): Promise<Snapshot> {
  const key = await importJWK(issuerPublicJwk as never, 'ES256');
  const { payload, protectedHeader } = await jwtVerify(jws, key, { algorithms: ['ES256'], issuer: issuerId });
  if (protectedHeader.typ !== STATUS_TOKEN_TYPE) throw new Error('wrong snapshot type');
  const { ver, statuses, next_update, iat } = payload as Record<string, unknown>;
  if (typeof ver !== 'number' || typeof iat !== 'number' || typeof statuses !== 'object' || statuses === null) {
    throw new Error('malformed snapshot');
  }
  return { ver, issued_at: iat, next_update: Number(next_update ?? iat), statuses: statuses as Snapshot['statuses'], jws };
}

export type Mode = 'online' | 'cached' | 'stale';
export type Purpose = 'login' | 'write' | 'privileged';
export type Decision = { allow: true; mode: Mode; age: number } | { allow: false; reason: string; age?: number };

// Policy (plan section 6; brief section 6):
//   unknown credential or revoked      -> deny, always
//   no snapshot                        -> deny
//   age <= online                      -> online
//   age <= cached                      -> cached        (allowed for everything)
//   age <= hard                        -> stale         (ordinary writes only; never login, never privileged)
//   age  > hard                        -> deny
export function evaluateStatus(input: {
  snapshot: Pick<Snapshot, 'issued_at' | 'statuses'> | null;
  credentialId: string;
  now: number; // seconds
  purpose: Purpose;
}): Decision {
  const { snapshot, credentialId, now, purpose } = input;
  if (!snapshot) return { allow: false, reason: 'no_status' };
  const state = snapshot.statuses[credentialId];
  const age = Math.max(0, now - snapshot.issued_at);
  if (state === undefined) return { allow: false, reason: 'unknown_credential', age };
  if (state === 'r') return { allow: false, reason: 'revoked', age };
  if (age > STATUS_POLICY.hard) return { allow: false, reason: 'status_too_old', age };
  if (age <= STATUS_POLICY.online) return { allow: true, mode: 'online', age };
  if (age <= STATUS_POLICY.cached) return { allow: true, mode: 'cached', age };
  if (purpose === 'write') return { allow: true, mode: 'stale', age };
  return { allow: false, reason: 'status_stale_for_purpose', age };
}
