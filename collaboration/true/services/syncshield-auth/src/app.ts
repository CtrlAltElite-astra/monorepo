// Regional auth sidecar routes (SYNCSHIELD ADAPTATION).
//
// One of these runs beside every FastAPI region. It is the ONLY place that turns a TRUE presentation into a
// SyncShield session; it calls the partner's real verifyPresentation and never re-implements credential crypto.
//
// Trust boundaries:
//   * reachable only on loopback and only with the service credential (X-Sidecar-Token);
//   * trusts exactly one issuer public key, pinned from a file, never one that arrives inside a presentation;
//   * never calls the issuer while handling a login: status comes from the cached, issuer-signed snapshot.
import { timingSafeEqual } from 'node:crypto';
import { mkdirSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { Hono } from 'hono';
import { z } from 'zod';
import { SignJWT, importJWK } from 'jose';
import type { DatabaseSync } from 'node:sqlite';
import {
  CHALLENGE_TTL_SECONDS, SESSION_AUDIENCE, SESSION_ISSUER_PREFIX, SESSION_TOKEN_TYPE, SESSION_TTL_SECONDS,
  STATUS_POLICY, SYNCSHIELD_CLAIMS, SYNCSHIELD_CREDENTIAL_TYPE, SYNCSHIELD_ROLES,
} from '@true/shared';
import { randomToken, verifyPresentation, type Jwk } from '@true/credentials';
import type { ChallengeRow } from './db.ts';
import { evaluateStatus, verifySnapshot, type Snapshot } from './status.ts';

export type SessionKey = { kid: string; privateJwk: Jwk; publicJwk: Jwk };

export type AuthDeps = {
  db: DatabaseSync;
  region: string;
  issuerId: string;
  issuerPublicJwk: Jwk;       // PINNED
  sessionKey: SessionKey;
  sidecarToken: string;
  statusFile?: string;        // verified snapshot is mirrored here for FastAPI to read locally
  now?: () => number;         // milliseconds; injectable so tests need no sleeping
  sessionTtl?: number;
  challengeTtl?: number;
};

const refuse = (code: string, message: string) => ({ error: { code, message } });

const ACTOR_RE = /^act_[0-9a-f]{16}$/;
const CRED_RE = /^cred_[0-9a-f]{24}$/;

export function createAuthApp(deps: AuthDeps) {
  const { db, region, issuerId, issuerPublicJwk, sessionKey } = deps;
  const nowMs = deps.now ?? (() => Date.now());
  const nowS = () => Math.floor(nowMs() / 1000);
  const sessionTtl = deps.sessionTtl ?? SESSION_TTL_SECONDS;
  const challengeTtl = deps.challengeTtl ?? CHALLENGE_TTL_SECONDS;
  const audience = `syncshield-region:${region}`; // chosen HERE; a client can never pick its own

  const app = new Hono();

  // ---- service credential (constant time) ----
  app.use('/internal/*', async (c, next) => {
    const given = Buffer.from(c.req.header('x-sidecar-token') ?? '');
    const want = Buffer.from(deps.sidecarToken);
    if (given.length !== want.length || !timingSafeEqual(given, want)) {
      return c.json(refuse('unauthorized', 'sidecar credential required'), 401);
    }
    await next();
  });

  // ---- status snapshot ----
  function storedSnapshot(): (Snapshot & { fetched_at: number }) | null {
    const row = db.prepare('SELECT jws, ver, issued_at, fetched_at FROM status_snapshot WHERE id = 1').get() as
      | { jws: string; ver: number; issued_at: number; fetched_at: number } | undefined;
    if (!row) return null;
    return { jws: row.jws, ver: row.ver, issued_at: row.issued_at, fetched_at: row.fetched_at, next_update: row.issued_at, statuses: {} };
  }

  // Accept a snapshot only if its signature verifies against the pinned issuer key and it is not a rollback.
  async function ingestSnapshot(jws: string): Promise<{ accepted: boolean; reason?: string; ver?: number }> {
    let snap: Snapshot;
    try {
      snap = await verifySnapshot(jws, issuerId, issuerPublicJwk);
    } catch {
      return { accepted: false, reason: 'bad_signature' };
    }
    const have = db.prepare('SELECT ver, issued_at FROM status_snapshot WHERE id = 1').get() as { ver: number; issued_at: number } | undefined;
    if (have && snap.ver < have.ver) return { accepted: false, reason: 'rollback', ver: have.ver };
    if (have && snap.ver === have.ver && snap.issued_at < have.issued_at) return { accepted: false, reason: 'older_same_version', ver: have.ver };
    db.prepare(
      `INSERT INTO status_snapshot (id, jws, ver, issued_at, fetched_at) VALUES (1, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET jws = excluded.jws, ver = excluded.ver, issued_at = excluded.issued_at, fetched_at = excluded.fetched_at`,
    ).run(jws, snap.ver, snap.issued_at, nowS());
    if (deps.statusFile) {
      mkdirSync(dirname(deps.statusFile), { recursive: true });
      const tmp = deps.statusFile + '.tmp';
      writeFileSync(tmp, jws);
      renameSync(tmp, deps.statusFile); // atomic: a reader never sees half a file
    }
    return { accepted: true, ver: snap.ver };
  }

  // The verified, parsed current snapshot (re-verified from the stored JWS so a DB edit cannot forge it).
  async function currentSnapshot(): Promise<Snapshot | null> {
    const row = storedSnapshot();
    if (!row) return null;
    try {
      return await verifySnapshot(row.jws, issuerId, issuerPublicJwk);
    } catch {
      return null;
    }
  }

  app.post('/internal/v1/status', async (c) => {
    const body = z.object({ jws: z.string() }).safeParse(await c.req.json().catch(() => null));
    if (!body.success) return c.json(refuse('bad_request', 'jws is required'), 400);
    const result = await ingestSnapshot(body.data.jws);
    return c.json(result, result.accepted ? 200 : 409);
  });

  app.get('/internal/v1/health', async (c) => {
    const snap = await currentSnapshot();
    return c.json({
      ok: true, region, session_kid: sessionKey.kid,
      status: snap ? { ver: snap.ver, issued_at: snap.issued_at, age_s: nowS() - snap.issued_at } : null,
    });
  });

  // ---- challenge ----
  app.post('/internal/v1/challenge', (c) => {
    const id = randomToken();
    const created = nowS();
    db.prepare('INSERT INTO challenges (id, nonce, audience, created_at, expires_at) VALUES (?, ?, ?, ?, ?)')
      .run(id, randomToken(), audience, created, created + challengeTtl);
    const row = db.prepare('SELECT * FROM challenges WHERE id = ?').get(id) as ChallengeRow;
    return c.json({
      challenge_id: row.id, nonce: row.nonce, audience: row.audience, expires_at: row.expires_at,
      required_claims: SYNCSHIELD_CLAIMS, credential_type: SYNCSHIELD_CREDENTIAL_TYPE, issuer_id: issuerId, region,
    }, 201);
  });

  // ---- session ----
  const finish = (id: string, state: 'completed' | 'denied', code: string) =>
    db.prepare("UPDATE challenges SET state = ?, result_code = ? WHERE id = ? AND state = 'verifying'").run(state, code, id);

  app.post('/internal/v1/session', async (c) => {
    const body = z.object({ challenge_id: z.string(), presentation: z.string() })
      .safeParse(await c.req.json().catch(() => null));
    if (!body.success) return c.json(refuse('bad_request', 'challenge_id and presentation are required'), 400);
    const { challenge_id, presentation } = body.data;

    // 1. Claim the challenge ATOMICALLY before doing anything slow. Exactly one concurrent caller wins.
    //    A failed attempt also consumes it (one try per challenge): a guesser gets nothing to iterate on.
    const claimed = db.prepare(
      "UPDATE challenges SET state = 'verifying' WHERE id = ? AND state = 'pending' AND expires_at > ?",
    ).run(challenge_id, nowS());
    if (claimed.changes !== 1) {
      const row = db.prepare('SELECT * FROM challenges WHERE id = ?').get(challenge_id) as ChallengeRow | undefined;
      if (!row) return c.json(refuse('unknown_challenge', 'No such challenge'), 404);
      if (row.state === 'pending' && row.expires_at <= nowS()) {
        db.prepare("UPDATE challenges SET state = 'expired' WHERE id = ? AND state = 'pending'").run(challenge_id);
        return c.json(refuse('challenge_expired', 'This challenge has expired'), 410);
      }
      if (row.state === 'expired') return c.json(refuse('challenge_expired', 'This challenge has expired'), 410);
      return c.json(refuse('challenge_consumed', 'This challenge was already used'), 409);
    }
    const ch = db.prepare('SELECT * FROM challenges WHERE id = ?').get(challenge_id) as ChallengeRow;

    const deny = (status: 401 | 403 | 503, code: string, message: string) => {
      finish(challenge_id, 'denied', code);
      return c.json(refuse(code, message), status);
    };

    // 2. The partner's real cryptographic verification (signature, disclosures, expiry, holder binding,
    //    nonce, audience, proof age). Nonce and audience come from OUR stored challenge, not from the caller.
    const result = await verifyPresentation({
      presentation, nonce: ch.nonce, audience: ch.audience, getIssuerPublicJwk: (id) => (id === issuerId ? issuerPublicJwk : null),
    });
    if (!result.ok) return deny(401, result.code, result.message);

    // 3. Profile checks on VERIFIED values only.
    const { claims, meta } = result;
    if (meta.iss !== issuerId) return deny(401, 'wrong_issuer', 'Credential was not issued by the trusted issuer');
    if (meta.vct !== SYNCSHIELD_CREDENTIAL_TYPE) return deny(401, 'wrong_credential_type', 'Not a SyncShield member credential');
    if (!meta.holderBound) return deny(401, 'missing_holder_binding', 'Credential is not bound to a holder key');
    if (meta.exp === undefined || meta.exp <= nowS()) return deny(401, 'credential_expired', 'The credential has expired');
    const names = Object.keys(claims).sort();
    const wanted = [...SYNCSHIELD_CLAIMS].sort();
    if (names.length !== wanted.length || names.some((n, i) => n !== wanted[i])) {
      return deny(401, 'wrong_claims', 'Exactly the requested claims must be disclosed');
    }
    const { actor_id, syncshield_member, syncshield_role, credential_id } = claims as Record<string, unknown>;
    if (typeof actor_id !== 'string' || !ACTOR_RE.test(actor_id)) return deny(401, 'bad_claim', 'actor_id is malformed');
    if (typeof credential_id !== 'string' || !CRED_RE.test(credential_id)) return deny(401, 'bad_claim', 'credential_id is malformed');
    if (syncshield_member !== true) return deny(403, 'not_a_member', 'This credential does not grant membership');
    if (typeof syncshield_role !== 'string' || !(SYNCSHIELD_ROLES as readonly string[]).includes(syncshield_role)) {
      return deny(403, 'bad_role', 'Unknown role');
    }

    // 4. Current status, from the cached issuer-signed snapshot (no issuer call, works while it is down).
    const snap = await currentSnapshot();
    const status = evaluateStatus({ snapshot: snap, credentialId: credential_id, now: nowS(), purpose: 'login' });
    if (!status.allow) {
      const unavailable = status.reason === 'no_status' || status.reason === 'status_stale_for_purpose' || status.reason === 'status_too_old';
      return deny(unavailable ? 503 : 403, status.reason === 'revoked' ? 'credential_revoked' : status.reason,
        unavailable ? 'Credential status is not fresh enough to approve a login' : 'Credential is not active');
    }

    // 5. Persist completion BEFORE returning the token.
    finish(challenge_id, 'completed', 'ok');

    // 6. Session token: short-lived, region-signed, never longer than the credential or the status bound.
    const iat = nowS();
    const exp = Math.min(iat + sessionTtl, meta.exp, snap!.issued_at + STATUS_POLICY.hard);
    const key = await importJWK(sessionKey.privateJwk as never, 'ES256');
    const token = await new SignJWT({
      cid: credential_id, role: syncshield_role, sver: snap!.ver, sat: snap!.issued_at, cexp: meta.exp,
    })
      .setProtectedHeader({ alg: 'ES256', typ: SESSION_TOKEN_TYPE, kid: sessionKey.kid })
      .setIssuer(SESSION_ISSUER_PREFIX + region)
      .setSubject(actor_id)
      .setAudience(SESSION_AUDIENCE)
      .setIssuedAt(iat)
      .setNotBefore(iat)
      .setExpirationTime(exp)
      .setJti(randomToken())
      .sign(key);

    return c.json({
      token, expires_at: exp, actor_id, role: syncshield_role, credential_id,
      auth_mode: status.mode, status_ver: snap!.ver, status_age_s: status.age, region,
    }, 200, { 'Cache-Control': 'no-store' });
  });

  return Object.assign(app, { ingestSnapshot, currentSnapshot });
}
