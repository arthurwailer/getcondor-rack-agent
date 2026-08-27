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

import state
from uploader import upload_file, media_type_for
from geotiff import process_geotiff_zip

logger = logging.getLogger("watcher")

WATCH_DIR = Path(os.getenv("MEDIA_WATCH_DIR", "/data/watch"))
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "10"))

# Un archivo debe llevar al menos esto sin modificarse para considerarse
# "terminado de escribir". Proporcional al intervalo de escaneo (2x) en
# vez de un numero fijo chico: da margen real para videos/GeoTIFFs
# grandes que tardan mas en escribirse que una foto.
STABILITY_WINDOW_SECONDS = max(SCAN_INTERVAL_SECONDS * 2, 10)


def handle_new_file(path: Path, mission_id: str = "") -> bool:
    """Procesa un solo archivo. Devuelve True si se subio (o no aplicaba
    subir, ej. extension desconocida), False si el upload fallo -- el
    llamador decide si eso es motivo de log adicional."""
    media_type = media_type_for(path)

    if media_type is None:
        logger.debug("ignorado (extension desconocida): %s", path.name)
        return True

    logger.info("archivo estable detectado: %s (%s)", path.name, media_type)

    if media_type == "GEOTIFF" and path.suffix.lower() == ".zip":
        success = process_geotiff_zip(path, mission_id=mission_id)
    else:
        success = upload_file(path, media_type=media_type, mission_id=mission_id)

    if success:
        try:
            size = path.stat().st_size
            state.mark_uploaded(str(path), size, media_type)
        except OSError:
            # El archivo pudo haber sido movido/borrado externamente entre
            # el upload y este stat -- el upload ya quedo registrado del
            # lado del servidor, asi que no se reintenta; si reaparece se
            # volveria a subir, lo cual es preferible a perderlo del todo.
            logger.warning("no se pudo confirmar tamano final de %s tras upload", path.name)

    return success


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
            if not _is_stable(path):
                continue

            try:
                size = path.stat().st_size
            except OSError:
                continue

            if state.is_uploaded(str(path), size):
                continue

            handle_new_file(path)

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
