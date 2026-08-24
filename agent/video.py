"""
Parseo de videos: extrae drone_id, mission_id y timestamp real de captura
desde el nombre de archivo, y coordenadas/OSCAR/matricula desde el sidecar
.dat (JSON) que ya genera Shamrock-AddOn (metaOSD.py / __addMetadataInfoToVideo()).

Los .ts (MPEG-TS) se remuxean a .mp4 antes de subir -- los navegadores no
reproducen MPEG-TS nativo, y "-c copy" solo cambia el contenedor sin
recodificar (rapido, sin perdida de calidad), mismo enfoque que
migrate_ftp_to_getcondor.py.
"""

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from photo import DRONE_RE, PHOTO_FILENAME_RE, find_drone_id, find_mission_label, LISBON_TZ  # noqa: F401
from datetime import datetime, timezone
import re

logger = logging.getLogger("video")

VIDEO_FILENAME_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})_CAM\d"
)


def parse_timestamp(filename: str):
    m = VIDEO_FILENAME_RE.search(filename)
    if not m:
        return None
    date_str, hh, mm, ss = m.groups()
    naive_dt = datetime.strptime(f"{date_str} {hh}:{mm}:{ss}.000000", "%Y-%m-%d %H:%M:%S.%f")
    local_dt = LISBON_TZ.localize(naive_dt)
    return local_dt.astimezone(timezone.utc)


def _to_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_video_sidecar(path: Path) -> dict:
    """El .dat vive en una carpeta 'data/' junto al video, mismo nombre
    sin extension. Si no existe o no parsea, se sigue sin esos campos
    (mejor subir el video sin metadata que no subirlo)."""
    sidecar = path.parent / "data" / path.name.replace(".ts", ".dat")
    if not sidecar.exists():
        return {}
    try:
        with open(sidecar) as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("no se pudo leer sidecar %s: %s", sidecar, exc)
        return {}


def remux_to_mp4(ts_path: Path) -> Optional[Path]:
    """Remuxea .ts a .mp4 sin recodificar (-c copy = solo cambia el
    contenedor). Devuelve la ruta al .mp4 temporal, o None si fallo --
    en ese caso el llamador debe subir el .ts original como red de
    seguridad en vez de perder el archivo."""
    tmp_dir = Path(tempfile.gettempdir())
    mp4_path = tmp_dir / ts_path.name.replace(".ts", ".mp4")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(ts_path), "-c", "copy", "-movflags", "+faststart", str(mp4_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
        )
        if result.returncode != 0 or not mp4_path.exists():
            logger.warning("remux fallo para %s: %s", ts_path.name, result.stderr.decode(errors="ignore")[:300])
            return None
        return mp4_path
    except Exception as exc:
        logger.warning("remux exception para %s: %s", ts_path.name, exc)
        return None


class VideoTask:
    """Datos extraidos de un video, listos para subir via uploader.upload_file.
    upload_path puede diferir de path original (mp4 remuxeado) -- el
    llamador es responsable de limpiar el archivo temporal despues de subir."""

    def __init__(self, path, upload_path, drone_id, mission_id, captured_at, latitude, longitude, altitude, heading, metadata, is_temp_upload_path):
        self.path = path
        self.upload_path = upload_path
        self.drone_id = drone_id
        self.mission_id = mission_id
        self.captured_at = captured_at
        self.latitude = latitude
        self.longitude = longitude
        self.altitude = altitude
        self.heading = heading
        self.metadata = metadata
        self.is_temp_upload_path = is_temp_upload_path


def build_video_task(path: Path) -> Optional[VideoTask]:
    """Punto de entrada: dado un .ts que aparecio en la carpeta watch,
    arma la tarea de subida (con remux a mp4 si es posible), o None si
    no se pudo determinar drone_id/timestamp."""
    drone_id = find_drone_id(path)
    if not drone_id:
        logger.warning("no se pudo determinar drone_id para %s", path)
        return None

    captured_at = parse_timestamp(path.name)
    if not captured_at:
        logger.warning("no se pudo parsear timestamp del nombre: %s", path.name)
        return None

    meta = parse_video_sidecar(path)
    mission_id = find_mission_label(path, f"Oscar{drone_id[-2:]}")

    mp4_path = remux_to_mp4(path)
    upload_path = mp4_path if mp4_path else path
    is_temp = mp4_path is not None

    return VideoTask(
        path=path,
        upload_path=upload_path,
        drone_id=drone_id,
        mission_id=mission_id or "",
        captured_at=captured_at,
        latitude=_to_float(meta.get("LATITUDE")),
        longitude=_to_float(meta.get("LONGITUDE")),
        altitude=_to_float(meta.get("ALTITUDE")),
        heading=0.0,
        metadata=meta if meta else None,
        is_temp_upload_path=is_temp,
    )
