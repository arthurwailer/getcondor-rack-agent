"""
Entry point del rack agent.
TODO: watcher.run() todavia no esta implementado (ver watcher.py) --
falta confirmar en el rack la convencion de archivos/carpetas.
telemetry.py: pendiente, activado via TELEMETRY_ENABLED.
"""

import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger("main")


def main() -> None:
    drone_id = os.getenv("DRONE_ID", "UNKNOWN")
    logger.info("getcondor-rack-agent arrancando para drone_id=%s", drone_id)

    telemetry_enabled = os.getenv("TELEMETRY_ENABLED", "false").lower() == "true"
    if telemetry_enabled:
        import threading
        from agent import telemetry_orchestrator
        threading.Thread(target=telemetry_orchestrator.run, daemon=True).start()
        logger.info("telemetria habilitada, corriendo en background")

    try:
        import watcher
        watcher.run()
    except NotImplementedError as exc:
        logger.warning("watcher no conectado todavia: %s", exc)
        logger.info("agente en modo idle -- conectar watcher.run() cuando este listo")
        # Mantener el contenedor vivo mientras se termina watcher.py
        import time
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
