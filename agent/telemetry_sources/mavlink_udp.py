"""
Fuente MAVLink sobre UDP -- para cuando el rack tiene acceso directo al
autopiloto (Pixhawk/ArduPilot/PX4) en vez de (o ademas de) un gimbal EO/IR.

Requiere pymavlink instalado (agregar a requirements.txt cuando se
active esta fuente -- no se agrega por defecto para no forzar la
dependencia en instalaciones que no la necesitan).
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional

from agent.telemetry_sources.base import TelemetrySource, TelemetrySample

logger = logging.getLogger("telemetry.mavlink")


class MAVLinkUDPSource(TelemetrySource):
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._conn = None

    def connect(self) -> None:
        try:
            from pymavlink import mavutil
        except ImportError:
            raise RuntimeError(
                "TELEMETRY_SOURCE_TYPE=mavlink requiere 'pymavlink' -- "
                "instalar con: pip install pymavlink"
            )
        self._conn = mavutil.mavlink_connection(f"udp:{self.host}:{self.port}")
        logger.info("conectando a MAVLink en udp:%s:%d ...", self.host, self.port)
        self._conn.wait_heartbeat(timeout=30)
        logger.info("heartbeat MAVLink recibido, sistema %d", self._conn.target_system)

    def read_next(self, timeout_seconds: float) -> Optional[TelemetrySample]:
        msg = self._conn.recv_match(
            type=["GLOBAL_POSITION_INT", "VFR_HUD"], blocking=True, timeout=timeout_seconds
        )
        if msg is None:
            return None

        if msg.get_type() == "GLOBAL_POSITION_INT":
            return TelemetrySample(
                timestamp=datetime.now(timezone.utc),
                latitude=msg.lat / 1e7,
                longitude=msg.lon / 1e7,
                altitude=msg.relative_alt / 1000.0,
                heading=msg.hdg / 100.0 if msg.hdg != 65535 else None,
                raw=msg.to_dict(),
            )
        return None

    def close(self) -> None:
        if self._conn:
            self._conn.close()
