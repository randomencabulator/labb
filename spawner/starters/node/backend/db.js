// The app's database, for the rest of backend/, in three calls (each a promise):
//
//   const rows = await db.query("SELECT * FROM items WHERE done = ? ORDER BY created_at", [false]); // an array of objects
//   const row = await db.one("SELECT * FROM items WHERE id = ?", [id]);                              // an object, or null
//   const count = await db.execute("DELETE FROM items WHERE id = ?", [id]);                          // the rows it changed
//
// Values always go in as `?`, never into the SQL's text. It's Postgres when the app has a database
// (DATABASE_URL, which its owner turns on on its page), else SQLite in /data/app.db (kept across
// builds and restarts), so write SQL both understand: TEXT ids made in code (crypto.randomUUID()),
// BOOLEAN, TIMESTAMP, and `?` for values.
//
// The tables come from db/migrations/*.sql, each run once, in name order: for Postgres by labb at each
// build, for SQLite by migrate() as the server starts. To change a table, write the next migration
// (0002_….sql): one that ran is never run again.
const fs = require("node:fs");
const path = require("node:path");

const URL = process.env.DATABASE_URL || "";
const FILE = path.join(process.env.DATA_DIR || "/data", "app.db");
const MIGRATIONS = path.join(__dirname, "db", "migrations");

let pool, sqlite;

function postgres() {
  if (!pool) {
    const { Pool } = require("pg");
    pool = new Pool({ connectionString: URL, max: 5 });
  }
  return pool;
}

function local() {
  if (!sqlite) {
    const { DatabaseSync } = require("node:sqlite");
    fs.mkdirSync(path.dirname(FILE), { recursive: true });
    sqlite = new DatabaseSync(FILE, { timeout: 10_000 });
    sqlite.exec("PRAGMA foreign_keys = ON");
  }
  return sqlite;
}

// `?` as Postgres's $1, $2… (but not inside quotes)
function numbered(sql) {
  let count = 0;
  return sql.replace(/'(?:[^']|'')*'|\?/g, found => (found === "?" ? `$${++count}` : found));
}

// What SQLite can take: booleans as 1 and 0, times as text
function bound(values) {
  return values.map(value =>
    value === true ? 1 : value === false ? 0 : value === undefined ? null
      : value instanceof Date ? value.toISOString().replace("T", " ").slice(0, 19) : value);
}

// SQLite keeps booleans and times as numbers and text: given back as Postgres gives them
function typed(statement, rows) {
  const kinds = new Map(statement.columns().map(column => [column.name, (column.type || "").toUpperCase()]));
  for (const row of rows) {
    for (const [name, kind] of kinds) {
      if (row[name] === null || row[name] === undefined) continue;
      if (kind === "BOOLEAN") row[name] = Boolean(row[name]);
      else if (kind === "TIMESTAMP") row[name] = new Date(String(row[name]).replace(" ", "T") + "Z");
    }
  }
  return rows;
}

async function query(sql, values = []) {
  if (URL) return (await postgres().query(numbered(sql), values)).rows;
  const statement = local().prepare(sql);
  if (statement.columns().length === 0) {
    statement.run(...bound(values));
    return [];
  }
  return typed(statement, statement.all(...bound(values)).map(row => ({ ...row })));
}

async function one(sql, values = []) {
  return (await query(sql, values))[0] ?? null;
}

async function execute(sql, values = []) {
  if (URL) return (await postgres().query(numbered(sql), values)).rowCount;
  return Number(local().prepare(sql).run(...bound(values)).changes);
}

// SQLite's tables: the migrations it hasn't had yet (Postgres has had them, from labb)
async function migrate() {
  if (URL || !fs.existsSync(MIGRATIONS)) return;
  const database = local();
  database.exec("CREATE TABLE IF NOT EXISTS labb_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)");
  const done = new Set(database.prepare("SELECT name FROM labb_migrations").all().map(row => row.name));
  for (const name of fs.readdirSync(MIGRATIONS).filter(file => file.endsWith(".sql")).sort()) {
    if (done.has(name)) continue;
    // All of it or none of it
    database.exec("BEGIN");
    try {
      database.exec(fs.readFileSync(path.join(MIGRATIONS, name), "utf8"));
      database.prepare("INSERT INTO labb_migrations (name) VALUES (?)").run(name);
      database.exec("COMMIT");
    } catch (error) {
      database.exec("ROLLBACK");
      throw new Error(`The migration ${name} failed: ${error.message}`);
    }
  }
}

module.exports = { query, one, execute, migrate };
