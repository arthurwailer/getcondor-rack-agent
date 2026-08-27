"""
Entry point del rack agent.
telemetry.py: pendiente, activado via TELEMETRY_ENABLED.
"""

import os
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger("main")

# Si watcher.run() muere por una excepcion inesperada (no deberia, ya
# que _scan_once atrapa errores por archivo y por ciclo, pero un fallo
# catastrofico en el propio bucle -- ej. una excepcion durante el
# logging -- no esta descartado), se reinicia en vez de dejar el
# contenedor entero sin subir mas archivos. Backoff fijo simple: esto
# es la ultima linea de defensa, no se espera que dispare en operacion
# normal.
WATCHER_RESTART_BACKOFF_SECONDS = 30


def _run_watcher_forever() -> None:
    import watcher
    while True:
        try:
            watcher.run()
        except Exception:
            logger.exception(
                "watcher.run() termino por un error inesperado -- "
                "reiniciando en %ds", WATCHER_RESTART_BACKOFF_SECONDS,
            )
            time.sleep(WATCHER_RESTART_BACKOFF_SECONDS)


def main() -> None:
    drone_id = os.getenv("DRONE_ID", "UNKNOWN")
    logger.info("getcondor-rack-agent arrancando para drone_id=%s", drone_id)

    import threading
    import heartbeat
    threading.Thread(target=heartbeat.run, daemon=True).start()

    telemetry_enabled = os.getenv("TELEMETRY_ENABLED", "false").lower() == "true"
    if telemetry_enabled:
        import telemetry_orchestrator
        threading.Thread(target=telemetry_orchestrator.run, daemon=True).start()
        logger.info("telemetria habilitada, corriendo en background")

    # El watcher corre en el hilo principal (no en background): si
    # alguna vez se cae de forma catastrofica y agota los reintentos de
    # _run_watcher_forever (lo cual no deberia pasar), el proceso
    # termina en vez de quedar en un estado ambiguo de "vivo pero sin
    # hacer nada util" -- systemd/docker restart policy se encargan de
    # levantarlo de nuevo desde cero.
    _run_watcher_forever()


if __name__ == "__main__":
    main()
