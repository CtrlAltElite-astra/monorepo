// Starts one regional auth sidecar (default ports 4101 / 4102 / 4103 for mum / pun / del).
//
//   AUTH_REGION             mum | pun | del                      (required)
//   AUTH_PORT               default 4101
//   AUTH_DATA_DIR           this sidecar's own files: auth.db, session-key.json
//   AUTH_SIDECAR_TOKEN      service credential FastAPI must send (required)
//   ISSUER_URL              default http://127.0.0.1:4001
//   ISSUER_PUBLIC_JWK_FILE  PINNED issuer public key {issuerId, publicJwk} (required, never fetched from the network)
//   STATUS_FILE             where the verified snapshot is mirrored for FastAPI
//   SESSION_KEY_DIR         directory of public session keys, one <region>.json per region (read by FastAPI)
//   STATUS_REFRESH_MS       default 15000
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { serve } from '@hono/node-server';
import { calculateJwkThumbprint } from 'jose';
import { generateSigningKeyPair, type Jwk } from '@true/credentials';
import { openAuthDb } from './db.ts';
import { createAuthApp, type SessionKey } from './app.ts';

const env = process.env;
const region = env.AUTH_REGION;
const sidecarToken = env.AUTH_SIDECAR_TOKEN;
if (!region || !sidecarToken || !env.ISSUER_PUBLIC_JWK_FILE) {
  console.error('AUTH_REGION, AUTH_SIDECAR_TOKEN and ISSUER_PUBLIC_JWK_FILE are required.');
  process.exit(1);
}
const port = Number(env.AUTH_PORT ?? 4101);
const dataDir = resolve(env.AUTH_DATA_DIR ?? `data/auth-${region}`);
const issuerUrl = (env.ISSUER_URL ?? 'http://127.0.0.1:4001').replace(/\/$/, '');
const refreshMs = Number(env.STATUS_REFRESH_MS ?? 15000);
mkdirSync(dataDir, { recursive: true });

// Pinned issuer key: loaded from a file the operator controls.
const pinned = JSON.parse(readFileSync(env.ISSUER_PUBLIC_JWK_FILE, 'utf8')) as { issuerId: string; publicJwk: Jwk };

// This region's session signing key: persisted (so sessions survive a restart), separate from every other key.
const keyFile = resolve(dataDir, 'session-key.json');
let pair: { publicJwk: Jwk; privateJwk: Jwk };
if (existsSync(keyFile)) {
  pair = JSON.parse(readFileSync(keyFile, 'utf8'));
} else {
  pair = await generateSigningKeyPair();
  writeFileSync(keyFile, JSON.stringify(pair, null, 2), { mode: 0o600 });
  try { chmodSync(keyFile, 0o600); } catch { /* best effort on Windows */ }
}
const kid = `${region}-${(await calculateJwkThumbprint(pair.publicJwk as never)).slice(0, 12)}`;
const sessionKey: SessionKey = { kid, privateJwk: pair.privateJwk, publicJwk: pair.publicJwk };

// Publish ONLY the public half, one file per region, for every FastAPI node to pin.
if (env.SESSION_KEY_DIR) {
  mkdirSync(env.SESSION_KEY_DIR, { recursive: true });
  writeFileSync(resolve(env.SESSION_KEY_DIR, `${region}.json`), JSON.stringify({ kid, region, jwk: pair.publicJwk }, null, 2));
}

const db = openAuthDb(resolve(dataDir, 'auth.db'));
const app = createAuthApp({
  db, region, issuerId: pinned.issuerId, issuerPublicJwk: pinned.publicJwk, sessionKey, sidecarToken,
  statusFile: env.STATUS_FILE ? resolve(env.STATUS_FILE) : undefined,
});

// Background status refresh: bounded timeouts, one retry, last good snapshot is kept on any failure.
async function refreshStatus() {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const res = await fetch(`${issuerUrl}/v1/syncshield/status-snapshot`, { signal: AbortSignal.timeout(2000) });
      if (!res.ok) throw new Error('http ' + res.status);
      const body = (await res.json()) as { jws: string };
      const out = await app.ingestSnapshot(body.jws);
      if (!out.accepted) console.warn(`[auth-${region}] snapshot rejected: ${out.reason}`);
      return;
    } catch {
      // keep the last good snapshot; try once more, then wait for the next tick
    }
  }
}
await refreshStatus();
setInterval(refreshStatus, refreshMs).unref();

serve({ fetch: app.fetch, port, hostname: '127.0.0.1' }, () =>
  console.log(`SyncShield auth sidecar [${region}] on http://127.0.0.1:${port} (session key ${kid})`));
