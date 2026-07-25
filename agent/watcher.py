"""
Watcher: detecta archivos nuevos en la carpeta de media (MEDIA_WATCH_DIR)
y dispara el upload correspondiente.

PENDIENTE DE CONFIRMAR EN EL RACK (Shamrock-AddOn / InfraredAnalyzer):
  - Convencion real de nombres de archivo (para mapear a media_type con
    mas precision que solo la extension -- ver uploader.EXT_TO_MEDIA_TYPE).
  - Si hay subcarpetas por mission_id o si el mission_id se saca de otro lado.
  - Si el modulo que ya escribe a la carpeta local usa un patron de
    "escritura atomica" (ej. escribe a .tmp y luego rename) para evitar
    subir un archivo a medio escribir. Si no lo hace, hay que agregar
    un debounce (esperar N segundos sin cambios de tamano antes de subir).

Diseño pensado con watchdog (ya en requirements.txt):

    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    class MediaHandler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                return
            handle_new_file(Path(event.src_path))

    observer = Observer()
    observer.schedule(MediaHandler(), str(WATCH_DIR), recursive=True)
    observer.start()

handle_new_file() ya tiene a donde delegar (uploader.upload_file /
geotiff.process_geotiff_zip) -- falta conectar el trigger real.
"""

import os
import logging
from pathlib import Path

from uploader import upload_file, media_type_for
from geotiff import process_geotiff_zip

logger = logging.getLogger("watcher")

WATCH_DIR = Path(os.getenv("MEDIA_WATCH_DIR", "/data/watch"))


def handle_new_file(path: Path, mission_id: str = "") -> None:
    media_type = media_type_for(path)

    if media_type is None:
        logger.debug("ignorado (extension desconocida): %s", path.name)
        return

    if media_type == "GEOTIFF" and path.suffix.lower() == ".zip":
        process_geotiff_zip(path, mission_id=mission_id)
        return

    upload_file(path, media_type=media_type, mission_id=mission_id)


def run() -> None:
    raise NotImplementedError(
        "TODO: conectar watchdog Observer sobre WATCH_DIR una vez "
        "confirmada la convencion real de archivos/carpetas en el rack."
    )


if __name__ == "__main__":
    run()
