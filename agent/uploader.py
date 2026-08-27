"""
Uploader: sube archivos (PHOTO/VIDEO/GEOTIFF) a GetCondor via
POST /media/upload, con retry + backoff exponencial para tolerar
cortes de Starlink en vuelo.

Auth: drone_id + mqtt_token (mismo esquema que ya usa el rpi-agent
existente contra media-svc), NO un API key separado.
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional

import requests

import state

logger = logging.getLogger("uploader")

GETCONDOR_API_URL = os.getenv("GETCONDOR_API_URL", "https://getcondor.win").rstrip("/")
DRONE_ID = os.getenv("DRONE_ID", "UNKNOWN")
MQTT_TOKEN = os.getenv("MQTT_TOKEN", "")

# Extension -> media_type. Placeholder hasta confirmar convencion real
# de nombres en el rack (ver watcher.py).
EXT_TO_MEDIA_TYPE = {
    ".jpg": "PHOTO",
    ".jpeg": "PHOTO",
    ".png": "PHOTO",
    ".mp4": "VIDEO",
    ".mov": "VIDEO",
    ".zip": "GEOTIFF",  # zip conteniendo el GeoTIFF, se descomprime en geotiff.py
    ".tif": "GEOTIFF",
    ".tiff": "GEOTIFF",
}

MAX_RETRIES = 6
BASE_BACKOFF_SECONDS = 5  # 5, 10, 20, 40, 80, 160...


def media_type_for(path: Path) -> Optional[str]:
    return EXT_TO_MEDIA_TYPE.get(path.suffix.lower())


def upload_file(
    path: Path,
    media_type: str,
    mission_id: str = "",
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    altitude: Optional[float] = None,
    heading: Optional[float] = None,
) -> bool:
    """
    Sube un archivo a /media/upload con retry + backoff exponencial.
    Devuelve True si el upload fue exitoso (2xx), False si se agotaron
    los reintentos o si el servidor rechazo el archivo de forma definitiva.
    """
    url = f"{GETCONDOR_API_URL}/media/upload"

    data = {
        "drone_id": DRONE_ID,
        "token": MQTT_TOKEN,
        "media_type": media_type,
        "mission_id": mission_id,
    }
    if latitude is not None:
        data["latitude"] = str(latitude)
    if longitude is not None:
        data["longitude"] = str(longitude)
    if altitude is not None:
        data["altitude"] = str(altitude)
    if heading is not None:
        data["heading"] = str(heading)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with open(path, "rb") as f:
                files = {"file": (path.name, f)}
                resp = requests.post(url, data=data, files=files, timeout=60)

            if resp.status_code == 403:
                # plan_restricted u otro rechazo definitivo: no tiene sentido
                # reintentar. Se marca como falla permanente para que el
                # watcher deje de re-listar y re-intentar este mismo archivo
                # en cada ciclo de escaneo.
                logger.error(
                    "upload rechazado (403, permanente, no se reintentara) para %s: %s",
                    path.name, resp.text,
                )
                try:
                    state.mark_permanent_failure(str(path), path.stat().st_size, "403_forbidden")
                except OSError:
                    logger.warning("no se pudo registrar falla permanente para %s", path.name)
                return False

            resp.raise_for_status()
            logger.info("upload OK: %s (%s)", path.name, media_type)
            return True

        except requests.RequestException as exc:
            wait = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "upload fallo (intento %d/%d) para %s: %s -- reintentando en %ds",
                attempt, MAX_RETRIES, path.name, exc, wait,
            )
            if attempt < MAX_RETRIES:
                time.sleep(wait)

    logger.error("upload agotado tras %d intentos: %s", MAX_RETRIES, path.name)
    return False

