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


def get_last_upload_time_by_type():
    """Returns a dict {media_type: last_uploaded_at} for the most recent
    successful upload of each type (PHOTO, VIDEO, GEOTIFF_ZIP). Missing
    types are simply absent from the dict -- the heartbeat treats an
    absent type as 'nothing of this type ever uploaded', not an error."""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT media_type, MAX(uploaded_at) FROM uploaded_files GROUP BY media_type"
        ).fetchall()
        return {media_type: ts for media_type, ts in rows if ts}


def _ensure_failures_table() -> None:
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS permanent_failures (
            path TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            reason TEXT,
            failed_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def mark_permanent_failure(path: str, size: int, reason: str = "") -> None:
    """Registra que este archivo (ruta + tamano) nunca debe reintentarse
    -- ej. un zip corrupto que nunca dejara de estarlo, o un rechazo 403
    del servidor (plan restringido, token invalido para ese drone_id).
    Sin esto, el watcher reintentaria el mismo archivo roto en cada
    ciclo de escaneo para siempre, generando logs y trabajo inutil.

    Se distingue por tamano igual que is_uploaded/mark_uploaded: si el
    archivo se sobreescribe con contenido distinto bajo el mismo nombre
    (ej. una nueva captura reusa el mismo path), se reintenta en vez de
    asumir que sigue siendo el mismo archivo roto de antes."""
    with _lock:
        _ensure_failures_table()
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO permanent_failures (path, size, reason) VALUES (?, ?, ?)",
            (path, size, reason),
        )
        conn.commit()


def is_permanent_failure(path: str, size: int) -> bool:
    with _lock:
        _ensure_failures_table()
        conn = _get_conn()
        row = conn.execute(
            "SELECT size FROM permanent_failures WHERE path = ?", (path,)
        ).fetchone()
        return row is not None and row[0] == size


def _ensure_agent_meta_table() -> None:
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()


def is_primed() -> bool:
    """True si el priming inicial (marcar el historico existente como
    'ya visto' sin subirlo) ya se ejecuto alguna vez para este rack.
    Persiste en el mismo volumen sqlite que el dedup normal, asi que
    sobrevive reinicios del rack (encendido/apagado con cada vuelo) --
    solo se ejecuta una vez en la vida del rack, no en cada arranque."""
    with _lock:
        _ensure_agent_meta_table()
        conn = _get_conn()
        row = conn.execute(
            "SELECT value FROM agent_meta WHERE key = 'primed'"
        ).fetchone()
        return row is not None and row[0] == "true"


def mark_primed() -> None:
    with _lock:
        _ensure_agent_meta_table()
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO agent_meta (key, value) VALUES ('primed', 'true')"
        )
        conn.commit()
