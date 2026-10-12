-- Contract v1.1 (additive): client operation id on events, and a record of carried events.
-- Applied once by store.init() inside a transaction (see schema_migrations).
ALTER TABLE events ADD COLUMN op_id TEXT;
CREATE INDEX IF NOT EXISTS ix_events_op ON events (op_id);
CREATE TABLE IF NOT EXISTS carried_events (event_id TEXT PRIMARY KEY, at REAL NOT NULL);
