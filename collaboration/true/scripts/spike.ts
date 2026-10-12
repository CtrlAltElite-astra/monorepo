// M0 spike: proves the SD-JWT library works on this machine before we build on it.
// Run with: npm run spike
import {
  generateSigningKeyPair,
  generateHolderKey,
  issueCredential,
  createPresentation,
  verifyPresentation,
  disclosedClaimNames,
} from '@true/credentials';

const issuer = await generateSigningKeyPair();
const holder = await generateHolderKey();

const credential = await issueCredential({
  issuerId: 'issuer:spike',
  issuerPrivateJwk: issuer.privateJwk,
  holderPublicJwk: holder.publicJwk,
  type: 'StudentCredential',
  claims: { name: 'Test Student', dob: '2005-01-01', age_over_18: true },
  validDays: 1,
});

const presentation = await createPresentation({
  credential,
  holderPrivateKey: holder.privateKey,
  claims: ['age_over_18'],
  nonce: 'nonce-1',
  audience: 'verifier:spike',
});

const check = (text: string) =>
  verifyPresentation({ presentation: text, nonce: 'nonce-1', audience: 'verifier:spike', getIssuerPublicJwk: () => issuer.publicJwk });

const good = await check(presentation);
console.log(good.ok ? 'verified' : 'NOT verified: ' + good.message, good.ok ? good.claims : '');
console.log('disclosed claim names:', disclosedClaimNames(presentation));

// Tamper: flip one character inside the issuer-signed part.
const tampered = presentation.replace(/^(.{40})(.)/, (_, head, ch) => head + (ch === 'A' ? 'B' : 'A'));
const bad = await check(tampered);
console.log(bad.ok ? 'tampered -> ACCEPTED (bug!)' : 'tampered -> rejected (' + bad.code + ')');
