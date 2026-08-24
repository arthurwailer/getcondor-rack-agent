"""
Fuente JSON generica sobre UDP -- para sistemas (muchos gimbals EO/IR)
que mandan cada muestra de telemetria como un objeto JSON por paquete UDP.

Los NOMBRES de los campos varian por fabricante, asi que en vez de
hardcodear "lat"/"lon"/etc, se mapean via config -- cuando tengas el
gimbal delante y veas el JSON real (via RawUDPSource), solo ajustas
estas variables de entorno, no el codigo.
"""

from __future__ import annotations
import json
import logging
import socket
from datetime import datetime, timezone
from typing import Optional

from agent.telemetry_sources.base import TelemetrySource, TelemetrySample

logger = logging.getLogger("telemetry.generic_json")


class GenericJSONUDPSource(TelemetrySource):
    def __init__(
        self, host: str, port: int,
        field_lat: str = "lat", field_lon: str = "lon",
        field_alt: str = "alt", field_heading: str = "heading",
        field_speed: str = "speed",
    ):
        self.host = host
        self.port = port
        self.field_lat = field_lat
        self.field_lon = field_lon
        self.field_alt = field_alt
        self.field_heading = field_heading
        self.field_speed = field_speed
        self._sock: Optional[socket.socket] = None

    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((self.host, self.port))
        logger.info(
            "escuchando telemetria JSON en %s:%d (campos: lat=%s lon=%s alt=%s heading=%s speed=%s)",
            self.host, self.port, self.field_lat, self.field_lon,
            self.field_alt, self.field_heading, self.field_speed,
        )

    def read_next(self, timeout_seconds: float) -> Optional[TelemetrySample]:
        self._sock.settimeout(timeout_seconds)
        try:
            data, _ = self._sock.recvfrom(65535)
        except socket.timeout:
            return None

        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("paquete no es JSON valido, se descarta: %s", exc)
            return None

        if self.field_lat not in payload or self.field_lon not in payload:
            logger.warning(
                "paquete JSON sin campos %s/%s esperados -- payload keys: %s",
                self.field_lat, self.field_lon, list(payload.keys()),
            )
            return None

        return TelemetrySample(
            timestamp=datetime.now(timezone.utc),
            latitude=float(payload[self.field_lat]),
            longitude=float(payload[self.field_lon]),
            altitude=float(payload[self.field_alt]) if self.field_alt in payload else None,
            heading=float(payload[self.field_heading]) if self.field_heading in payload else None,
            speed=float(payload[self.field_speed]) if self.field_speed in payload else None,
            raw=payload,
        )

    def close(self) -> None:
        if self._sock:
            self._sock.close()
