// SyncShield integration tests: REAL partner crypto (credentials package), REAL issuer routes, REAL auth sidecar
// routes, in-process. Only the clock and the network are simulated.
import { beforeEach, describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { jwtVerify, importJWK, decodeJwt, decodeProtectedHeader, calculateJwkThumbprint } from 'jose';
import { ISSUER_ID, SESSION_AUDIENCE, SESSION_TOKEN_TYPE, STATUS_POLICY, SYNCSHIELD_CLAIMS, SYNCSHIELD_CREDENTIAL_TYPE } from '@true/shared';
import {
  createHolderProof, createPresentation, disclosedClaimNames, generateHolderKey, generateSigningKeyPair, issueCredential,
} from '@true/credentials';
import { openIssuerDb } from '../services/issuer-api/src/db.ts';
import { createIssuerApp } from '../services/issuer-api/src/app.ts';
import { openAuthDb } from '../services/syncshield-auth/src/db.ts';
import { createAuthApp } from '../services/syncshield-auth/src/app.ts';
import { evaluateStatus } from '../services/syncshield-auth/src/status.ts';

type Req = { request: (path: string, init?: RequestInit) => Response | Promise<Response> };
type Holder = Awaited<ReturnType<typeof generateHolderKey>>;

const ADMIN = 'test-admin-token';
const SIDECAR = 'test-sidecar-token';

let clock: number; // ms; shared by the issuer and the sidecar so freshness tests need no sleeping
let issuerKeys: Awaited<ReturnType<typeof generateSigningKeyPair>>;
let issuerDb: ReturnType<typeof openIssuerDb>;
let issuer: Req;
let authDb: ReturnType<typeof openAuthDb>;
let auth: ReturnType<typeof createAuthApp>;
let sessionPublic: Record<string, unknown>;

const json = (body: unknown, headers: Record<string, string> = {}) => ({
  method: 'POST', headers: { 'content-type': 'application/json', ...headers }, body: JSON.stringify(body),
});
const adminPost = (path: string, body: unknown) => issuer.request(path, json(body, { 'x-issuer-admin-token': ADMIN }));
const sidecarPost = (path: string, body: unknown, token = SIDECAR) => auth.request(path, json(body, { 'x-sidecar-token': token }));

async function buildAuth(db = authDb) {
  const pair = await generateSigningKeyPair();
  sessionPublic = pair.publicJwk;
  return createAuthApp({
    db, region: 'mum', issuerId: ISSUER_ID, issuerPublicJwk: issuerKeys.publicJwk,
    sessionKey: { kid: 'mum-test', privateJwk: pair.privateJwk, publicJwk: pair.publicJwk },
    sidecarToken: SIDECAR, now: () => clock,
  });
}

beforeEach(async () => {
  clock = Date.now();
  issuerKeys = await generateSigningKeyPair();
  issuerDb = openIssuerDb(':memory:');
  issuer = createIssuerApp(issuerDb, issuerKeys, { adminToken: ADMIN, now: () => new Date(clock) });
  authDb = openAuthDb(':memory:');
  auth = await buildAuth();
});

// ---- wallet-side helpers (the same packages/credentials code the browser wallet uses) ----
async function provision(role: 'officer' | 'supervisor' | 'admin' = 'officer', name = 'Demo Member') {
  const res = await adminPost('/v1/syncshield/members', { name, role });
  expect(res.status).toBe(201);
  return (await res.json()) as { actor_id: string; role: string; student_id: number };
}

async function claimWith(code: string, holder: Holder, issuerApp: Req = issuer) {
  const offer = await (await issuerApp.request(`/v1/claim-codes/${code}`)).json();
  const proof = await createHolderProof({
    holderPrivateKey: holder.privateKey, holderPublicJwk: holder.publicJwk, nonce: offer.nonce ?? 'x', audience: ISSUER_ID,
  });
  return issuerApp.request('/v1/credentials', json({ code, proof }));
}

async function issueTo(actorId: string, holder?: Holder) {
  const h = holder ?? (await generateHolderKey());
  const code = await (await adminPost('/v1/syncshield/claim-codes', { actorId })).json();
  const res = await claimWith(code.code, h);
  expect(res.status).toBe(201);
  const body = await res.json();
  return { holder: h, credential: body.credential as string, credentialId: body.credentialId as string, role: body.role as string };
}

async function syncStatus() {
  const snap = await (await issuer.request('/v1/syncshield/status-snapshot')).json();
  const res = await sidecarPost('/internal/v1/status', { jws: snap.jws });
  return { status: res.status, body: await res.json(), snap };
}

async function challenge() {
  const res = await sidecarPost('/internal/v1/challenge', {});
  expect(res.status).toBe(201);
  return (await res.json()) as { challenge_id: string; nonce: string; audience: string; required_claims: string[] };
}

async function present(cred: { holder: Holder; credential: string }, ch: { nonce: string; audience: string }, claims: readonly string[] = SYNCSHIELD_CLAIMS) {
  return createPresentation({
    credential: cred.credential, holderPrivateKey: cred.holder.privateKey, claims: [...claims], nonce: ch.nonce, audience: ch.audience,
  });
}

async function login(cred: { holder: Holder; credential: string }) {
  const ch = await challenge();
  const presentation = await present(cred, ch);
  return sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation });
}

// ================================================================================================
describe('1. member -> claim code -> holder proof -> credential -> session', () => {
  it('an active member gets a minimal credential and a verifiable regional session', async () => {
    const m = await provision('officer', 'Alice');
    const cred = await issueTo(m.actor_id);
    await syncStatus();
    const res = await login(cred);
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('no-store');
    const out = await res.json();
    expect(out).toMatchObject({ actor_id: m.actor_id, role: 'officer', credential_id: cred.credentialId, auth_mode: 'online' });

    // the token verifies with the region's PUBLIC key and carries exactly the contract
    const key = await importJWK(sessionPublic as never, 'ES256');
    const { payload, protectedHeader } = await jwtVerify(out.token, key, { audience: SESSION_AUDIENCE, issuer: 'syncshield-region:mum' });
    expect(protectedHeader.typ).toBe(SESSION_TOKEN_TYPE);
    expect(protectedHeader.kid).toBe('mum-test');
    expect(payload.sub).toBe(m.actor_id);
    expect(payload).toMatchObject({ role: 'officer', cid: cred.credentialId });
    expect(payload.exp! - payload.iat!).toBeLessThanOrEqual(300);
    expect(typeof payload.jti).toBe('string');
  });

  it('discloses only the four SyncShield claims, never name/DOB/address/photo/PNR', async () => {
    const m = await provision('officer', 'Zubin Privatename');
    const cred = await issueTo(m.actor_id);
    const ch = await challenge();
    const p = await present(cred, ch);
    expect(disclosedClaimNames(p).sort()).toEqual([...SYNCSHIELD_CLAIMS].sort());
    const plain = p.split(/[~.]/).map((x) => Buffer.from(x, 'base64url').toString('utf8')).join(' ');
    for (const secret of ['Zubin', 'Privatename', '2000-01-01', 'synthetic demo member', 'SS-act_']) expect(plain).not.toContain(secret);
  });

  it('the role comes from the issuer database, not from anything the caller sends', async () => {
    const sup = await provision('supervisor', 'Sunita');
    const cred = await issueTo(sup.actor_id);
    const out = await (await (async () => { await syncStatus(); return login(cred); })()).json();
    expect(out.role).toBe('supervisor');
  });
});

describe('2. membership gate and claim codes', () => {
  it('unknown member and unknown code get nothing', async () => {
    const unknown = await adminPost('/v1/syncshield/claim-codes', { actorId: 'act_0000000000000000' });
    expect(unknown.status).toBe(404);
    expect((await unknown.json()).error.code).toBe('unknown_member');
    const made = await claimWith('ZZZZZZZZ', await generateHolderKey());
    expect(made.status).toBe(400);
  });

  it('a revoked (inactive) member cannot get a code or a credential', async () => {
    const m = await provision();
    const code = await (await adminPost('/v1/syncshield/claim-codes', { actorId: m.actor_id })).json();
    await adminPost(`/v1/syncshield/members/${m.actor_id}/revoke`, { reason: 'left the team' });
    const claimed = await claimWith(code.code, await generateHolderKey());
    expect(claimed.status).toBe(409);
    const again = await adminPost('/v1/syncshield/claim-codes', { actorId: m.actor_id });
    expect(again.status).toBe(409);
  });

  it('reused and expired claim codes are refused', async () => {
    const m = await provision();
    const { code } = await (await adminPost('/v1/syncshield/claim-codes', { actorId: m.actor_id })).json();
    expect((await claimWith(code, await generateHolderKey())).status).toBe(201);
    const second = await claimWith(code, await generateHolderKey());
    expect((await second.json()).error.code).toBe('code_used');

    const m2 = await provision();
    const c2 = await (await adminPost('/v1/syncshield/claim-codes', { actorId: m2.actor_id })).json();
    issuerDb.prepare('UPDATE claim_codes SET expires_at = ? WHERE code = ?').run('2000-01-01T00:00:00.000Z', c2.code);
    expect(((await (await claimWith(c2.code, await generateHolderKey())).json()).error.code)).toBe('code_expired');
  });

  it('two simultaneous redemptions of one code produce exactly one credential', async () => {
    const m = await provision();
    const { code } = await (await adminPost('/v1/syncshield/claim-codes', { actorId: m.actor_id })).json();
    const [a, b] = await Promise.all([claimWith(code, await generateHolderKey()), claimWith(code, await generateHolderKey())]);
    expect([a.status, b.status].sort()).toEqual([201, 400]);
    const count = issuerDb.prepare('SELECT COUNT(*) AS n FROM syncshield_credentials').get() as { n: number };
    expect(count.n).toBe(1);
  });

  it('issuer admin routes need the admin token; the original open routes are closed too', async () => {
    expect((await issuer.request('/v1/students')).status).toBe(401);
    expect((await issuer.request('/v1/syncshield/members')).status).toBe(401);
    expect((await issuer.request('/v1/syncshield/members', { headers: { 'x-issuer-admin-token': 'wrong' } })).status).toBe(401);
    expect((await issuer.request('/v1/syncshield/members', { headers: { 'x-issuer-admin-token': ADMIN } })).status).toBe(200);
    expect((await issuer.request('/v1/claim-codes', json({ studentId: 1 }))).status).toBe(401);
    // public, read-only
    expect((await issuer.request('/v1/syncshield/status-snapshot')).status).toBe(200);
    expect((await issuer.request('/v1/.well-known/issuer')).status).toBe(200);
  });

  it('the sidecar rejects callers without the service credential', async () => {
    expect((await auth.request('/internal/v1/challenge', json({}))).status).toBe(401);
    expect((await sidecarPost('/internal/v1/challenge', {}, 'wrong')).status).toBe(401);
  });

  it('issuance input can not override reserved claims', async () => {
    const holder = await generateHolderKey();
    await expect(issueCredential({
      issuerId: ISSUER_ID, issuerPrivateJwk: issuerKeys.privateJwk, holderPublicJwk: holder.publicJwk,
      type: SYNCSHIELD_CREDENTIAL_TYPE, validDays: 1, claims: { exp: 99999999999, syncshield_role: 'admin' },
    })).rejects.toThrow(/reserved/);
  });
});

describe('3. presentations that must be denied (real crypto, no state change)', () => {
  const expectDenied = async (res: Response, codes: string[]) => {
    expect([401, 403, 503]).toContain(res.status);
    const body = await res.json();
    expect(codes).toContain(body.error.code);
    expect(body.token).toBeUndefined();
  };

  it('a changed disclosure value is rejected', async () => {
    const m = await provision('officer');
    const cred = await issueTo(m.actor_id);
    await syncStatus();
    const ch = await challenge();
    const p = await present(cred, ch);
    const parts = p.split('~');
    const d = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));
    d[2] = d[1] === 'syncshield_role' ? 'admin' : d[2];
    parts[1] = Buffer.from(JSON.stringify(d)).toString('base64url');
    // find the role disclosure explicitly so the tamper is meaningful
    for (let i = 1; i < parts.length - 1; i++) {
      const dd = JSON.parse(Buffer.from(parts[i], 'base64url').toString('utf8'));
      if (dd[1] === 'syncshield_role') { dd[2] = 'admin'; parts[i] = Buffer.from(JSON.stringify(dd)).toString('base64url'); }
    }
    await expectDenied(await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: parts.join('~') }), ['tampered_disclosure', 'invalid_presentation', 'bad_signature']);
  });

  it('a credential signed by a different key under the real issuer name is rejected', async () => {
    const forgerKeys = await generateSigningKeyPair();
    const forgerDb = openIssuerDb(':memory:');
    const forger = createIssuerApp(forgerDb, forgerKeys, { adminToken: ADMIN });
    const fm = await (await forger.request('/v1/syncshield/members', json({ name: 'Mallory', role: 'admin' }, { 'x-issuer-admin-token': ADMIN }))).json();
    const code = await (await forger.request('/v1/syncshield/claim-codes', json({ actorId: fm.actor_id }, { 'x-issuer-admin-token': ADMIN }))).json();
    const holder = await generateHolderKey();
    const claimed = await claimWith(code.code, holder, forger);
    const { credential } = await claimed.json();
    await syncStatus();
    const ch = await challenge();
    const p = await present({ holder, credential }, ch);
    await expectDenied(await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: p }), ['bad_signature', 'invalid_presentation']);
  });

  it('a credential of the wrong type (the original student credential) is rejected', async () => {
    const holder = await generateHolderKey();
    const { code } = await (await adminPost('/v1/claim-codes', { studentId: 1 })).json();
    const { credential } = await (await claimWith(code, holder)).json();
    await syncStatus();
    const ch = await challenge();
    const p = await createPresentation({ credential, holderPrivateKey: holder.privateKey, claims: ['is_student'], nonce: ch.nonce, audience: ch.audience });
    await expectDenied(await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: p }), ['wrong_credential_type', 'wrong_claims']);
  });

  it('a stolen credential presented with another holder key is rejected', async () => {
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    await syncStatus();
    const thief = await generateHolderKey();
    const ch = await challenge();
    const p = await present({ holder: thief, credential: cred.credential }, ch);
    await expectDenied(await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: p }), ['bad_signature', 'invalid_presentation', 'missing_holder_binding']);
  });

  it('a presentation made for the wrong audience or another nonce is rejected', async () => {
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    await syncStatus();
    const ch = await challenge();
    const wrongAud = await createPresentation({ credential: cred.credential, holderPrivateKey: cred.holder.privateKey, claims: [...SYNCSHIELD_CLAIMS], nonce: ch.nonce, audience: 'syncshield-region:pun' });
    await expectDenied(await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: wrongAud }), ['wrong_audience']);

    const other = await challenge();
    const forOther = await present(cred, other);
    const ch2 = await challenge();
    await expectDenied(await sidecarPost('/internal/v1/session', { challenge_id: ch2.challenge_id, presentation: forOther }), ['nonce_mismatch']);
  });

  it('undisclosed or extra claims are rejected (exactly the requested set)', async () => {
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    await syncStatus();
    const ch = await challenge();
    const p = await present(cred, ch, ['actor_id', 'credential_id']); // role and member hidden
    await expectDenied(await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: p }), ['wrong_claims']);
  });

  it('an expired credential is rejected (library expiry is real, not just a comment)', async () => {
    const m = await provision();
    const holder = await generateHolderKey();
    const expired = await issueCredential({
      issuerId: ISSUER_ID, issuerPrivateJwk: issuerKeys.privateJwk, holderPublicJwk: holder.publicJwk,
      type: SYNCSHIELD_CREDENTIAL_TYPE, validDays: -1,
      claims: { actor_id: m.actor_id, syncshield_member: true, syncshield_role: 'officer', credential_id: 'cred_' + 'a'.repeat(24) },
    });
    await syncStatus();
    const ch = await challenge();
    const p = await present({ holder, credential: expired }, ch);
    await expectDenied(await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: p }), ['credential_expired', 'invalid_presentation']);
  });

  it('a credential the issuer never recorded (unknown credential id) is denied', async () => {
    const m = await provision();
    const holder = await generateHolderKey();
    const signedButUnrecorded = await issueCredential({
      issuerId: ISSUER_ID, issuerPrivateJwk: issuerKeys.privateJwk, holderPublicJwk: holder.publicJwk,
      type: SYNCSHIELD_CREDENTIAL_TYPE, validDays: 30,
      claims: { actor_id: m.actor_id, syncshield_member: true, syncshield_role: 'admin', credential_id: 'cred_' + 'b'.repeat(24) },
    });
    await syncStatus();
    const ch = await challenge();
    const p = await present({ holder, credential: signedButUnrecorded }, ch);
    await expectDenied(await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: p }), ['unknown_credential']);
  });
});

describe('4. challenge lifecycle', () => {
  it('a challenge works once; replay and a second user of the same challenge are refused', async () => {
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    await syncStatus();
    const ch = await challenge();
    const p = await present(cred, ch);
    expect((await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: p })).status).toBe(200);
    const replay = await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: p });
    expect(replay.status).toBe(409);
    expect((await replay.json()).error.code).toBe('challenge_consumed');
  });

  it('simultaneous submissions of the same challenge yield exactly one session', async () => {
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    await syncStatus();
    const ch = await challenge();
    const p = await present(cred, ch);
    const results = await Promise.all([1, 2, 3, 4].map(() => sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: p })));
    expect(results.filter((r) => r.status === 200).length).toBe(1);
    expect(results.filter((r) => r.status === 409).length).toBe(3);
  });

  it('a FAILED attempt also consumes the challenge (one try per challenge)', async () => {
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    await syncStatus();
    const ch = await challenge();
    const bad = await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: 'not-a-presentation' });
    expect(bad.status).toBe(401);
    const good = await present(cred, ch);
    const retry = await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: good });
    expect(retry.status).toBe(409);
  });

  it('an expired challenge is refused with 410', async () => {
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    await syncStatus();
    const ch = await challenge();
    const p = await present(cred, ch);
    clock += 121_000;
    const res = await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: p });
    expect(res.status).toBe(410);
  });

  it('caller-supplied nonce / audience are ignored: only the stored challenge counts', async () => {
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    await syncStatus();
    const ch = await challenge();
    const attackerNonce = 'attacker-nonce';
    const p = await createPresentation({ credential: cred.credential, holderPrivateKey: cred.holder.privateKey, claims: [...SYNCSHIELD_CLAIMS], nonce: attackerNonce, audience: 'whatever' });
    const res = await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: p, nonce: attackerNonce, audience: 'whatever' });
    expect(res.status).toBe(401);
  });

  it('an unknown challenge id is 404', async () => {
    const res = await sidecarPost('/internal/v1/session', { challenge_id: 'nope', presentation: 'x' });
    expect(res.status).toBe(404);
  });
});

describe('5. revocation, status snapshot, rollback', () => {
  it('a real, already-issued credential works, then is denied after revoke + refresh', async () => {
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    await syncStatus();
    expect((await login(cred)).status).toBe(200);

    expect((await adminPost(`/v1/syncshield/members/${m.actor_id}/revoke`, { reason: 'key lost' })).status).toBe(200);
    // before the region hears about it, the cached snapshot still says active (documented partition window)
    expect((await login(cred)).status).toBe(200);
    await syncStatus();
    const denied = await login(cred);
    expect(denied.status).toBe(403);
    expect((await denied.json()).error.code).toBe('credential_revoked');
  });

  it('the snapshot only contains opaque ids and one-letter statuses', async () => {
    const m = await provision('officer', 'Privacy Check');
    await issueTo(m.actor_id);
    const { snap } = await syncStatus();
    const payload = decodeJwt(snap.jws) as Record<string, unknown>;
    const text = JSON.stringify(payload);
    expect(text).not.toContain('Privacy');
    expect(text).not.toContain(m.actor_id);
    expect(Object.values(payload.statuses as Record<string, string>).every((s) => s === 'a' || s === 'r')).toBe(true);
    expect(decodeProtectedHeader(snap.jws).typ).toBe('syncshield-status+jwt');
  });

  it('a tampered or foreign-signed snapshot is rejected and the last good one is kept', async () => {
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    const first = await syncStatus();
    expect(first.status).toBe(200);
    const parts = (first.snap.jws as string).split('.');
    const forged = [parts[0], Buffer.from(JSON.stringify({ ...decodeJwt(first.snap.jws), statuses: {} })).toString('base64url'), parts[2]].join('.');
    expect((await sidecarPost('/internal/v1/status', { jws: forged })).status).toBe(409);

    // signed by a different key
    const other = await generateSigningKeyPair();
    const otherIssuer = createIssuerApp(openIssuerDb(':memory:'), other, { adminToken: ADMIN });
    const foreign = await (await otherIssuer.request('/v1/syncshield/status-snapshot')).json();
    const res = await sidecarPost('/internal/v1/status', { jws: foreign.jws });
    expect(res.status).toBe(409);
    expect((await res.json()).reason).toBe('bad_signature');
    expect((await login(cred)).status).toBe(200); // still served from the last good snapshot
  });

  it('rolling back to an older snapshot (which hides a revocation) is refused', async () => {
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    const before = await syncStatus();           // version N: active
    await adminPost(`/v1/syncshield/members/${m.actor_id}/revoke`, { reason: 'x' });
    await syncStatus();                           // version N+1: revoked
    const replay = await sidecarPost('/internal/v1/status', { jws: before.snap.jws });
    expect(replay.status).toBe(409);
    expect((await replay.json()).reason).toBe('rollback');
    expect((await login(cred)).status).toBe(403);
  });

  it('revoked stays revoked through an issuer outage and a sidecar restart', async () => {
    const dir = await import('node:fs').then((fs) => fs.mkdtempSync(require('node:os').tmpdir() + '/ss-auth-'));
    const dbPath = dir + '/auth.db';
    const db1 = openAuthDb(dbPath);
    const a1 = await buildAuth(db1);
    auth = a1;
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    await syncStatus();
    await adminPost(`/v1/syncshield/members/${m.actor_id}/revoke`, { reason: 'x' });
    await syncStatus();
    db1.close();
    // "restart": a new sidecar process on the same database file; the issuer is not contacted at all
    auth = await buildAuth(openAuthDb(dbPath));
    // (a new session key pair is fine: sessions are verified against whatever key this process publishes)
    const res = await login(cred);
    expect(res.status).toBe(403);
    expect((await res.json()).error.code).toBe('credential_revoked');
  });
});

describe('6. freshness policy (injected clock, no sleeping)', () => {
  it('no snapshot at all: no approval', async () => {
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    const res = await login(cred);
    expect(res.status).toBe(503);
    expect((await res.json()).error.code).toBe('no_status');
  });

  it('login is allowed while cached and refused once the snapshot is older than 10 minutes', async () => {
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    await syncStatus();
    clock += 30_000;
    expect((await (await login(cred)).json()).auth_mode).toBe('online');
    clock += 120_000;                               // 150 s
    expect((await (await login(cred)).json()).auth_mode).toBe('cached');
    clock += 500_000;                               // 650 s
    const res = await login(cred);
    expect(res.status).toBe(503);
    expect((await res.json()).error.code).toBe('status_stale_for_purpose');
  });

  it('session lifetime never exceeds the credential or the hard status bound', async () => {
    const m = await provision();
    const cred = await issueTo(m.actor_id);
    await syncStatus();
    clock += 590_000;                               // snapshot 590 s old
    const out = await (await login(cred)).json();
    const nowS = Math.floor(clock / 1000);
    const snapIssued = nowS - 590;
    expect(out.expires_at).toBeLessThanOrEqual(snapIssued + STATUS_POLICY.hard);
    expect(out.expires_at).toBeLessThanOrEqual(nowS + 300);
  });

  const cases = JSON.parse(readFileSync(new URL('./status-policy-cases.json', import.meta.url), 'utf8')).cases as Array<{
    name: string; status: 'a' | 'r' | null; age: number; purpose: 'login' | 'write' | 'privileged'; allow: boolean; mode?: string; reason?: string;
  }>;
  for (const c of cases) {
    it(`policy: ${c.name}`, () => {
      const snapshot = { issued_at: 1_000_000, statuses: c.status ? { cred_x: c.status } : {} } as never;
      const d = evaluateStatus({ snapshot, credentialId: 'cred_x', now: 1_000_000 + c.age, purpose: c.purpose });
      expect(d.allow).toBe(c.allow);
      if (d.allow) expect(d.mode).toBe(c.mode);
      else expect(d.reason).toBe(c.reason);
    });
  }
});

describe('7. library facts we rely on (checked, not assumed)', () => {
  it('the thumbprint-based kid format is stable for a JWK', async () => {
    const pair = await generateSigningKeyPair();
    const a = await calculateJwkThumbprint(pair.publicJwk as never);
    const b = await calculateJwkThumbprint(pair.publicJwk as never);
    expect(a).toBe(b);
  });
});

// ================================================================================================
describe('8. contract v1.2: prove ONE attribute (age_over_18 | member), the session records only that', () => {
  async function member(attributes?: { age_over_18?: boolean; member?: boolean }) {
    const res = await adminPost('/v1/syncshield/members', { name: 'Attr Member', role: 'officer', ...(attributes ? { attributes } : {}) });
    expect(res.status).toBe(201);
    return (await res.json()) as { actor_id: string; attributes: { age_over_18: boolean; member: boolean } };
  }
  async function challengeFor(claim?: string) {
    const res = await sidecarPost('/internal/v1/challenge', claim ? { claim } : {});
    return { res, ch: (await res.json()) as { challenge_id: string; nonce: string; audience: string; required_claims: string[] } };
  }
  async function session(cred: { holder: Holder; credential: string }, claim: string | undefined, shown?: string[]) {
    const { ch } = await challengeFor(claim);
    const p = await present(cred, ch, shown ?? ch.required_claims);
    const res = await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: p });
    return { res, body: await res.json(), presentation: p };
  }
  const tokenClaims = (token: string) => decodeJwt(token) as { clm?: string[]; dsc?: string[] };

  it('the issuer records both attributes (default yes) and signs them into the credential', async () => {
    const m = await member({ age_over_18: false });
    expect(m.attributes).toEqual({ age_over_18: false, member: true });
    const listed = await (await issuer.request('/v1/syncshield/members', { headers: { 'x-issuer-admin-token': ADMIN } })).json();
    expect(listed.members.find((x: { actor_id: string }) => x.actor_id === m.actor_id).attributes).toEqual({ age_over_18: false, member: true });
    const profile = await (await issuer.request('/v1/syncshield/profile')).json();
    expect(profile.attributes).toEqual(['age_over_18', 'member']);
  });

  it('an ordinary sign-in discloses neither attribute and the session proves none', async () => {
    const cred = await issueTo((await member()).actor_id);
    await syncStatus();
    const { res, body, presentation } = await session(cred, undefined);
    expect(res.status).toBe(200);
    expect(disclosedClaimNames(presentation).sort()).toEqual([...SYNCSHIELD_CLAIMS].sort());
    expect(body).toMatchObject({ claims: [], disclosed: [] });
    expect(tokenClaims(body.token)).toMatchObject({ clm: [], dsc: [] });
  });

  it('asking for age_over_18 discloses exactly that attribute, never member', async () => {
    const cred = await issueTo((await member()).actor_id);
    await syncStatus();
    const { res, body, presentation } = await session(cred, 'age_over_18');
    expect(res.status).toBe(200);
    const shown = disclosedClaimNames(presentation);
    expect(shown).toContain('age_over_18');
    expect(shown).not.toContain('member');
    expect(body).toMatchObject({ claims: ['age_over_18'], disclosed: ['age_over_18'] });
    expect(tokenClaims(body.token)).toMatchObject({ clm: ['age_over_18'], dsc: ['age_over_18'] });
  });

  it('a member who is not over 18 discloses the fact, and the session proves nothing', async () => {
    const cred = await issueTo((await member({ age_over_18: false })).actor_id);
    await syncStatus();
    const { res, body } = await session(cred, 'age_over_18');
    expect(res.status).toBe(200);
    expect(body).toMatchObject({ claims: [], disclosed: ['age_over_18'] });
  });

  it('withholding the asked attribute still signs in, without the claim', async () => {
    const cred = await issueTo((await member()).actor_id);
    await syncStatus();
    const { res, body } = await session(cred, 'age_over_18', [...SYNCSHIELD_CLAIMS]);
    expect(res.status).toBe(200);
    expect(body).toMatchObject({ claims: [], disclosed: [] });
  });

  it('disclosing an attribute that was NOT asked for is refused', async () => {
    const cred = await issueTo((await member()).actor_id);
    await syncStatus();
    const notAsked = await session(cred, undefined, [...SYNCSHIELD_CLAIMS, 'member']);
    expect(notAsked.res.status).toBe(401);
    expect(notAsked.body.error.code).toBe('wrong_claims');
    const other = await session(cred, 'age_over_18', [...SYNCSHIELD_CLAIMS, 'member']);
    expect(other.body.error.code).toBe('wrong_claims');
    const both = await session(cred, 'age_over_18', [...SYNCSHIELD_CLAIMS, 'age_over_18', 'member']);
    expect(both.body.error.code).toBe('wrong_claims');
  });

  it('flipping a disclosed attribute after signing is caught by the real verifier', async () => {
    const cred = await issueTo((await member({ age_over_18: false })).actor_id);
    await syncStatus();
    const { ch } = await challengeFor('age_over_18');
    const parts = (await present(cred, ch, ch.required_claims)).split('~');
    for (let i = 1; i < parts.length - 1; i++) {
      const d = JSON.parse(Buffer.from(parts[i], 'base64url').toString('utf8'));
      if (d[1] === 'age_over_18') { d[2] = true; parts[i] = Buffer.from(JSON.stringify(d)).toString('base64url'); }
    }
    const res = await sidecarPost('/internal/v1/session', { challenge_id: ch.challenge_id, presentation: parts.join('~') });
    expect(res.status).toBe(401);
    expect((await res.json()).token).toBeUndefined();
  });

  it('a challenge for an unknown attribute is a 400, and only one attribute per challenge', async () => {
    expect((await challengeFor('date_of_birth')).res.status).toBe(400);
    const res = await sidecarPost('/internal/v1/challenge', { claim: ['age_over_18', 'member'] });
    expect(res.status).toBe(400);
  });
});
