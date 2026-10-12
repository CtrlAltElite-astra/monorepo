// SyncShield wallet CLI (SYNCSHIELD ADAPTATION). A headless wallet built on the partner's real credential
// functions (createHolderProof / createPresentation). The browser wallet in dashboard/ uses the same package;
// this one stores its holder key as a JWK file so tests and demo scripts can act as a user across processes.
//
//   node scripts/wallet-cli.mjs claim   --wallet w.json --issuer http://127.0.0.1:4001 --code ABCD2345
//   node scripts/wallet-cli.mjs login   --wallet w.json --region http://127.0.0.1:8001 [--tamper role]
//   node scripts/wallet-cli.mjs present --wallet w.json --nonce N --audience A [--claims a,b,c]
//
// Output is one JSON document on stdout. A holder private key is never printed.
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { createHolderProof, createPresentation, generateSigningKeyPair } from '@true/credentials';
import { ISSUER_ID, SYNCSHIELD_CLAIMS } from '@true/shared';

const args = Object.fromEntries(
  process.argv.slice(3).reduce((acc, a, i, all) => (a.startsWith('--') ? [...acc, [a.slice(2), all[i + 1]?.startsWith('--') || all[i + 1] === undefined ? true : all[i + 1]]] : acc), []),
);
const command = process.argv[2];
const out = (o) => console.log(JSON.stringify(o));
const fail = (o, code = 1) => { out({ ok: false, ...o }); process.exit(code); };

async function loadWallet(path) {
  if (existsSync(path)) return JSON.parse(readFileSync(path, 'utf8'));
  // a fresh holder key (ES256). Test wallet only: the browser wallet keeps its key non-extractable.
  const pair = await generateSigningKeyPair();
  const wallet = { privateJwk: pair.privateJwk, publicJwk: { kty: pair.publicJwk.kty, crv: pair.publicJwk.crv, x: pair.publicJwk.x, y: pair.publicJwk.y }, credentials: [] };
  writeFileSync(path, JSON.stringify(wallet, null, 2));
  return wallet;
}
async function signingKey(wallet) {
  return crypto.subtle.importKey('jwk', wallet.privateJwk, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
}
const post = (url, body) => fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });

async function present(wallet, { nonce, audience, claims, credential, tamper }) {
  let p = await createPresentation({
    credential, holderPrivateKey: await signingKey(wallet), claims, nonce, audience,
  });
  if (tamper) {
    // change one disclosed value after signing, to prove the verifier notices
    const parts = p.split('~');
    for (let i = 1; i < parts.length - 1; i++) {
      const d = JSON.parse(Buffer.from(parts[i], 'base64url').toString('utf8'));
      if (d[1] === tamper) { d[2] = d[2] === true ? false : tamper === 'syncshield_role' ? 'admin' : 'x'; parts[i] = Buffer.from(JSON.stringify(d)).toString('base64url'); }
    }
    p = parts.join('~');
  }
  return p;
}

const walletPath = args.wallet;
if (!walletPath) fail({ error: 'missing --wallet' });
const wallet = await loadWallet(walletPath);

if (command === 'claim') {
  const issuer = String(args.issuer).replace(/\/$/, '');
  const code = String(args.code).toUpperCase();
  const offerRes = await fetch(`${issuer}/v1/claim-codes/${code}`);
  const offer = await offerRes.json();
  if (!offerRes.ok) fail({ stage: 'offer', status: offerRes.status, ...offer });
  const proof = await createHolderProof({ holderPrivateKey: await signingKey(wallet), holderPublicJwk: wallet.publicJwk, nonce: offer.nonce, audience: offer.issuerId ?? ISSUER_ID });
  const res = await post(`${issuer}/v1/credentials`, { code, proof });
  const body = await res.json();
  if (!res.ok) fail({ stage: 'claim', status: res.status, ...body });
  wallet.credentials.push({ credential: body.credential, credentialId: body.credentialId, role: body.role, issuer: body.issuerId });
  writeFileSync(walletPath, JSON.stringify(wallet, null, 2));
  out({ ok: true, credentialId: body.credentialId, role: body.role, expiresAt: body.expiresAt });
} else if (command === 'login') {
  const region = String(args.region).replace(/\/$/, '');
  const cred = wallet.credentials.at(-1);
  if (!cred) fail({ error: 'wallet has no credential' });
  const chRes = await post(`${region}/v1/auth/challenge`, {});
  const ch = await chRes.json();
  if (!chRes.ok) fail({ stage: 'challenge', status: chRes.status, ...ch });
  const claims = args.claims ? String(args.claims).split(',') : ch.required_claims ?? [...SYNCSHIELD_CLAIMS];
  const presentation = await present(wallet, { nonce: ch.nonce, audience: ch.audience, claims, credential: cred.credential, tamper: args.tamper });
  const res = await post(`${region}/v1/auth/session`, { challenge_id: ch.challenge_id, presentation });
  const body = await res.json();
  out({ ok: res.ok, status: res.status, ...body, ...(args.keep ? { presentation } : {}), challenge_id: ch.challenge_id });
} else if (command === 'present') {
  const cred = wallet.credentials.at(-1);
  if (!cred) fail({ error: 'wallet has no credential' });
  const claims = args.claims ? String(args.claims).split(',') : [...SYNCSHIELD_CLAIMS];
  out({ ok: true, presentation: await present(wallet, { nonce: args.nonce, audience: args.audience, claims, credential: cred.credential, tamper: args.tamper }) });
} else {
  fail({ error: `unknown command ${command}` });
}
