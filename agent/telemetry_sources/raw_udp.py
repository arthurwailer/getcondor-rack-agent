"""
Fuente de telemetria "raw": escucha el puerto UDP y NO intenta parsear
nada -- solo loguea cada paquete crudo (hex + intento de decode utf-8)
a nivel INFO. Sirve para dos cosas:

1) Verificar HOY que el rack recibe datos en ese puerto/red, sin tener
   el gimbal/autopiloto al lado para inspeccionar el protocolo.
2) Capturar muestras reales una vez este conectado al hardware, para
   diseñar el parser definitivo (mavlink_udp.py, generic_json_udp.py,
   o uno nuevo) con datos reales en vez de adivinar.

No sube nada a GetCondor -- read_next() siempre devuelve None, asi que
telemetry_orchestrator.py no intenta subir "telemetria" sin sentido.
Es deliberado: esta fuente es de diagnostico, no de produccion.
"""

from __future__ import annotations
import logging
import socket
from typing import Optional

from agent.telemetry_sources.base import TelemetrySource, TelemetrySample

logger = logging.getLogger("telemetry.raw")


class RawUDPSource(TelemetrySource):
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None

    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((self.host, self.port))
        logger.info(
            "escuchando UDP en %s:%d (modo diagnostico -- no sube nada, "
            "solo loguea paquetes crudos para diseñar el parser real)",
            self.host, self.port,
        )

    def read_next(self, timeout_seconds: float) -> Optional[TelemetrySample]:
        self._sock.settimeout(timeout_seconds)
        try:
            data, addr = self._sock.recvfrom(65535)
        except socket.timeout:
            return None

        try:
            as_text = data.decode("utf-8")
        except UnicodeDecodeError:
            as_text = None

        logger.info(
            "paquete recibido de %s (%d bytes) -- hex[:80]=%s%s",
            addr, len(data), data[:80].hex(),
            f" texto={as_text!r}" if as_text else "",
        )
        return None  # diagnostico puro -- no produce telemetria "real"

    def close(self) -> None:
        if self._sock:
            self._sock.close()
