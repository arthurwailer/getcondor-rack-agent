"""
Parseo de fotos: extrae drone_id, mission_id, timestamp real de captura y
coordenadas GPS del target desde el nombre de archivo y el EXIF UserComment
que ya escribe Shamrock-AddOn (metaOSD.py / UploadMetadata()).

Misma convencion que ya usa migrate_ftp_to_getcondor.py -- se mantiene el
mismo parseo para no divergir en como se interpreta la estructura de
carpetas y el EXIF.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PIL import Image
import pytz

logger = logging.getLogger("photo")

LISBON_TZ = pytz.timezone("Europe/Lisbon")

DRONE_RE = re.compile(r"^Oscar(\d+)$", re.IGNORECASE)
PHOTO_FILENAME_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})-(\d{3})_CAM\d"
)


def find_drone_id(path: Path) -> Optional[str]:
    """Busca un componente tipo 'OscarNN' en cualquier parte del path
    (estructura que usa shamrock-adc, donde Insync junta varios OSCAR en
    un mismo arbol). Si no lo encuentra -- caso normal corriendo DENTRO
    de un rack individual, donde todo lo que hay en la carpeta ya es de
    ese avion -- cae al DRONE_ID fijo del entorno en vez de descartar
    el archivo."""
    for part in path.parts:
        m = DRONE_RE.match(part)
        if m:
            return f"OSCAR{m.group(1)}"
    env_drone_id = os.getenv("DRONE_ID")
    return env_drone_id if env_drone_id and env_drone_id != "UNKNOWN" else None


def find_mission_label(path: Path, drone_folder_name: str) -> Optional[str]:
    """La carpeta justo antes de la carpeta de la aeronave suele ser el
    nombre de la mision/base (ej: 'TESTE', 'Ferreira_do_Alentejo')."""
    parts = path.parts
    for i, part in enumerate(parts):
        if part.lower() == drone_folder_name.lower() and i > 0:
            return parts[i - 1]
    return None


def parse_timestamp(filename: str):
    m = PHOTO_FILENAME_RE.search(filename)
    if not m:
        return None
    date_str, hh, mm, ss, ms = m.groups()
    naive_dt = datetime.strptime(f"{date_str} {hh}:{mm}:{ss}.{ms}000", "%Y-%m-%d %H:%M:%S.%f")
    local_dt = LISBON_TZ.localize(naive_dt)
    return local_dt.astimezone(timezone.utc)


def _to_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dms_to_decimal(dms, ref: str) -> float:
    degrees, minutes, seconds = dms
    value = float(degrees) + float(minutes) / 60 + float(seconds) / 3600
    if ref in ("S", "W"):
        value = -value
    return value


def parse_standard_exif_gps(image_path: Path) -> dict:
    """Fallback cuando la foto no trae el UserComment custom de
    metaOSD.py (camara distinta, o UserComment ausente): lee los tags
    GPS EXIF estandar (GPSLatitude/GPSLongitude/GPSAltitude/
    GPSImgDirection), que la mayoria de camaras si escribe de forma
    nativa. Devuelve {} si no hay GPS EXIF tampoco -- nunca se inventan
    coordenadas."""
    from PIL.ExifTags import TAGS, GPSTAGS
    try:
        img = Image.open(image_path)
        raw = img._getexif()
        if not raw:
            return {}
        exif = {TAGS.get(k, k): v for k, v in raw.items()}
        gps_info = exif.get("GPSInfo")
        if not gps_info:
            return {}
        gps = {GPSTAGS.get(k, k): v for k, v in gps_info.items()}
        result = {}
        if "GPSLatitude" in gps and "GPSLatitudeRef" in gps:
            result["LATITUDE"] = _dms_to_decimal(gps["GPSLatitude"], gps["GPSLatitudeRef"])
        if "GPSLongitude" in gps and "GPSLongitudeRef" in gps:
            result["LONGITUDE"] = _dms_to_decimal(gps["GPSLongitude"], gps["GPSLongitudeRef"])
        if "GPSAltitude" in gps:
            try:
                result["ALTITUDE"] = float(gps["GPSAltitude"])
            except (TypeError, ValueError):
                pass
        if "GPSImgDirection" in gps:
            try:
                result["FRAME_HEADING"] = float(gps["GPSImgDirection"])
            except (TypeError, ValueError):
                pass
        return result
    except Exception as exc:
        logger.warning("no se pudo leer EXIF GPS estandar de %s: %s", image_path.name, exc)
        return {}


def parse_exif_usercomment(image_path: Path) -> dict:
    """Devuelve dict con LATITUDE/LONGITUDE/ALTITUDE/etc, o {} si no hay/falla.

    El UserComment de estas camaras a veces viene truncado en origen (falta
    el '}}' de cierre, parece un limite de buffer del firmware) -- si el
    parseo normal falla, se reintenta agregando el cierre faltante antes
    de rendirse."""
    try:
        img = Image.open(image_path)
        exif = img._getexif()
        if not exif:
            return {}
        for tag_id, value in exif.items():
            if tag_id == 37510 or (isinstance(value, bytes) and value.startswith(b"UNICODE")):
                if not isinstance(value, bytes):
                    continue
                idx = value.find(b"{\x00")
                if idx == -1:
                    continue
                raw = value[idx:]
                text = raw.decode("utf-16-le", errors="ignore").strip("\x00")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    for repair in ('"}', '}', '""}'):
                        try:
                            return json.loads(text + repair)
                        except json.JSONDecodeError:
                            continue
                    return {}
    except Exception as exc:
        logger.warning("no se pudo leer EXIF de %s: %s", image_path.name, exc)
        return {}
    return {}


class PhotoTask:
    """Datos extraidos de una foto, listos para subir via uploader.upload_file."""

    def __init__(self, path, drone_id, mission_id, captured_at, latitude, longitude, altitude, heading, metadata):
        self.path = path
        self.drone_id = drone_id
        self.mission_id = mission_id
        self.captured_at = captured_at
        self.latitude = latitude
        self.longitude = longitude
        self.altitude = altitude
        self.heading = heading
        self.metadata = metadata


def build_photo_task(path: Path) -> Optional[PhotoTask]:
    """Punto de entrada: dado un .jpg que aparecio en la carpeta watch,
    arma la tarea de subida, o None si algo no se pudo determinar
    (drone_id desconocido, nombre no matchea el patron esperado)."""
    drone_id = find_drone_id(path)
    if not drone_id:
        logger.warning("no se pudo determinar drone_id para %s", path)
        return None

    captured_at = parse_timestamp(path.name)
    if not captured_at:
        logger.warning("no se pudo parsear timestamp del nombre: %s", path.name)
        return None

    meta = parse_exif_usercomment(path)
    if not meta:
        # No hay UserComment de metaOSD.py -- cae a GPS EXIF estandar
        # en vez de subir la foto sin coordenadas.
        meta = parse_standard_exif_gps(path)
    mission_id = find_mission_label(path, f"Oscar{drone_id[-2:]}")

    return PhotoTask(
        path=path,
        drone_id=drone_id,
        mission_id=mission_id or "",
        captured_at=captured_at,
        latitude=_to_float(meta.get("LATITUDE")),
        longitude=_to_float(meta.get("LONGITUDE")),
        altitude=_to_float(meta.get("ALTITUDE")),
        heading=_to_float(meta.get("FRAME_HEADING")) or _to_float(meta.get("GIMBAL_HEADING")),
        metadata=meta if meta else None,
    )
