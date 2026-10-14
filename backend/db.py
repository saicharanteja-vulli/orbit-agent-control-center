import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def connect():
    path = Path(os.environ.get('CONTROL_DB', 'data/control.db'))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init():
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, task TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            result TEXT, error TEXT,
            agent TEXT NOT NULL DEFAULT 'planner', mode TEXT NOT NULL DEFAULT 'demo',
            model TEXT NOT NULL DEFAULT '', approval_required INTEGER NOT NULL DEFAULT 1,
            tokens INTEGER NOT NULL DEFAULT 0, parent_id TEXT REFERENCES runs(id)
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(id),
            kind TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
        CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, heartbeat TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT OR IGNORE INTO settings VALUES ('queue_paused','0');
        CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1);
        INSERT OR IGNORE INTO agents(id) VALUES ('planner'),('writer'),('analyst');
        ''')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','member','viewer')), active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),csrf TEXT NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS rate_limits (key TEXT NOT NULL,window INTEGER NOT NULL,count INTEGER NOT NULL,expires REAL NOT NULL,PRIMARY KEY(key,window));
        CREATE TABLE IF NOT EXISTS login_attempts (username TEXT NOT NULL,created REAL NOT NULL);
        ''')
        db.execute('BEGIN IMMEDIATE')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(runs)')}
        for name, definition in [('owner_id', 'TEXT REFERENCES users(id)'), ('enabled_tools', "TEXT NOT NULL DEFAULT '[]'"), ('checkpoint', 'TEXT')]:
            if name not in columns:
                db.execute(f'ALTER TABLE runs ADD COLUMN {name} {definition}')
        db.executescript("""
        CREATE TABLE IF NOT EXISTS actions (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
            sequence INTEGER NOT NULL, tool TEXT NOT NULL, arguments TEXT NOT NULL,
            digest TEXT NOT NULL, requires_approval INTEGER NOT NULL,
            status TEXT NOT NULL, result TEXT, error TEXT,
            created_at TEXT NOT NULL, decided_at TEXT, finished_at TEXT,
            UNIQUE(run_id,sequence)
        );
        CREATE INDEX IF NOT EXISTS actions_run ON actions(run_id,sequence);
        CREATE TRIGGER IF NOT EXISTS immutable_action_arguments
        BEFORE UPDATE OF tool, arguments, digest, requires_approval, run_id, sequence ON actions
        BEGIN SELECT RAISE(ABORT, 'Tool proposal is immutable'); END;
        """)


def event(db, run_id, kind, message):
    db.execute('INSERT INTO events(run_id,kind,message,created_at) VALUES(?,?,?,?)',
               (run_id, kind, message, now()))
