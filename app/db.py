"""SQLite storage. One connection per call; writes use BEGIN IMMEDIATE so
balance checks and postings are serialized (no double-spend)."""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("MVULE_DB", os.path.join(os.path.dirname(__file__), "..", "data", "mvule.db"))

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  phone TEXT UNIQUE,
  full_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('user','system')),
  code TEXT,                       -- system account code, e.g. FAUCET
  name TEXT NOT NULL,
  currency TEXT NOT NULL,
  account_number TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  UNIQUE (user_id, currency),
  UNIQUE (code, currency)
);
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ref TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  type TEXT NOT NULL CHECK (type IN ('topup','transfer','payout')),
  status TEXT NOT NULL CHECK (status IN ('pending','completed','failed')),
  currency TEXT NOT NULL,
  amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
  from_account_id INTEGER REFERENCES accounts(id),
  to_account_id INTEGER REFERENCES accounts(id),
  rail TEXT,
  counterparty TEXT,               -- JSON
  memo TEXT,
  idempotency_key TEXT,
  failure_reason TEXT,
  settle_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (user_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS postings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  txn_id INTEGER NOT NULL REFERENCES transactions(id),
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  amount_minor INTEGER NOT NULL,   -- signed: + credit, - debit
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_postings_account ON postings(account_id);
CREATE INDEX IF NOT EXISTS ix_txn_status ON transactions(status, settle_at);
CREATE TABLE IF NOT EXISTS payees (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  label TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('internal','bank','mobile')),
  details TEXT NOT NULL,           -- JSON
  created_at TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with closing() as c:
        c.executescript(SCHEMA)


@contextmanager
def closing():
    c = connect()
    try:
        yield c
    finally:
        c.close()


@contextmanager
def write_tx():
    """Exclusive write transaction; commits on success, rolls back on error."""
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise
    finally:
        c.close()
