"""
Extrae informacion de negocio (media_type, mission_id, drone_id) a partir
de la ruta de un archivo dentro de MEDIA_WATCH_DIR, basandose en la
estructura real de carpetas del rack (confirmada en el rack fisico,
identica a la que usa shamrock-adc para sus 3 flujos):

  {WATCH_DIR}/photos/Fotos/{mes}/{dia}/{mision}/OscarNN/archivo.jpg
  {WATCH_DIR}/videos/Videos/{mes}/{dia}/{mision}/OscarNN/archivo.ts
  {WATCH_DIR}/spectral/Multiespectal/{mes}/{dia}/{mision}/OscarNN/SCA/ShamrockOutput_XXXX/archivo.zip

El nombre de cada subcarpeta raiz (photos/videos/spectral) determina el
media_type de forma mas confiable que la extension del archivo -- una
extension ambigua o inesperada dentro de la carpeta correcta sigue
identificandose bien. Los nombres de subcarpeta son configurables via
env vars para que un rack con una convencion distinta no requiera
cambios de codigo, solo de configuracion.

Logica de mission_id/drone_id calcada de migrate_ftp_to_getcondor.py
(el script de migracion historica ya probado en produccion contra los
mismos datos reales en shamrock-adc): el drone_id es la primera carpeta
que matchea "OscarNN" en cualquier parte del path, y el mission_id es
el nombre de la carpeta justo antes de esa.
"""

import os
import re
from pathlib import Path
from typing import NamedTuple, Optional

PHOTOS_SUBDIR = os.getenv("PHOTOS_SUBDIR", "photos")
VIDEOS_SUBDIR = os.getenv("VIDEOS_SUBDIR", "videos")
SPECTRAL_SUBDIR = os.getenv("SPECTRAL_SUBDIR", "spectral")

# subcarpeta raiz (en minuscula) -> media_type
ROOT_SUBDIR_TO_MEDIA_TYPE = {
    PHOTOS_SUBDIR.lower(): "PHOTO",
    VIDEOS_SUBDIR.lower(): "VIDEO",
    SPECTRAL_SUBDIR.lower(): "GEOTIFF",
}

DRONE_RE = re.compile(r"^Oscar(\d+)$", re.IGNORECASE)


class PathInfo(NamedTuple):
    media_type: Optional[str]  # None si el archivo no cae en ninguna de las 3 raices conocidas
    mission_id: str            # "" si no se pudo determinar
    drone_id: str              # "" si no se pudo determinar


def _relative_parts(path: Path, watch_dir: Path) -> list:
    try:
        rel = path.relative_to(watch_dir)
    except ValueError:
        # El archivo no esta realmente bajo watch_dir -- no deberia pasar
        # en uso normal (watcher.py solo llama esto con paths que salieron
        # de un rglob sobre watch_dir), pero se maneja sin explotar.
        return list(path.parts)
    return list(rel.parts)


def _media_type_from_root_subdir(parts: list) -> Optional[str]:
    if not parts:
        return None
    return ROOT_SUBDIR_TO_MEDIA_TYPE.get(parts[0].lower())


def _find_drone_id(parts: list) -> str:
    """Busca un componente tipo 'OscarNN' en cualquier parte de la ruta.
    Misma logica que find_drone_id() en migrate_ftp_to_getcondor.py."""
    for part in parts:
        m = DRONE_RE.match(part)
        if m:
            return f"OSCAR{m.group(1)}"
    return ""


def _find_mission_label(parts: list, drone_folder_name: str) -> str:
    """La carpeta justo antes de la carpeta de la aeronave es el nombre
    de la mision/base (ej: 'TESTE', 'Ferreira_do_Alentejo'). Misma logica
    que find_mission_label() en migrate_ftp_to_getcondor.py."""
    for i, part in enumerate(parts):
        if part.lower() == drone_folder_name.lower() and i > 0:
            return parts[i - 1]
    return ""


def resolve(path: Path, watch_dir: Path) -> PathInfo:
    parts = _relative_parts(path, watch_dir)
    media_type = _media_type_from_root_subdir(parts)

    drone_id = _find_drone_id(parts)
    mission_id = ""
    if drone_id:
        # parts guarda el nombre de carpeta real (ej "Oscar01"), no el
        # "OSCAR01" normalizado -- hay que volver a encontrarlo tal cual
        # aparece en el path para el chequeo de igualdad en
        # _find_mission_label.
        for part in parts:
            if DRONE_RE.match(part):
                mission_id = _find_mission_label(parts, part)
                break

    return PathInfo(media_type=media_type, mission_id=mission_id, drone_id=drone_id)
