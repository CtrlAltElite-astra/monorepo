-- Contract v1.1 (additive): index on the client operation id, and a record of carried events.
-- The events.op_id COLUMN is added by store._ensure_op_id_column (SQLite cannot ADD COLUMN IF NOT EXISTS).
-- Applied once by store.init() inside a transaction (see schema_migrations); every statement is idempotent anyway.
CREATE INDEX IF NOT EXISTS ix_events_op ON events (op_id);
CREATE TABLE IF NOT EXISTS carried_events (event_id TEXT PRIMARY KEY, at REAL NOT NULL);
