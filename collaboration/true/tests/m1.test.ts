// M1 tests: one per promise we make in the demo.
// Both APIs run in-process with in-memory databases. The "wallet" here is the same
// packages/credentials code the real wallet uses in the browser.
import { beforeAll, describe, expect, it } from 'vitest';
import { ISSUER_ID } from '@true/shared';
import {
  createHolderProof,
  createPresentation,
  disclosedClaimNames,
  generateHolderKey,
  generateSigningKeyPair,
} from '@true/credentials';
import { openIssuerDb } from '../services/issuer-api/src/db.ts';
import { createIssuerApp } from '../services/issuer-api/src/app.ts';
import { openVerifierDb } from '../services/verifier-api/src/db.ts';
import { createVerifierApp } from '../services/verifier-api/src/app.ts';

type App = { request: (path: string, init?: RequestInit) => Response | Promise<Response> };
type Holder = Awaited<ReturnType<typeof generateHolderKey>>;

let issuer: App;
let verifier: App;
let issuerDb: ReturnType<typeof openIssuerDb>;

const post = (app: App, path: string, body: unknown) =>
  app.request(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });

// Wallet step: use a claim code to get a credential.
async function claim(code: string, holder: Holder) {
  const offer = await (await issuer.request(`/v1/claim-codes/${code}`)).json();
  const proof = await createHolderProof({
    holderPrivateKey: holder.privateKey,
    holderPublicJwk: holder.publicJwk,
    nonce: offer.nonce ?? 'no-nonce',
    audience: ISSUER_ID,
  });
  return post(issuer, '/v1/credentials', { code, proof });
}

// Admin + wallet: issue a credential to student 1 (Aarav, an adult) and return it with its holder key.
async function issueToStudent(studentId = 1) {
  const holder = await generateHolderKey();
  const { code } = await (await post(issuer, '/v1/claim-codes', { studentId })).json();
  const { credential } = await (await claim(code, holder)).json();
  return { holder, credential: credential as string };
}

// Verifier step: open a request and fetch what the wallet would see.
async function openRequest(verifierId: string) {
  const { requestId } = await (await post(verifier, '/v1/requests', { verifierId })).json();
  const object = await (await verifier.request(`/v1/requests/${requestId}/object`)).json();
  return { requestId: requestId as string, object };
}

const respond = (requestId: string, presentation: string) =>
  post(verifier, `/v1/requests/${requestId}/response`, { presentation });
const stateOf = async (requestId: string) => (await verifier.request(`/v1/requests/${requestId}`)).json();

beforeAll(async () => {
  const keys = await generateSigningKeyPair();
  issuerDb = openIssuerDb(':memory:');
  issuer = createIssuerApp(issuerDb, keys);
  // The verifier is given ONLY the issuer's public key (what the registry holds). It has no handle on the issuer app.
  verifier = createVerifierApp(openVerifierDb(':memory:'), (id) => (id === ISSUER_ID ? keys.publicJwk : null));
});

describe('happy path', () => {
  it('issue -> present selected claim -> verified', async () => {
    const { holder, credential } = await issueToStudent();
    const { requestId, object } = await openRequest('verifier:nova-arena');
    const presentation = await createPresentation({
      credential, holderPrivateKey: holder.privateKey, claims: object.claims, nonce: object.nonce, audience: object.verifierId,
    });
    expect((await respond(requestId, presentation)).status).toBe(200);
    const final = await stateOf(requestId);
    expect(final.state).toBe('verified');
    expect(final.result.claims).toEqual({ age_over_18: true });
  });
});

describe('policy', () => {
  it('a genuine credential that answers No is denied, not verified', async () => {
    const { holder, credential } = await issueToStudent(3); // Rohan is under 18
    const { requestId, object } = await openRequest('verifier:nova-arena');
    const presentation = await createPresentation({
      credential, holderPrivateKey: holder.privateKey, claims: object.claims, nonce: object.nonce, audience: object.verifierId,
    });
    await respond(requestId, presentation);
    const final = await stateOf(requestId);
    expect(final.state).toBe('denied');
    expect(final.result.code).toBe('requirement_not_met');
  });
});

describe('selective disclosure', () => {
  it('hidden claims are absent from the presentation', async () => {
    const { holder, credential } = await issueToStudent();
    const presentation = await createPresentation({
      credential, holderPrivateKey: holder.privateKey, claims: ['age_over_18'], nonce: 'n', audience: 'verifier:nova-arena',
    });
    expect(disclosedClaimNames(presentation)).toEqual(['age_over_18']);
    // Decode every part of the presentation and search the plain text for the hidden values.
    const plainText = presentation
      .split(/[~.]/)
      .map((part) => Buffer.from(part, 'base64url').toString('utf8'))
      .join(' ');
    for (const hidden of ['Aarav', 'AIT2023001', '2005-03-14', 'Lake View', 'Computer Engineering']) {
      expect(plainText).not.toContain(hidden);
    }
  });

  it('City Library gets name, photo and PNR but not date of birth or address', async () => {
    const { holder, credential } = await issueToStudent();
    const { requestId, object } = await openRequest('verifier:city-library');
    const presentation = await createPresentation({
      credential, holderPrivateKey: holder.privateKey, claims: object.claims, nonce: object.nonce, audience: object.verifierId,
    });
    await respond(requestId, presentation);
    const final = await stateOf(requestId);
    expect(final.state).toBe('verified');
    expect(Object.keys(final.result.claims).sort()).toEqual(['name', 'photo', 'pnr']);
  });
});

describe('attacks', () => {
  it('tamper: a changed disclosure value is not accepted', async () => {
    const { holder, credential } = await issueToStudent(3); // Rohan is under 18, so age_over_18 is false
    const { requestId, object } = await openRequest('verifier:nova-arena');
    const presentation = await createPresentation({
      credential, holderPrivateKey: holder.privateKey, claims: ['age_over_18'], nonce: object.nonce, audience: object.verifierId,
    });
    // Rewrite the disclosure [salt, "age_over_18", false] to say true.
    const parts = presentation.split('~');
    const disclosure = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));
    expect(disclosure[2]).toBe(false);
    disclosure[2] = true;
    parts[1] = Buffer.from(JSON.stringify(disclosure)).toString('base64url');
    await respond(requestId, parts.join('~'));
    expect((await stateOf(requestId)).state).toBe('denied');
  });

  it('tamper: a credential signed by a different key is rejected', async () => {
    // A forger with their own key signs a look-alike credential under the real issuer's name.
    const forgerKeys = await generateSigningKeyPair();
    const forger = createIssuerApp(openIssuerDb(':memory:'), forgerKeys);
    const holder = await generateHolderKey();
    const { code } = await (await post(forger, '/v1/claim-codes', { studentId: 1 })).json();
    const offer = await (await forger.request(`/v1/claim-codes/${code}`)).json();
    const proof = await createHolderProof({ holderPrivateKey: holder.privateKey, holderPublicJwk: holder.publicJwk, nonce: offer.nonce, audience: ISSUER_ID });
    const { credential } = await (await post(forger, '/v1/credentials', { code, proof })).json();

    const { requestId, object } = await openRequest('verifier:nova-arena');
    const presentation = await createPresentation({
      credential, holderPrivateKey: holder.privateKey, claims: object.claims, nonce: object.nonce, audience: object.verifierId,
    });
    await respond(requestId, presentation);
    const final = await stateOf(requestId);
    expect(final.state).toBe('denied');
    expect(final.result.code).toBe('bad_signature');
  });

  it('replay: the same presentation cannot be used twice', async () => {
    const { holder, credential } = await issueToStudent();
    const first = await openRequest('verifier:nova-arena');
    const presentation = await createPresentation({
      credential, holderPrivateKey: holder.privateKey, claims: ['age_over_18'], nonce: first.object.nonce, audience: first.object.verifierId,
    });
    expect((await respond(first.requestId, presentation)).status).toBe(200);

    // Replay against the same request: already answered.
    const again = await respond(first.requestId, presentation);
    expect(again.status).toBe(409);

    // Replay against a fresh request: the nonce inside the old proof does not match.
    const second = await openRequest('verifier:nova-arena');
    await respond(second.requestId, presentation);
    const final = await stateOf(second.requestId);
    expect(final.state).toBe('denied');
    expect(final.result.code).toBe('nonce_mismatch');
  });

  it('wrong audience: a proof made for one verifier fails at another', async () => {
    const { holder, credential } = await issueToStudent();
    const { requestId, object } = await openRequest('verifier:nova-arena');
    // Right nonce, but the holder signed it for the Campus Store.
    const presentation = await createPresentation({
      credential, holderPrivateKey: holder.privateKey, claims: ['age_over_18'], nonce: object.nonce, audience: 'verifier:campus-store',
    });
    await respond(requestId, presentation);
    const final = await stateOf(requestId);
    expect(final.state).toBe('denied');
    expect(final.result.code).toBe('wrong_audience');
  });

  it('stolen credential: presenting without the holder key fails', async () => {
    const { credential } = await issueToStudent();
    const thief = await generateHolderKey();
    const { requestId, object } = await openRequest('verifier:nova-arena');
    const presentation = await createPresentation({
      credential, holderPrivateKey: thief.privateKey, claims: ['age_over_18'], nonce: object.nonce, audience: object.verifierId,
    });
    await respond(requestId, presentation);
    expect((await stateOf(requestId)).state).toBe('denied');
  });
});

describe('issuer database is the gate', () => {
  it('unknown student: no claim code and no credential', async () => {
    const response = await post(issuer, '/v1/claim-codes', { studentId: 9999 });
    expect(response.status).toBe(404);
    expect((await response.json()).error.code).toBe('unknown_student');

    // A made-up code gets nothing either.
    const madeUp = await claim('ZZZZZZZZ', await generateHolderKey());
    expect(madeUp.status).toBe(400);
    expect((await madeUp.json()).error.code).toBe('unknown_code');
  });

  it('reused claim code: second use is refused', async () => {
    const { code } = await (await post(issuer, '/v1/claim-codes', { studentId: 2 })).json();
    expect((await claim(code, await generateHolderKey())).status).toBe(201);
    const second = await claim(code, await generateHolderKey());
    expect(second.status).toBe(400);
    expect((await second.json()).error.code).toBe('code_used');
  });

  it('expired claim code is refused', async () => {
    const { code } = await (await post(issuer, '/v1/claim-codes', { studentId: 2 })).json();
    issuerDb.prepare('UPDATE claim_codes SET expires_at = ? WHERE code = ?').run('2000-01-01T00:00:00.000Z', code);
    const response = await claim(code, await generateHolderKey());
    expect((await response.json()).error.code).toBe('code_expired');
  });

  it('revoked student cannot be issued a credential', async () => {
    const { code } = await (await post(issuer, '/v1/claim-codes', { studentId: 4 })).json();
    issuerDb.prepare("UPDATE students SET status = 'revoked' WHERE id = 4").run();
    const response = await claim(code, await generateHolderKey());
    expect(response.status).toBe(409);
    expect((await response.json()).error.code).toBe('student_not_active');
    expect((await post(issuer, '/v1/claim-codes', { studentId: 4 })).status).toBe(409);
  });

  // Goes green in M2, when the status list exists. Kept here so it is not forgotten.
  it.todo("revoked student's credential fails verification");
});
