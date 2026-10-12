// Verifier database: this verifier's own config, requests and results.
// It is a separate file from the issuer database and holds no student records.
import { DatabaseSync } from 'node:sqlite';

export type VerifierRow = {
  id: string;
  name: string;
  purpose: string;
  allowed_claims_json: string; // JSON array of claim names this verifier may ask for
};

export type RequestRow = {
  id: string;
  verifier_id: string;
  claims_json: string;
  nonce: string;
  state: 'pending' | 'verified' | 'denied' | 'expired';
  created_at: string;
  expires_at: string;
  result_json: string | null;
};

const SCHEMA = `
CREATE TABLE IF NOT EXISTS verifiers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  purpose TEXT NOT NULL,
  allowed_claims_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
  id TEXT PRIMARY KEY,
  verifier_id TEXT NOT NULL REFERENCES verifiers(id),
  claims_json TEXT NOT NULL,
  nonce TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  result_json TEXT
);
`;

// Demo verifiers. One process hosts all three to keep the demo to one command;
// in real life each would run its own copy of this service.
const DEMO_VERIFIERS: [string, string, string, string[]][] = [
  ['verifier:city-library', 'City Library', 'Issue a library membership card', ['name', 'photo', 'pnr']],
  ['verifier:nova-arena', 'Nova Arena', 'Entry to an 18+ event', ['age_over_18']],
  ['verifier:campus-store', 'Campus Store', 'Student discount', ['is_student']],
];

// Opens (and on first run creates and seeds) the verifier database. Use ':memory:' in tests.
export function openVerifierDb(path: string): DatabaseSync {
  const db = new DatabaseSync(path);
  db.exec(SCHEMA);
  const count = db.prepare('SELECT COUNT(*) AS n FROM verifiers').get() as { n: number };
  if (count.n === 0) {
    const insert = db.prepare('INSERT INTO verifiers (id, name, purpose, allowed_claims_json) VALUES (?, ?, ?, ?)');
    for (const [id, name, purpose, claims] of DEMO_VERIFIERS) insert.run(id, name, purpose, JSON.stringify(claims));
  }
  return db;
}
