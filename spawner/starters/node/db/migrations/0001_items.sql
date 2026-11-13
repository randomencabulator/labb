-- The example's table, for backend/items.js. A migration that ran is never run again: to change a
-- table, or add one, write the next file (0002_….sql). Written so both SQLite and Postgres take it:
-- TEXT ids made by the server, BOOLEAN, TIMESTAMP.
CREATE TABLE items (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  done BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
