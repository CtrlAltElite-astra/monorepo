// SYNCSHIELD ADAPTATION (not part of upstream TRUE).
//
// Adds, without changing the original student demonstration:
//   * members      : which issuer-database persons may use SyncShield, with a server-controlled role
//   * credentials  : an issuer-side record per issued SyncShield credential (id, status, expiry)
//   * claim codes  : admin-provisioned, one-time, bound to ONE member (no public "give me a code for id N")
//   * revocation   : an admin action, published as an issuer-signed status snapshot
//
// The issuer database is the membership authority. The browser can never choose a role: the role claim is
// copied from the members table at issuance time.
import { randomBytes, timingSafeEqual } from 'node:crypto';
import type { DatabaseSync } from 'node:sqlite';
import { Hono } from 'hono';
import { z } from 'zod';
import { SignJWT, importJWK } from 'jose';
import { ISSUER_ID, SYNCSHIELD_CREDENTIAL_TYPE, SYNCSHIELD_CLAIMS, SYNCSHIELD_ROLES, STATUS_TOKEN_TYPE, STATUS_POLICY, URLS, type SyncShieldRole } from '@true/shared';
import { issueCredential, randomClaimCode, randomToken, type Jwk } from '@true/credentials';

export type IssuerKeys = { publicJwk: Jwk; privateJwk: Jwk };
type Clock = () => Date;
const refuse = (code: string, message: string) => ({ error: { code, message } });

const CREDENTIAL_VALID_DAYS = 365;
const MAX_CLAIM_CODE_MINUTES = 24 * 60;

// ---------- schema (additive migration; safe on every start) ----------

const SCHEMA = `
CREATE TABLE IF NOT EXISTS syncshield_members (
  student_id INTEGER PRIMARY KEY REFERENCES students(id),
  actor_id TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS syncshield_credentials (
  credential_id TEXT PRIMARY KEY,
  student_id INTEGER NOT NULL REFERENCES students(id),
  actor_id TEXT NOT NULL,
  role TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  issued_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  revoked_reason TEXT
);
CREATE TABLE IF NOT EXISTS issuer_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
`;

export function migrateSyncShield(db: DatabaseSync): void {
  db.exec(SCHEMA);
  const cols = db.prepare('PRAGMA table_info(claim_codes)').all() as { name: string }[];
  if (!cols.some((c) => c.name === 'profile')) {
    db.exec("ALTER TABLE claim_codes ADD COLUMN profile TEXT NOT NULL DEFAULT 'student'");
  }
}

// ---------- status generation (monotonic: a rollback is detectable) ----------

export function statusGeneration(db: DatabaseSync): number {
  const row = db.prepare("SELECT v FROM issuer_meta WHERE k = 'status_generation'").get() as { v: string } | undefined;
  return row ? Number(row.v) : 0;
}

function bumpGeneration(db: DatabaseSync): number {
  const next = statusGeneration(db) + 1;
  db.prepare("INSERT INTO issuer_meta (k, v) VALUES ('status_generation', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v").run(String(next));
  return next;
}

// ---------- members ----------

export type Member = { actor_id: string; role: SyncShieldRole; student_id: number; name: string; status: string };

const newActorId = () => 'act_' + randomBytes(8).toString('hex');
const newCredentialId = () => 'cred_' + randomBytes(12).toString('hex');

// A synthetic demo person (never real data). Idempotent when `actorId` already exists.
export function provisionMember(
  db: DatabaseSync,
  input: { name: string; role: SyncShieldRole; actorId?: string },
  now: Clock = () => new Date(),
): Member {
  if (!(SYNCSHIELD_ROLES as readonly string[]).includes(input.role)) throw new Error(`unknown role ${input.role}`);
  if (input.actorId) {
    const existing = db.prepare(
      `SELECT m.actor_id, m.role, m.student_id, s.name, s.status FROM syncshield_members m
       JOIN students s ON s.id = m.student_id WHERE m.actor_id = ?`).get(input.actorId) as Member | undefined;
    if (existing) return existing;
  }
  const actorId = input.actorId ?? newActorId();
  const stamp = now().toISOString();
  db.exec('BEGIN IMMEDIATE');
  try {
    const result = db.prepare(
      `INSERT INTO students (name, pnr, dob, address, class, section, branch, photo, created_at)
       VALUES (?, ?, '2000-01-01', 'synthetic demo member', 'SyncShield', '-', 'SyncShield', '', ?)`,
    ).run(input.name, 'SS-' + actorId, stamp);
    const studentId = Number(result.lastInsertRowid);
    db.prepare('INSERT INTO syncshield_members (student_id, actor_id, role, created_at) VALUES (?, ?, ?, ?)')
      .run(studentId, actorId, input.role, stamp);
    db.exec('COMMIT');
    return { actor_id: actorId, role: input.role, student_id: studentId, name: input.name, status: 'active' };
  } catch (error) {
    db.exec('ROLLBACK');
    throw error;
  }
}

export function listMembers(db: DatabaseSync): Member[] {
  return db.prepare(
    `SELECT m.actor_id, m.role, m.student_id, s.name, s.status FROM syncshield_members m
     JOIN students s ON s.id = m.student_id ORDER BY m.student_id`).all() as Member[];
}

// ---------- claim codes ----------

export type ClaimCodeResult = { code: string; expiresAt: string } | { error: { code: string; message: string }; status: number };

export function createSyncShieldClaimCode(
  db: DatabaseSync, actorId: string, ttlMinutes: number, now: Clock = () => new Date(),
): ClaimCodeResult {
  const member = db.prepare(
    `SELECT m.student_id, s.status FROM syncshield_members m JOIN students s ON s.id = m.student_id WHERE m.actor_id = ?`,
  ).get(actorId) as { student_id: number; status: string } | undefined;
  if (!member) return { ...refuse('unknown_member', 'No such SyncShield member'), status: 404 };
  if (member.status !== 'active') return { ...refuse('member_not_active', 'This member is revoked'), status: 409 };
  const ttl = Math.min(Math.max(ttlMinutes, 1), MAX_CLAIM_CODE_MINUTES);
  const code = randomClaimCode();
  const created = now();
  const expiresAt = new Date(created.getTime() + ttl * 60_000).toISOString();
  db.prepare(
    "INSERT INTO claim_codes (code, student_id, nonce, created_at, expires_at, profile) VALUES (?, ?, ?, ?, ?, 'syncshield')",
  ).run(code, member.student_id, randomToken(), created.toISOString(), expiresAt);
  return { code, expiresAt };
}

// ---------- issuance ----------

// Called by the claim flow AFTER the code was consumed and the holder proof verified.
export async function issueSyncShieldCredential(
  db: DatabaseSync, keys: IssuerKeys, studentId: number, holderPublicJwk: Jwk, now: Clock = () => new Date(),
): Promise<{ credential: string; credentialId: string; role: SyncShieldRole; actorId: string; expiresAt: string }> {
  const member = db.prepare('SELECT actor_id, role FROM syncshield_members WHERE student_id = ?')
    .get(studentId) as { actor_id: string; role: SyncShieldRole } | undefined;
  if (!member) throw new Error('not_a_member');
  const credentialId = newCredentialId();
  const issued = now();
  const expiresAt = new Date(issued.getTime() + CREDENTIAL_VALID_DAYS * 86_400_000).toISOString();
  const credential = await issueCredential({
    issuerId: ISSUER_ID,
    issuerPrivateJwk: keys.privateJwk,
    holderPublicJwk,
    type: SYNCSHIELD_CREDENTIAL_TYPE,
    validDays: CREDENTIAL_VALID_DAYS,
    claims: {
      actor_id: member.actor_id,
      syncshield_member: true,
      syncshield_role: member.role, // computed here, from the members table, never from the caller
      credential_id: credentialId,
    },
  });
  db.prepare(
    `INSERT INTO syncshield_credentials (credential_id, student_id, actor_id, role, issued_at, expires_at)
     VALUES (?, ?, ?, ?, ?, ?)`).run(credentialId, studentId, member.actor_id, member.role, issued.toISOString(), expiresAt);
  bumpGeneration(db);
  return { credential, credentialId, role: member.role, actorId: member.actor_id, expiresAt };
}

// ---------- revocation and the signed status snapshot ----------

export function revokeMember(db: DatabaseSync, actorId: string, reason: string, now: Clock = () => new Date()): number {
  const member = db.prepare('SELECT student_id FROM syncshield_members WHERE actor_id = ?').get(actorId) as { student_id: number } | undefined;
  if (!member) return -1;
  const stamp = now().toISOString();
  db.exec('BEGIN IMMEDIATE');
  try {
    // No new credential can be issued, and every issued one is marked revoked.
    db.prepare("UPDATE students SET status = 'revoked', revoked_reason = ? WHERE id = ?").run(reason, member.student_id);
    const changed = db.prepare(
      "UPDATE syncshield_credentials SET status = 'revoked', revoked_at = ?, revoked_reason = ? WHERE actor_id = ? AND status = 'active'",
    ).run(stamp, reason, actorId);
    bumpGeneration(db);
    db.exec('COMMIT');
    return Number(changed.changes);
  } catch (error) {
    db.exec('ROLLBACK');
    throw error;
  }
}

// Compact bulk snapshot: opaque credential ids and a one-letter status. No personal data.
// This is NOT the W3C Bitstring Status List; it is a signed JSON document (documented as such).
export async function signStatusSnapshot(
  db: DatabaseSync, keys: IssuerKeys, now: Clock = () => new Date(),
): Promise<{ jws: string; ver: number; issued_at: number }> {
  const rows = db.prepare('SELECT credential_id, status FROM syncshield_credentials').all() as { credential_id: string; status: string }[];
  const statuses: Record<string, 'a' | 'r'> = {};
  for (const r of rows) statuses[r.credential_id] = r.status === 'active' ? 'a' : 'r';
  const issuedAt = Math.floor(now().getTime() / 1000);
  const ver = statusGeneration(db);
  const key = await importJWK(keys.privateJwk as never, 'ES256');
  const jws = await new SignJWT({ ver, statuses, next_update: issuedAt + STATUS_POLICY.cached })
    .setProtectedHeader({ alg: 'ES256', typ: STATUS_TOKEN_TYPE })
    .setIssuer(ISSUER_ID)
    .setIssuedAt(issuedAt)
    .sign(key);
  return { jws, ver, issued_at: issuedAt };
}

// ---------- routes ----------

export type SyncShieldOptions = { adminToken?: string; now?: Clock };

export function requireAdmin(adminToken: string | undefined) {
  return async (c: any, next: () => Promise<void>) => {
    if (!adminToken) return c.json(refuse('admin_token_not_configured', 'Issuer admin token is not configured'), 503);
    const given = c.req.header('x-issuer-admin-token') ?? '';
    const a = Buffer.from(given);
    const b = Buffer.from(adminToken);
    if (a.length !== b.length || !timingSafeEqual(a, b)) return c.json(refuse('unauthorized', 'Issuer admin token required'), 401);
    await next();
  };
}

export function registerSyncShieldRoutes(app: Hono, db: DatabaseSync, keys: IssuerKeys, options: SyncShieldOptions = {}) {
  const now = options.now ?? (() => new Date());
  const admin = requireAdmin(options.adminToken);

  // Public: what a wallet needs to know to claim and to log in.
  app.get('/v1/syncshield/profile', (c) =>
    c.json({ issuerId: ISSUER_ID, credentialType: SYNCSHIELD_CREDENTIAL_TYPE, claims: SYNCSHIELD_CLAIMS, roles: SYNCSHIELD_ROLES, wallet: URLS.wallet }));

  // Public: the issuer-signed status snapshot every region caches and verifies with the pinned issuer key.
  app.get('/v1/syncshield/status-snapshot', async (c) => c.json(await signStatusSnapshot(db, keys, now)));

  app.get('/v1/syncshield/members', admin, (c) => c.json({ members: listMembers(db) }));

  app.post('/v1/syncshield/members', admin, async (c) => {
    const body = z.object({ name: z.string().min(1).max(80), role: z.enum(SYNCSHIELD_ROLES) })
      .safeParse(await c.req.json().catch(() => null));
    if (!body.success) return c.json(refuse('bad_request', 'name and role (officer|supervisor|admin) are required'), 400);
    return c.json(provisionMember(db, body.data, now), 201);
  });

  app.post('/v1/syncshield/claim-codes', admin, async (c) => {
    const body = z.object({ actorId: z.string(), ttlMinutes: z.number().int().min(1).max(MAX_CLAIM_CODE_MINUTES).optional() })
      .safeParse(await c.req.json().catch(() => null));
    if (!body.success) return c.json(refuse('bad_request', 'actorId is required'), 400);
    const result = createSyncShieldClaimCode(db, body.data.actorId, body.data.ttlMinutes ?? 10, now);
    if ('error' in result) return c.json({ error: result.error }, result.status as 404 | 409);
    return c.json({ code: result.code, expiresAt: result.expiresAt }, 201);
  });

  app.post('/v1/syncshield/members/:actorId/revoke', admin, async (c) => {
    const body = z.object({ reason: z.string().min(1).max(200) }).safeParse(await c.req.json().catch(() => null));
    if (!body.success) return c.json(refuse('bad_request', 'reason is required'), 400);
    const revoked = revokeMember(db, c.req.param('actorId'), body.data.reason, now);
    if (revoked < 0) return c.json(refuse('unknown_member', 'No such SyncShield member'), 404);
    return c.json({ revokedCredentials: revoked, statusVersion: statusGeneration(db) });
  });
}
