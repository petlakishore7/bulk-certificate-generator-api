import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    event_name   TEXT NOT NULL,
    issuer       TEXT NOT NULL,
    issue_date   TEXT NOT NULL,
    status       TEXT NOT NULL,
    total        INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS certificates (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    idx             INTEGER NOT NULL,
    recipient_name  TEXT,
    recipient_email TEXT,
    status          TEXT NOT NULL,
    error           TEXT,
    file_path       TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_certificates_job ON certificates (job_id, idx);
"""


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(db_path):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn(db_path):
    """Short-lived connection per unit of work (safe across request/worker threads)."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
