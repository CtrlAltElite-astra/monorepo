// Starts the verifier API on port 4002.
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { serve } from '@hono/node-server';
import { openVerifierDb } from './db.ts';
import { createVerifierApp } from './app.ts';

// Private files of the verifier (its own database). Git-ignored.
const DATA_DIR = new URL('../data/', import.meta.url);
// Public registry file. Stand-in for the chain until M2: it holds ONLY public keys.
const REGISTRY_FILE = new URL('../../../data/registry.json', import.meta.url);

mkdirSync(DATA_DIR, { recursive: true });

// Read the registry each time, so a key published after we started is still found.
function getIssuerPublicJwk(issuerId: string) {
  if (!existsSync(REGISTRY_FILE)) return null;
  const registry = JSON.parse(readFileSync(REGISTRY_FILE, 'utf8'));
  return registry.issuers?.[issuerId]?.publicJwk ?? null;
}

const db = openVerifierDb(fileURLToPath(new URL('verifier.db', DATA_DIR)));
const app = createVerifierApp(db, getIssuerPublicJwk);

serve({ fetch: app.fetch, port: 4002 }, () => console.log('Verifier API listening on http://localhost:4002'));
