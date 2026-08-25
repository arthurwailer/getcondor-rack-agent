"""
Estado persistente del agente (SQLite) -- registra que archivos ya se
subieron para no volver a subirlos, y sobrevive a reinicios del rack (el
volumen agent-state en docker-compose.yml lo persiste fuera del
contenedor).
"""

import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger("state")

DB_PATH = Path(os.getenv("STATE_DB_PATH", "/app/data/state.db"))

_lock = threading.Lock()
_conn = None


def _get_conn():
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS uploaded_files (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                media_type TEXT,
                uploaded_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        _conn.commit()
    return _conn


def is_uploaded(path: str, size: int) -> bool:
    """True si este archivo (misma ruta y mismo tamano) ya se subio.
    Compara tamano tambien: si el archivo se sobreescribio con contenido
    distinto bajo el mismo nombre, se vuelve a subir en vez de asumir
    que es el mismo de antes."""
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT size FROM uploaded_files WHERE path = ?", (path,)
        ).fetchone()
        return row is not None and row[0] == size


def mark_uploaded(path: str, size: int, media_type: str) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO uploaded_files (path, size, media_type) VALUES (?, ?, ?)",
            (path, size, media_type),
        )
        conn.commit()


def get_last_upload_time():
    """Returns the timestamp of the most recent successful upload, or
    None if nothing has been uploaded yet. Used by the heartbeat to
    report freshness -- lets an admin see 'last upload: 2 minutes ago'
    without connecting to the rack directly."""
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT MAX(uploaded_at) FROM uploaded_files"
        ).fetchone()
        return row[0] if row and row[0] else None


def count_uploaded_last_hour():
    """Returns how many files were uploaded in the last hour. A sudden
    drop to 0 during an active mission is a useful signal something's
    wrong, even before checking logs."""
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT COUNT(*) FROM uploaded_files WHERE uploaded_at >= datetime('now', '-1 hour')"
        ).fetchone()
        return row[0] if row else 0
