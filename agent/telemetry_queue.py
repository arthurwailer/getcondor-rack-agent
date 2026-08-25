"""
Durable store-and-forward queue for telemetry samples.

Standard industry pattern (used in SCADA, satellite telemetry, IoT edge
agents) for unreliable-network environments: NEVER write directly to
the network. Always persist locally first, then drain the local queue
to the network in a separate process/thread, retrying until the
network confirms receipt.

This means a sample is never lost just because Starlink drops for a
few minutes -- it sits in this queue (on disk, durable across process
restarts) until it can be sent, in order, oldest first.

Bounded by TELEMETRY_QUEUE_MAX_ROWS: if the rack loses connectivity for
a very long time, we cap disk usage by dropping the oldest unsent rows
once the cap is hit, rather than filling the disk. Media (photos/videos)
takes priority over old telemetry samples for disk space.
"""

from __future__ import annotations
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.getenv("TELEMETRY_QUEUE_DB_PATH", "/app/data/telemetry_queue.db"))
MAX_ROWS = int(os.getenv("TELEMETRY_QUEUE_MAX_ROWS", "50000"))

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        # WAL mode: safe to read while writing, and survives a hard
        # power-loss on the rack without corrupting the queue (unlike
        # default rollback-journal mode under abrupt shutdown).
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                payload BLOB NOT NULL,
                enqueued_at TEXT DEFAULT (datetime('now')),
                attempts INTEGER DEFAULT 0,
                last_attempt_at TEXT
            )
            """
        )
        _conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_queue_id ON telemetry_queue (id)"
        )
        _conn.commit()
    return _conn


def enqueue(captured_at: datetime, payload: bytes) -> None:
    """Durably persist a sample. Returns only after it's committed to
    disk -- if this call returns, the sample survives a crash/reboot
    until explicitly acknowledged and removed by mark_sent()."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO telemetry_queue (captured_at, payload) VALUES (?, ?)",
            (captured_at.astimezone(timezone.utc).isoformat(), payload),
        )
        conn.commit()
        _enforce_cap_locked(conn)


def _enforce_cap_locked(conn: sqlite3.Connection) -> int:
    """Caller must hold _lock. Drops oldest rows past MAX_ROWS. Returns
    how many were dropped (0 in the normal case)."""
    row = conn.execute("SELECT COUNT(*) FROM telemetry_queue").fetchone()
    count = row[0] if row else 0
    overflow = count - MAX_ROWS
    if overflow <= 0:
        return 0
    conn.execute(
        "DELETE FROM telemetry_queue WHERE id IN "
        "(SELECT id FROM telemetry_queue ORDER BY id ASC LIMIT ?)",
        (overflow,),
    )
    conn.commit()
    return overflow


def peek_oldest(limit: int = 20):
    """Returns up to `limit` oldest unsent rows as (id, captured_at, payload)
    tuples, oldest first -- for the sender loop to attempt publishing."""
    with _lock:
        conn = _get_conn()
        return conn.execute(
            "SELECT id, captured_at, payload FROM telemetry_queue "
            "ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()


def mark_sent(row_id: int) -> None:
    """Remove a row once the broker has CONFIRMED delivery (not just
    that publish() didn't raise -- see orchestrator's on_publish
    handling). This is the only way a row leaves the queue as success."""
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM telemetry_queue WHERE id = ?", (row_id,))
        conn.commit()


def mark_attempt(row_id: int) -> None:
    """Record a publish attempt without removing the row -- used when
    publish was sent but delivery confirmation hasn't arrived yet, or
    when there's no connection at all to even attempt."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE telemetry_queue SET attempts = attempts + 1, "
            "last_attempt_at = datetime('now') WHERE id = ?",
            (row_id,),
        )
        conn.commit()


def pending_count() -> int:
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT COUNT(*) FROM telemetry_queue").fetchone()
        return row[0] if row else 0


def oldest_pending_age_seconds() -> Optional[float]:
    """How stale is the oldest unsent sample -- a useful health signal:
    if this keeps growing, the sender is falling behind or disconnected."""
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT enqueued_at FROM telemetry_queue ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        enqueued = datetime.fromisoformat(row[0] + "+00:00" if "+" not in row[0] and "Z" not in row[0] else row[0])
        return (datetime.now(timezone.utc) - enqueued.replace(tzinfo=timezone.utc)).total_seconds()
