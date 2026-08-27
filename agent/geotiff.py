"""
Maneja el caso de ortofotomapas: llega un .zip conteniendo un GeoTIFF,
hay que descomprimirlo y subir el .tif/.tiff real como media_type=GEOTIFF
(no el .zip en si).
"""

import logging
import zipfile
import tempfile
from pathlib import Path
from typing import Optional

import state
from uploader import upload_file

logger = logging.getLogger("geotiff")

GEOTIFF_EXTENSIONS = {".tif", ".tiff"}


def extract_geotiff(zip_path: Path) -> Optional[Path]:
    """
    Descomprime zip_path a una carpeta temporal y devuelve el path
    al primer .tif/.tiff encontrado adentro, o None si no hay ninguno.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="geotiff_"))
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_dir)
    except zipfile.BadZipFile:
        logger.error("archivo no es un zip valido (permanente, no se reintentara): %s", zip_path)
        _mark_permanent(zip_path, "bad_zip")
        return None

    for candidate in tmp_dir.rglob("*"):
        if candidate.suffix.lower() in GEOTIFF_EXTENSIONS:
            return candidate

    logger.warning("no se encontro GeoTIFF dentro de %s (permanente, no se reintentara)", zip_path)
    _mark_permanent(zip_path, "no_geotiff_in_zip")
    return None


def _mark_permanent(zip_path: Path, reason: str) -> None:
    try:
        size = zip_path.stat().st_size
        state.mark_permanent_failure(str(zip_path), size, reason)
    except OSError:
        logger.warning("no se pudo registrar falla permanente para %s", zip_path)


def process_geotiff_zip(zip_path: Path, mission_id: str = "") -> bool:
    """
    Punto de entrada: dado un .zip que llego a la carpeta watch,
    extrae el GeoTIFF y lo sube. Devuelve True si se subio ok.
    """
    tif_path = extract_geotiff(zip_path)
    if tif_path is None:
        return False

    return upload_file(tif_path, media_type="GEOTIFF", mission_id=mission_id)
