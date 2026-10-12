// Issuer API routes. The issuer database decides who may receive a credential.
import { Hono } from 'hono';
import { cors } from 'hono/cors';
import { z } from 'zod';
import type { DatabaseSync } from 'node:sqlite';
import { ISSUER_ID, ISSUER_NAME, CREDENTIAL_TYPE, SYNCSHIELD_CREDENTIAL_TYPE, URLS } from '@true/shared';
import { issueCredential, verifyHolderProof, randomClaimCode, randomToken, type Jwk } from '@true/credentials';
import type { Student, ClaimCode } from './db.ts';
import { issueSyncShieldCredential, registerSyncShieldRoutes, requireAdmin, type SyncShieldOptions } from './syncshield.ts';

const CLAIM_CODE_MINUTES = 10;
const CREDENTIAL_VALID_DAYS = 365;

export type IssuerKeys = { publicJwk: Jwk; privateJwk: Jwk };

// Every refusal uses the same JSON shape: { error: { code, message } }.
const refuse = (code: string, message: string) => ({ error: { code, message } });

// True if the person is 18 or older today. The issuer computes this so no verifier needs the birth date.
function isOver18(dob: string): boolean {
  const birth = new Date(dob);
  const eighteenth = new Date(birth.getFullYear() + 18, birth.getMonth(), birth.getDate());
  return eighteenth <= new Date();
}

// SYNCSHIELD ADAPTATION: `adminToken` protects the admin routes. Without it the original demo behaviour is
// unchanged (open routes), which the upstream tests rely on; a SyncShield deployment always sets it.
export type IssuerOptions = SyncShieldOptions;

export function createIssuerApp(db: DatabaseSync, keys: IssuerKeys, options: IssuerOptions = {}) {
  const app = new Hono();
  app.use('*', cors({ allowHeaders: ['content-type', 'x-issuer-admin-token'] }));
  const adminGuard = options.adminToken !== undefined ? requireAdmin(options.adminToken) : null;
  if (adminGuard) {
    app.use('/v1/students', adminGuard);
    app.use('/v1/claim-codes', async (c, next) => (c.req.method === 'POST' ? adminGuard(c, next) : next()));
  }

  // Public: who this issuer is and which key it signs with.
  app.get('/v1/.well-known/issuer', (c) => c.json({ id: ISSUER_ID, name: ISSUER_NAME, publicJwk: keys.publicJwk }));

  // Admin: list students for the issuer screen.
  app.get('/v1/students', (c) => {
    const students = db.prepare('SELECT * FROM students ORDER BY id').all() as Student[];
    return c.json({ students });
  });

  // Admin: "Issue credential" button. Creates a one-time, short-lived claim code for one student.
  app.post('/v1/claim-codes', async (c) => {
    const body = z.object({ studentId: z.number().int() }).safeParse(await c.req.json().catch(() => null));
    if (!body.success) return c.json(refuse('bad_request', 'studentId (number) is required'), 400);

    const student = db.prepare('SELECT * FROM students WHERE id = ?').get(body.data.studentId) as Student | undefined;
    if (!student) return c.json(refuse('unknown_student', 'No such student in the issuer database'), 404);
    if (student.status !== 'active') return c.json(refuse('student_not_active', 'This student is revoked'), 409);

    const code = randomClaimCode();
    const now = new Date();
    const expiresAt = new Date(now.getTime() + CLAIM_CODE_MINUTES * 60_000).toISOString();
    db.prepare('INSERT INTO claim_codes (code, student_id, nonce, created_at, expires_at) VALUES (?, ?, ?, ?, ?)').run(
      code, student.id, randomToken(), now.toISOString(), expiresAt,
    );
    // The QR offer is just a link that opens the wallet with the code filled in.
    return c.json({ code, expiresAt, offerUrl: `${URLS.wallet}/#/claim?code=${code}` }, 201);
  });

  // Looks up a claim code and says why it cannot be used, if it cannot.
  function checkClaimCode(code: string): { row: ClaimCode } | { code: string; message: string } {
    const row = db.prepare('SELECT * FROM claim_codes WHERE code = ?').get(code) as ClaimCode | undefined;
    if (!row) return { code: 'unknown_code', message: 'This claim code does not exist' };
    if (row.used_at) return { code: 'code_used', message: 'This claim code was already used' };
    if (new Date(row.expires_at) < new Date()) return { code: 'code_expired', message: 'This claim code has expired' };
    return { row };
  }

  // Wallet: step 1 of claiming. Returns the nonce the wallet must sign to prove it owns its key.
  app.get('/v1/claim-codes/:code', (c) => {
    const checked = checkClaimCode(c.req.param('code').toUpperCase());
    if (!('row' in checked)) return c.json(refuse(checked.code, checked.message), 400);
    return c.json({
      issuerId: ISSUER_ID,
      issuerName: ISSUER_NAME,
      credentialType: checked.row.profile === 'syncshield' ? SYNCSHIELD_CREDENTIAL_TYPE : CREDENTIAL_TYPE,
      profile: checked.row.profile ?? 'student',
      nonce: checked.row.nonce,
      expiresAt: checked.row.expires_at,
    });
  });

  // Wallet: step 2 of claiming. Sends the code and a signed proof that contains the wallet's public key.
  app.post('/v1/credentials', async (c) => {
    const body = z.object({ code: z.string(), proof: z.string() }).safeParse(await c.req.json().catch(() => null));
    if (!body.success) return c.json(refuse('bad_request', 'code and proof are required'), 400);

    const checked = checkClaimCode(body.data.code.toUpperCase());
    if (!('row' in checked)) return c.json(refuse(checked.code, checked.message), 400);
    const claimCode = checked.row;

    const student = db.prepare('SELECT * FROM students WHERE id = ?').get(claimCode.student_id) as Student;
    if (student.status !== 'active') return c.json(refuse('student_not_active', 'This student is revoked'), 409);

    // The proof must be signed over THIS code's nonce, by the key it carries.
    const holderPublicJwk = await verifyHolderProof(body.data.proof, { nonce: claimCode.nonce, audience: ISSUER_ID });
    if (!holderPublicJwk) return c.json(refuse('bad_holder_proof', 'Proof of key ownership failed'), 400);

    // Mark the code used FIRST, and only continue if this request was the one that marked it.
    // This stops two requests racing with the same code from both getting a credential.
    const marked = db.prepare('UPDATE claim_codes SET used_at = ? WHERE code = ? AND used_at IS NULL').run(
      new Date().toISOString(), claimCode.code,
    );
    if (marked.changes !== 1) return c.json(refuse('code_used', 'This claim code was already used'), 400);

    // SYNCSHIELD ADAPTATION: a code provisioned for the SyncShield profile yields ONLY the minimal member credential.
    if (claimCode.profile === 'syncshield') {
      try {
        const issued = await issueSyncShieldCredential(db, keys, student.id, holderPublicJwk);
        return c.json({ credential: issued.credential, issuerId: ISSUER_ID, issuerName: ISSUER_NAME,
          profile: 'syncshield', credentialId: issued.credentialId, role: issued.role, expiresAt: issued.expiresAt }, 201);
      } catch {
        return c.json(refuse('not_a_member', 'This person is not a SyncShield member'), 409);
      }
    }

    // Give the student a position in the revocation status list (kept if they already have one).
    let statusIndex = student.status_list_index;
    if (statusIndex === null) {
      const max = db.prepare('SELECT MAX(status_list_index) AS m FROM students').get() as { m: number | null };
      statusIndex = max.m === null ? 0 : max.m + 1;
      db.prepare('UPDATE students SET status_list_index = ? WHERE id = ?').run(statusIndex, student.id);
    }

    const credential = await issueCredential({
      issuerId: ISSUER_ID,
      issuerPrivateJwk: keys.privateJwk,
      holderPublicJwk,
      type: CREDENTIAL_TYPE,
      validDays: CREDENTIAL_VALID_DAYS,
      claims: {
        // Raw claims, copied from the student row.
        name: student.name,
        pnr: student.pnr,
        dob: student.dob,
        address: student.address,
        class: student.class,
        section: student.section,
        branch: student.branch,
        photo: student.photo,
        institution: ISSUER_NAME,
        // Derived claims: separate yes/no facts, so these checks never need the raw date of birth.
        is_student: true,
        age_over_18: isOver18(student.dob),
      },
    });
    return c.json({ credential, issuerId: ISSUER_ID, issuerName: ISSUER_NAME }, 201);
  });

  registerSyncShieldRoutes(app, db, keys, options);
  return app;
}
