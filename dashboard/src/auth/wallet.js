// SyncShield browser wallet (convergence with TRUE).
//
// Stays on the SyncShield web origin so the holder key is available here: IndexedDB can hold a CryptoKey object
// created with extractable=false, which means this code (and any script on the page) can SIGN with it but can
// never read the private key. Adapted from the partner's apps/wallet/src/storage.ts, keyed per persona so the
// demo can run several shoppers and a supervisor in one browser through the same real flow.
//
// Credentials and holder keys live in IndexedDB. Session tokens do NOT: they are kept in memory only
// (see AuthContext.jsx), so they are not persisted, not in localStorage, not in URLs, not in cart events.
import { createHolderProof, createPresentation, generateHolderKey, readCredential } from '@true/credentials'

const DB_NAME = 'syncshield-wallet'

function openDb() {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open(DB_NAME, 1)
    open.onupgradeneeded = () => open.result.createObjectStore('kv')
    open.onsuccess = () => resolve(open.result)
    open.onerror = () => reject(open.error)
  })
}

async function get(key) {
  const db = await openDb()
  return new Promise((resolve, reject) => {
    const r = db.transaction('kv').objectStore('kv').get(key)
    r.onsuccess = () => resolve(r.result)
    r.onerror = () => reject(r.error)
  })
}

async function set(key, value) {
  const db = await openDb()
  return new Promise((resolve, reject) => {
    const tx = db.transaction('kv', 'readwrite')
    tx.objectStore('kv').put(value, key)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

async function del(key) {
  const db = await openDb()
  return new Promise((resolve, reject) => {
    const tx = db.transaction('kv', 'readwrite')
    tx.objectStore('kv').delete(key)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

export async function personas() {
  const list = await get('personas')
  return Array.isArray(list) ? list : []
}

async function addPersona(name) {
  const list = await personas()
  if (!list.includes(name)) await set('personas', [...list, name])
}

export async function holderKey(persona) {
  const existing = await get(`holder:${persona}`)
  if (existing) return existing
  const created = await generateHolderKey() // non-extractable
  await set(`holder:${persona}`, created)
  return created
}

export const storedCredential = (persona) => get(`cred:${persona}`)

export async function forgetPersona(persona) {
  await del(`cred:${persona}`)
  await del(`holder:${persona}`)
  await set('personas', (await personas()).filter((p) => p !== persona))
}

// What the wallet card may SHOW. This reads the credential without verifying it (the wallet received it
// straight from the issuer); nothing here is ever used to authorize anything.
export async function describeCredential(persona) {
  const c = await storedCredential(persona)
  if (!c) return null
  const claims = await readCredential(c.credential)
  return { credentialId: claims.credential_id, role: claims.syncshield_role, actorId: claims.actor_id, issuedAt: c.issuedAt }
}

const json = async (res) => {
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw Object.assign(new Error(body?.error?.message || `HTTP ${res.status}`), { status: res.status, body })
  return body
}

// Claim code -> credential. The wallet signs the issuer's nonce with its key to prove possession (holder proof);
// only then does the issuer sign a credential bound to that key.
export async function claimCredential({ persona, code, issuerUrl }) {
  const cleaned = String(code).trim().toUpperCase()
  const offer = await json(await fetch(`${issuerUrl}/v1/claim-codes/${encodeURIComponent(cleaned)}`))
  if (offer.profile !== 'syncshield') throw new Error('This code is not for a SyncShield membership')
  const holder = await holderKey(persona)
  const proof = await createHolderProof({
    holderPrivateKey: holder.privateKey, holderPublicJwk: holder.publicJwk, nonce: offer.nonce, audience: offer.issuerId,
  })
  const issued = await json(await fetch(`${issuerUrl}/v1/credentials`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ code: cleaned, proof }),
  }))
  await set(`cred:${persona}`, { credential: issued.credential, issuedAt: new Date().toISOString() })
  await addPersona(persona)
  return { credentialId: issued.credentialId, role: issued.role }
}

// Step 1 of login: ask a region for a one-time challenge. The region, not this page, chooses nonce and audience.
export async function requestChallenge(regionUrl) {
  return json(await fetch(`${regionUrl}/v1/auth/challenge`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' }))
}

// Step 2: reveal ONLY the claims the challenge asks for, bound to this nonce and this audience.
export async function completeLogin({ persona, regionUrl, challenge }) {
  const stored = await storedCredential(persona)
  if (!stored) throw new Error('No credential in this wallet')
  const holder = await holderKey(persona)
  const presentation = await createPresentation({
    credential: stored.credential, holderPrivateKey: holder.privateKey,
    claims: challenge.required_claims, nonce: challenge.nonce, audience: challenge.audience,
  })
  const res = await fetch(`${regionUrl}/v1/auth/session`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ challenge_id: challenge.challenge_id, presentation }),
  })
  return json(res)
}

// Parse "Name: CODE" lines (also "Name=CODE" or "Name CODE").
export function parseClaimBlock(text) {
  return String(text).split(/\r?\n/).map((l) => l.trim()).filter(Boolean).map((line) => {
    const m = line.match(/^(.+?)\s*[:=\s]\s*([A-Za-z0-9]{6,})$/)
    return m ? { persona: m[1].trim(), code: m[2].trim() } : null
  }).filter(Boolean)
}
