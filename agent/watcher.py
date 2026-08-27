"""
Watcher: detecta archivos nuevos en la carpeta de media (MEDIA_WATCH_DIR)
y dispara el upload correspondiente.

Usa polling periodico (no watchdog/inotify) a proposito: en un rack
embebido con Starlink intermitente, un mecanismo basado en eventos del
filesystem puede fallar en silencio sobre ciertos tipos de montaje (red,
algunos filesystems USB) y perder archivos sin que nadie lo note. Polling
es mas simple de razonar y de debuggear -- si corrio el escaneo, se ve en
el log, sin depender de que el SO haya entregado el evento correcto.

Estabilidad de archivo (evitar subir uno a medio escribir): se compara
la fecha de modificacion (mtime) contra el momento actual -- si el
archivo fue tocado hace menos de STABILITY_WINDOW_SECONDS, se ignora por
ahora. Mismo patron que usa el watcher real de Shamrock-AddOn
(arcgis-drive-check/search_drive), preferido sobre comparar tamanos en
memoria porque es completamente sin estado: sobrevive un reinicio del
propio watcher sin perder progreso ni esperar un ciclo extra de mas.

Resiliencia: ninguna excepcion en un archivo individual, ni en un
escaneo completo, mata el loop. El loop de polling corre para siempre
salvo que el proceso mismo se termine (systemd/docker se encargan de
reiniciar el contenedor si eso pasara).

Dedup: agent/state.py trackea que archivos (ruta + tamano) ya se
subieron, en SQLite persistente -- sobrevive reinicios del rack.
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional

import json

import pathinfo
import photo
import state
import video
from uploader import upload_file
from geotiff import process_geotiff_zip

logger = logging.getLogger("watcher")

WATCH_DIR = Path(os.getenv("MEDIA_WATCH_DIR", "/data/watch"))
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "10"))

# Un archivo debe llevar al menos esto sin modificarse para considerarse
# "terminado de escribir". Proporcional al intervalo de escaneo (2x) en
# vez de un numero fijo chico: da margen real para videos/GeoTIFFs
# grandes que tardan mas en escribirse que una foto.
STABILITY_WINDOW_SECONDS = max(SCAN_INTERVAL_SECONDS * 2, 10)


def _captured_at_iso(dt) -> Optional[str]:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _metadata_json(meta) -> Optional[str]:
    if not meta:
        return None
    try:
        return json.dumps(meta)
    except (TypeError, ValueError):
        logger.warning("metadata no serializable a JSON, se omite")
        return None


def _handle_photo(path: Path, mission_id: str) -> bool:
    """mission_id viene de pathinfo (fuente unica de verdad sobre
    estructura de carpetas -- ver watcher.py/_scan_once), no del propio
    photo.py, que tiene su propia copia de la misma logica de parseo de
    ruta para cuando corre fuera de este watcher (ej. scripts de
    migracion historicos). Se ignora deliberadamente el mission_id que
    build_photo_task calcularia por su cuenta, para no tener 2 fuentes
    de verdad que puedan divergir."""
    task = photo.build_photo_task(path)
    if task is None:
        # drone_id o timestamp no se pudieron determinar del nombre del
        # archivo -- se sube igual con lo que sabemos por la carpeta
        # (media_type, mission_id), sin GPS/captured_at/metadata, en vez
        # de descartar el archivo por completo.
        logger.warning("no se pudo enriquecer %s con datos EXIF/nombre, subiendo sin ellos", path.name)
        return upload_file(path, media_type="PHOTO", mission_id=mission_id)

    return upload_file(
        path, media_type="PHOTO", mission_id=mission_id,
        latitude=task.latitude, longitude=task.longitude,
        altitude=task.altitude, heading=task.heading,
        captured_at=_captured_at_iso(task.captured_at),
        metadata=_metadata_json(task.metadata),
    )


def _handle_video(path: Path, mission_id: str) -> bool:
    """Mismo criterio que _handle_photo sobre mission_id: se usa el de
    pathinfo, no el que build_video_task recalcularia."""
    task = video.build_video_task(path)
    if task is None:
        logger.warning("no se pudo enriquecer %s con datos de sidecar/nombre, subiendo sin ellos", path.name)
        return upload_file(path, media_type="VIDEO", mission_id=mission_id)

    try:
        success = upload_file(
            task.upload_path, media_type="VIDEO", mission_id=mission_id,
            latitude=task.latitude, longitude=task.longitude,
            altitude=task.altitude, heading=task.heading,
            captured_at=_captured_at_iso(task.captured_at),
            metadata=_metadata_json(task.metadata),
        )
    finally:
        # El .mp4 remuxeado es un archivo temporal fuera de MEDIA_WATCH_DIR
        # (vive en /tmp) -- se limpia siempre, haya salido bien o mal el
        # upload, para no acumular basura en el disco del rack con el
        # tiempo. El .ts original (bajo MEDIA_WATCH_DIR) nunca se toca --
        # eso es responsabilidad de quien lo genera, no de este agente.
        if task.is_temp_upload_path:
            try:
                task.upload_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("no se pudo limpiar archivo temporal %s", task.upload_path)

    return success


def handle_new_file(path: Path, media_type: str, mission_id: str = "") -> bool:
    """Procesa un solo archivo, ya con media_type/mission_id resueltos por
    pathinfo (segun la subcarpeta raiz y la posicion de la carpeta de la
    aeronave en el path -- ver pathinfo.py). Para PHOTO/VIDEO, enriquece
    con GPS/timestamp/metadata real via photo.py/video.py antes de subir.
    Devuelve True si se subio, False si el upload fallo -- el llamador
    decide si eso es motivo de log adicional."""
    logger.info(
        "archivo estable detectado: %s (%s, mission=%s)",
        path.name, media_type, mission_id or "?",
    )

    if media_type == "PHOTO":
        return _handle_photo(path, mission_id)
    if media_type == "VIDEO":
        return _handle_video(path, mission_id)
    if media_type == "GEOTIFF" and path.suffix.lower() == ".zip":
        return process_geotiff_zip(path, mission_id=mission_id)
    return upload_file(path, media_type=media_type, mission_id=mission_id)


def _is_stable(path: Path) -> bool:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        # Desaparecio entre el listado y este stat -- tratarlo como
        # "no estable todavia", reaparecera en el proximo escaneo si sigue
        # existiendo de verdad.
        return False
    return (time.time() - mtime) >= STABILITY_WINDOW_SECONDS


def _scan_once() -> None:
    if not WATCH_DIR.exists():
        logger.warning("MEDIA_WATCH_DIR no existe todavia: %s", WATCH_DIR)
        return

    try:
        candidates = [p for p in WATCH_DIR.rglob("*") if p.is_file()]
    except OSError as exc:
        logger.warning("error leyendo %s: %s -- reintentando proximo ciclo", WATCH_DIR, exc)
        return

    for path in candidates:
        # Cada archivo aislado: un fallo puntual (permisos, disco lleno,
        # excepcion inesperada en el upload) nunca debe frenar el resto
        # del lote ni el proximo ciclo de escaneo.
        try:
            info = pathinfo.resolve(path, WATCH_DIR)
            if info.media_type is None:
                # No cae bajo ninguna de las 3 raices conocidas
                # (photos/videos/spectral) -- probablemente un archivo
                # temporal o ajeno al pipeline, se ignora en silencio.
                continue

            if not _is_stable(path):
                continue

            try:
                size = path.stat().st_size
            except OSError:
                continue

            if state.is_uploaded(str(path), size):
                continue

            if state.is_permanent_failure(str(path), size):
                # Ya se determino antes que este archivo nunca se va a
                # poder subir (zip corrupto, sin GeoTIFF adentro, 403 del
                # servidor) -- se ignora en silencio en vez de reintentar
                # (y loguear el mismo error) en cada ciclo de escaneo para
                # siempre.
                continue

            success = handle_new_file(path, info.media_type, mission_id=info.mission_id)
            if not success:
                continue

            try:
                state.mark_uploaded(str(path), size, info.media_type)
            except OSError:
                logger.warning("no se pudo registrar dedup para %s", path.name)

        except Exception:
            logger.exception("error inesperado procesando %s, continuando con el resto", path)


def run() -> None:
    logger.info(
        "watcher starting -- folder: %s, scan interval: %ds, stability window: %ds",
        WATCH_DIR, SCAN_INTERVAL_SECONDS, STABILITY_WINDOW_SECONDS,
    )
    while True:
        try:
            _scan_once()
        except Exception:
            # Ultima linea de defensa: ni siquiera un fallo catastrofico
            # dentro de _scan_once (ej. state.py sin poder abrir su DB)
            # debe matar el loop -- se reintenta en el proximo ciclo.
            logger.exception("error inesperado durante el escaneo, continuando")
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
