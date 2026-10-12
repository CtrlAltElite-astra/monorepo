// Regional auth database: challenges and the last good status snapshot. A separate file from the region's
// event store: Node never writes into the SQLite file the FastAPI region owns.
import { DatabaseSync } from 'node:sqlite';

export type ChallengeRow = {
  id: string; nonce: string; audience: string; created_at: number; expires_at: number;
  state: 'pending' | 'verifying' | 'completed' | 'denied' | 'expired'; result_code: string | null;
};

const SCHEMA = `
CREATE TABLE IF NOT EXISTS challenges (
  id TEXT PRIMARY KEY,
  nonce TEXT NOT NULL UNIQUE,
  audience TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending',
  result_code TEXT
);
CREATE TABLE IF NOT EXISTS status_snapshot (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  jws TEXT NOT NULL,
  ver INTEGER NOT NULL,
  issued_at INTEGER NOT NULL,
  fetched_at INTEGER NOT NULL
);
`;

export function openAuthDb(path: string): DatabaseSync {
  const db = new DatabaseSync(path);
  db.exec('PRAGMA journal_mode = WAL');
  db.exec(SCHEMA);
  return db;
}
