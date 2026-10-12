// The browser wallet behind the identity panel: the partner's real SD-JWT code (createPresentation) with a holder
// key that never leaves this browser. Ported from dashboard/src/auth/wallet.js, keyed per shopper.
//
// The holder key is created with extractable=false and kept as a CryptoKey object in IndexedDB: this page can SIGN
// with it but can never read the private key. Session tokens are NOT stored here (client.ts returns them to memory).
import { createHolderProof, createPresentation, generateHolderKey, readCredential } from '@true/credentials'
import type { PresentRequest, Wallet } from './client'

const DB_NAME = 'syncshield-wallet'
const CREDENTIAL_META = ['iss', 'iat', 'exp', 'vct', 'cnf', 'status']   // technical fields, not attributes
type Holder = Awaited<ReturnType<typeof generateHolderKey>>
type Stored = { credential: string; issuedAt: string }

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open(DB_NAME, 1)
    open.onupgradeneeded = () => open.result.createObjectStore('kv')
    open.onsuccess = () => resolve(open.result)
    open.onerror = () => reject(open.error)
  })
}

async function get<T>(key: string): Promise<T | undefined> {
  const db = await openDb()
  return new Promise((resolve, reject) => {
    const r = db.transaction('kv').objectStore('kv').get(key)
    r.onsuccess = () => resolve(r.result as T | undefined)
    r.onerror = () => reject(r.error)
  })
}

async function set(key: string, value: unknown): Promise<void> {
  const db = await openDb()
  return new Promise((resolve, reject) => {
    const tx = db.transaction('kv', 'readwrite')
    tx.objectStore('kv').put(value, key)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

async function holderKey(shopper: string): Promise<Holder> {
  const existing = await get<Holder>(`holder:${shopper}`)
  if (existing) return existing
  const created = await generateHolderKey()   // non-extractable
  await set(`holder:${shopper}`, created)
  return created
}

export type BrowserWallet = Wallet & {
  hasCredential(): Promise<boolean>
  claim(code: string, issuerUrl: string): Promise<{ credentialId: string; role: string }>
}

export function browserWallet(shopper: string): BrowserWallet {
  const stored = () => get<Stored>(`cred:${shopper}`)
  return {
    async hasCredential() { return Boolean(await stored()) },

    // One-time claim code -> credential bound to this browser's holder key (the issuer checks a holder proof first).
    async claim(code, issuerUrl) {
      const cleaned = String(code).trim().toUpperCase()
      const offerRes = await fetch(`${issuerUrl}/v1/claim-codes/${encodeURIComponent(cleaned)}`)
      const offer = await offerRes.json()
      if (!offerRes.ok) throw new Error(offer?.error?.message || `HTTP ${offerRes.status}`)
      if (offer.profile !== 'syncshield') throw new Error('This code is not for a SyncShield membership')
      const holder = await holderKey(shopper)
      const proof = await createHolderProof({
        holderPrivateKey: holder.privateKey, holderPublicJwk: holder.publicJwk, nonce: offer.nonce, audience: offer.issuerId,
      })
      const res = await fetch(`${issuerUrl}/v1/credentials`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ code: cleaned, proof }),
      })
      const issued = await res.json()
      if (!res.ok) throw new Error(issued?.error?.message || `HTTP ${res.status}`)
      await set(`cred:${shopper}`, { credential: issued.credential, issuedAt: new Date().toISOString() })
      return { credentialId: issued.credentialId, role: issued.role }
    },

    // Display only: read without verifying (the wallet received it straight from the issuer). Never used to authorize.
    async holds() {
      const c = await stored()
      if (!c) return null
      const all = await readCredential(c.credential)
      return Object.fromEntries(Object.entries(all).filter(([k]) => !CREDENTIAL_META.includes(k)))
    },

    async present(req: PresentRequest) {
      const c = await stored()
      if (!c) throw new Error('No credential in this wallet')
      const holder = await holderKey(shopper)
      return createPresentation({
        credential: c.credential, holderPrivateKey: holder.privateKey, claims: req.claims, nonce: req.nonce, audience: req.audience,
      })
    },
  }
}
