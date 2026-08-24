"""
Interfaz base para fuentes de telemetria. Agregar un tipo nuevo (MAVLink,
KLV, JSON propietario, NMEA, etc) = crear un archivo nuevo en este
paquete que implemente esta clase, y sumarlo a SOURCE_REGISTRY en
telemetry_orchestrator.py. No se toca ningun modulo existente.

Cada fuente produce un dict "canonico" -- mismo shape sin importar de
donde vino, igual que uploader.upload_file ya recibe latitude/longitude/
altitude/heading sin importar si el dato salio de UserComment EXIF, GPS
EXIF estandar, o un sidecar .dat.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, TypedDict


class TelemetrySample(TypedDict, total=False):
    timestamp: datetime          # UTC, requerido
    latitude: float              # requerido
    longitude: float             # requerido
    altitude: Optional[float]
    heading: Optional[float]
    speed: Optional[float]
    raw: Optional[dict]          # payload crudo, para debug/auditoria


class TelemetrySource(ABC):
    """Ciclo de vida: connect() una vez, read_next() en loop, close() al salir."""

    @abstractmethod
    def connect(self) -> None:
        ...

    @abstractmethod
    def read_next(self, timeout_seconds: float) -> Optional[TelemetrySample]:
        """Bloquea hasta timeout_seconds esperando el proximo dato. Devuelve
        None si no llego nada en ese tiempo (no es un error -- el caller
        simplemente reintenta)."""
        ...

    def close(self) -> None:
        pass
