// Wallet storage. Everything stays in this browser (IndexedDB); nothing is sent to a server.
// IndexedDB is used instead of localStorage because it can store the non-exportable private key object.
import { generateHolderKey, type Jwk } from '@true/credentials';

export type HolderKey = { privateKey: CryptoKey; publicJwk: Jwk };
export type StoredCredential = { id: string; credential: string; issuerName: string; addedAt: string };
export type HistoryEntry = { at: string; verifierName: string; claims: string[]; decision: 'shared' | 'declined'; outcome: string };

// One simple key-value store.
function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open('true-wallet', 1);
    open.onupgradeneeded = () => open.result.createObjectStore('kv');
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
}

async function get<T>(key: string): Promise<T | undefined> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const request = db.transaction('kv').objectStore('kv').get(key);
    request.onsuccess = () => resolve(request.result as T | undefined);
    request.onerror = () => reject(request.error);
  });
}

async function set(key: string, value: unknown): Promise<void> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('kv', 'readwrite');
    tx.objectStore('kv').put(value, key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

// The wallet's key pair. Created on this device the first time it is needed, then reused.
export async function getHolderKey(): Promise<HolderKey> {
  const existing = await get<HolderKey>('holderKey');
  if (existing) return existing;
  const created = await generateHolderKey();
  await set('holderKey', created);
  return created;
}

export const getCredentials = async () => (await get<StoredCredential[]>('credentials')) ?? [];

export async function addCredential(credential: string, issuerName: string): Promise<void> {
  const list = await getCredentials();
  list.push({ id: crypto.randomUUID(), credential, issuerName, addedAt: new Date().toISOString() });
  await set('credentials', list);
}

export async function removeCredential(id: string): Promise<void> {
  await set('credentials', (await getCredentials()).filter((c) => c.id !== id));
}

// "Who asked what, when": kept only on the device.
export const getHistory = async () => (await get<HistoryEntry[]>('history')) ?? [];

export async function addHistory(entry: HistoryEntry): Promise<void> {
  await set('history', [entry, ...(await getHistory())]);
}
