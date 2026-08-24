"""
Orquestador de telemetria. Elige la fuente segun TELEMETRY_SOURCE_TYPE,
la conecta, y sube cada muestra a GetCondor.

Agregar un tipo de fuente nuevo:
  1) crear agent/telemetry_sources/mi_fuente.py implementando TelemetrySource
  2) sumarlo a SOURCE_REGISTRY aqui abajo
  3) TELEMETRY_SOURCE_TYPE=mi_fuente en config/.env
No hay que tocar main.py, uploader.py, ni las demas fuentes.
"""

import logging
import os
import time

from agent.telemetry_sources.base import TelemetrySource
from agent.telemetry_sources.raw_udp import RawUDPSource
from agent.telemetry_sources.mavlink_udp import MAVLinkUDPSource
from agent.telemetry_sources.generic_json_udp import GenericJSONUDPSource
import uploader

logger = logging.getLogger("telemetry")

SOURCE_REGISTRY = {
    "raw": lambda: RawUDPSource(
        host=os.getenv("TELEMETRY_UDP_HOST", "0.0.0.0"),
        port=int(os.getenv("TELEMETRY_UDP_PORT", "14550")),
    ),
    "mavlink": lambda: MAVLinkUDPSource(
        host=os.getenv("TELEMETRY_UDP_HOST", "0.0.0.0"),
        port=int(os.getenv("TELEMETRY_UDP_PORT", "14550")),
    ),
    "generic_json": lambda: GenericJSONUDPSource(
        host=os.getenv("TELEMETRY_UDP_HOST", "0.0.0.0"),
        port=int(os.getenv("TELEMETRY_UDP_PORT", "14550")),
        field_lat=os.getenv("TELEMETRY_JSON_FIELD_LAT", "lat"),
        field_lon=os.getenv("TELEMETRY_JSON_FIELD_LON", "lon"),
        field_alt=os.getenv("TELEMETRY_JSON_FIELD_ALT", "alt"),
        field_heading=os.getenv("TELEMETRY_JSON_FIELD_HEADING", "heading"),
        field_speed=os.getenv("TELEMETRY_JSON_FIELD_SPEED", "speed"),
    ),
}


def _build_source() -> TelemetrySource:
    source_type = os.getenv("TELEMETRY_SOURCE_TYPE", "raw")
    factory = SOURCE_REGISTRY.get(source_type)
    if factory is None:
        raise RuntimeError(
            f"TELEMETRY_SOURCE_TYPE='{source_type}' desconocido -- "
            f"opciones: {list(SOURCE_REGISTRY.keys())}"
        )
    return factory()


def run() -> None:
    source = _build_source()
    drone_id = os.getenv("DRONE_ID", "UNKNOWN")

    while True:
        try:
            source.connect()
            break
        except Exception:
            logger.exception("fallo conectando fuente de telemetria -- reintentando en 10s")
            time.sleep(10)

    logger.info("telemetria arrancando -- drone_id=%s, fuente=%s",
                drone_id, os.getenv("TELEMETRY_SOURCE_TYPE", "raw"))

    try:
        while True:
            try:
                sample = source.read_next(timeout_seconds=5.0)
            except Exception:
                logger.exception("error leyendo telemetria -- se reintenta")
                time.sleep(2)
                continue

            if sample is None:
                continue

            ok = uploader.upload_telemetry(
                drone_id=drone_id,
                timestamp=sample["timestamp"],
                latitude=sample["latitude"],
                longitude=sample["longitude"],
                altitude=sample.get("altitude"),
                heading=sample.get("heading"),
                speed=sample.get("speed"),
            )
            if not ok:
                logger.warning("no se pudo subir muestra de telemetria (se descarta, no se reintenta)")
    finally:
        source.close()
