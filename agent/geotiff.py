"""
Procesa los .zip espectrales: cada uno trae (tipicamente) 1-2 GeoTIFF ya
georreferenciados (RGBN y/o LWIR/KELVIN) mas un metadata.dat (JSON) con
coordenadas y datos de la mision -- mismo formato que ya sube
Shamrock-AddOn y que migrate_ftp_to_getcondor.py ya proceso en produccion
para el historico.

Cada tiff se sube como un registro GEOTIFF independiente via
uploader.upload_file (mismo endpoint que fotos/videos), en vez de subir
el .zip completo de una sola vez: mas resiliente a Starlink intermitente
(un corte a mitad de subida solo pierde el archivo en curso, no todo el
lote), y no requiere ningun cambio en el backend.

Deliberadamente NO incluye (por ahora, ver README "Known limitations"):
  - Conversion a COG (gdal_translate) -- requiere GDAL en el Dockerfile,
    dependencia pesada sin probar; el backend ya deriva bounds via
    TiTiler server-side.
  - Extraccion de FIRE_PERIMETER (contorno de incendio, ogr2ogr).
Ambas quedan para una iteracion futura, no bloqueantes para el primer
install real.
"""

import json
import logging
import re
import zipfile
from pathlib import Path
from typing import List, NamedTuple, Optional

from photo import find_drone_id, find_mission_label

logger = logging.getLogger("geotiff")

SPECTRAL_TIFF_PATTERNS = {
    "RGBN": re.compile(r"^RGBN_QuickMosaic", re.IGNORECASE),
    "LWIR": re.compile(r"^LWIR_QuickMosaic_Hotspot_Highlight", re.IGNORECASE),
    "KELVIN": re.compile(r"^LWIR_QuickMosaic_100xKelvin", re.IGNORECASE),
}

# El contorno de fuego (shapefile vectorial dentro de un zip anidado) se
# sube TAL CUAL, sin procesar ni convertir -- procesarlo (ogr2ogr, calculo
# de hectareas) requeriria GDAL en el Dockerfile, no probado, no es
# necesario para no perder el dato: subir el zip crudo como
# media_type=FIRE_PERIMETER lo deja disponible en GetCondor para
# procesarlo despues con calma, sin arriesgar nada la noche antes de un
# vuelo real.
FIRE_CONTOUR_PATTERN = re.compile(r"^fire_contours", re.IGNORECASE)

# Las operaciones de Shamrock son en Portugal continental -- se rechaza
# cualquier punto claramente fuera de rango (ej. datos de calibracion con
# coordenadas de otro continente cargadas por error) en vez de subirlo
# mal ubicado en el mapa. Mismo rango que isPlausibleCoordinate en el
# backend (drone-platform) -- ver INSTALL.md sobre por que la validacion
# geografica real vive solo alli; este chequeo aqui es una red de
# seguridad adicional, no la fuente de verdad.
PT_LAT_RANGE = (36.0, 43.0)
PT_LON_RANGE = (-10.0, -6.0)


def _to_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _read_metadata_dat(zf: zipfile.ZipFile, zip_path: Path) -> dict:
    """Busca metadata.dat en cualquier parte del zip (puede estar en la
    raiz o en una subcarpeta segun la version del firmware) y lo parsea
    como JSON. Devuelve {} si no existe o no parsea -- se sigue sin esos
    campos en vez de descartar el zip entero."""
    candidates = [n for n in zf.namelist() if Path(n).name.lower() == "metadata.dat"]
    if not candidates:
        logger.warning("no se encontro metadata.dat dentro de %s", zip_path.name)
        return {}
    try:
        raw = zf.read(candidates[0])
        return json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception as exc:
        logger.warning("no se pudo parsear metadata.dat de %s: %s", zip_path.name, exc)
        return {}


class SpectralUploadTask(NamedTuple):
    """Un archivo individual dentro del zip espectral, listo para
    extraer y subir -- ya sea un tiff de sensor (media_type=GEOTIFF,
    sensor=RGBN/LWIR/KELVIN) o el contorno de fuego crudo
    (media_type=FIRE_PERIMETER, sensor=None, sin procesar)."""
    zip_member: str
    media_type: str
    sensor: Optional[str]
    filename: str


def _find_sensor_tiffs(zf: zipfile.ZipFile) -> List[SpectralUploadTask]:
    tasks = []
    for member in zf.namelist():
        name = Path(member).name

        if member.lower().endswith((".tif", ".tiff")):
            sensor = next(
                (tag for tag, pat in SPECTRAL_TIFF_PATTERNS.items() if pat.search(name)),
                None,
            )
            if sensor:
                tasks.append(SpectralUploadTask(zip_member=member, media_type="GEOTIFF", sensor=sensor, filename=name))
            else:
                logger.warning("tiff de sensor desconocido ignorado dentro de %s: %s", zf.filename, name)
            continue

        if member.lower().endswith(".zip") and FIRE_CONTOUR_PATTERN.search(name):
            tasks.append(SpectralUploadTask(zip_member=member, media_type="FIRE_PERIMETER", sensor=None, filename=name))

    return tasks


def process_geotiff_zip(zip_path: Path, mission_id: str = "") -> bool:
    """Punto de entrada: dado un .zip espectral que aparecio en la
    carpeta watch, extrae metadata.dat + cada tiff de sensor conocido
    (RGBN/LWIR/KELVIN), y sube cada uno como un registro GEOTIFF
    independiente. Devuelve True solo si TODOS los tiffs encontrados se
    subieron con exito -- un exito parcial se trata como fallo global
    para que el watcher no marque el .zip como "ya subido" y pierda los
    tiffs que fallaron."""
    from uploader import upload_file  # import diferido: evita import circular con photo.py

    drone_id = find_drone_id(zip_path)
    if not drone_id:
        logger.warning("no se pudo determinar drone_id para %s", zip_path)
        return False

    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile:
        logger.error("archivo no es un zip valido (permanente, no se reintentara): %s", zip_path)
        _mark_permanent(zip_path, "bad_zip")
        return False

    with zf:
        meta = _read_metadata_dat(zf, zip_path)
        lat = _to_float(meta.get("LATITUDE_CENTROID") or meta.get("LATITUDE"))
        lon = _to_float(meta.get("LONGITUDE_CENTROID") or meta.get("LONGITUDE"))
        alt = _to_float(meta.get("ALTITUDE_AVERAGE") or meta.get("ALTITUDE"))

        if lat is not None and lon is not None:
            in_range = PT_LAT_RANGE[0] <= lat <= PT_LAT_RANGE[1] and PT_LON_RANGE[0] <= lon <= PT_LON_RANGE[1]
            if not in_range:
                logger.warning(
                    "coordenadas fuera de rango esperado para Portugal (lat=%s, lon=%s) en %s -- se sube sin posicion",
                    lat, lon, zip_path.name,
                )
                lat, lon = None, None

        upload_tasks = _find_sensor_tiffs(zf)
        if not upload_tasks:
            logger.warning("%s no contiene tiffs de sensor conocido ni contorno de fuego (permanente, no se reintentara)", zip_path.name)
            _mark_permanent(zip_path, "no_known_content")
            return False

        resolved_mission_id = mission_id or (find_mission_label(zip_path, f"Oscar{drone_id[-2:]}") or "")

        import state as _state

        all_ok = True
        any_permanent = False
        for task in upload_tasks:
            extracted_path = None
            try:
                extracted_path = _extract_member_to_tmp(zf, task.zip_member, task.filename)
                task_metadata = dict(meta)
                if task.sensor is not None:
                    task_metadata["sensor"] = task.sensor
                task_metadata["source_zip"] = zip_path.name

                extracted_size = extracted_path.stat().st_size
                success = upload_file(
                    extracted_path, media_type=task.media_type, mission_id=resolved_mission_id,
                    latitude=lat, longitude=lon, altitude=alt,
                    metadata=json.dumps(task_metadata),
                )
                if not success:
                    all_ok = False
                    # upload_file ya marco el tiff extraido (path temporal)
                    # como permanente en state.py si fue 400/403 -- se
                    # chequea aca (mismo path+tamano) para saber si
                    # propagar esa condicion al .zip contenedor: un
                    # rechazo definitivo del servidor (plan restringido,
                    # payload invalido) nunca se va a arreglar solo
                    # reintentando el mismo zip una y otra vez.
                    if _state.is_permanent_failure(str(extracted_path), extracted_size):
                        any_permanent = True
            finally:
                if extracted_path is not None:
                    try:
                        extracted_path.unlink(missing_ok=True)
                    except OSError:
                        logger.warning("no se pudo limpiar tiff temporal %s", extracted_path)

    if not all_ok and any_permanent:
        logger.error(
            "al menos un tiff de %s fue rechazado de forma permanente por el servidor -- "
            "marcando el .zip completo como permanente, no se reintentara",
            zip_path.name,
        )
        _mark_permanent(zip_path, "contains_permanently_rejected_tiff")

    return all_ok


def _extract_member_to_tmp(zf: zipfile.ZipFile, member: str, filename: str) -> Path:
    """Extrae un solo miembro del zip a un archivo temporal, con el
    nombre real del tiff (uploader.py usa path.name para el nombre subido)."""
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="geotiff_"))
    out_path = tmp_dir / filename
    with zf.open(member) as src, open(out_path, "wb") as dst:
        dst.write(src.read())
    return out_path


def _mark_permanent(zip_path: Path, reason: str) -> None:
    import state
    try:
        size = zip_path.stat().st_size
        state.mark_permanent_failure(str(zip_path), size, reason)
    except OSError:
        logger.warning("no se pudo registrar falla permanente para %s", zip_path)
