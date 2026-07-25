# getcondor-rack-agent

Agente que corre en cada rack Ubuntu a bordo (Shamrock-AddOn) y envia
media/telemetria hacia GetCondor, sin interrumpir el flujo existente a ArcGIS.

## Uso

```bash
cp config/.env.example config/.env
# completar DRONE_ID, GETCONDOR_API_KEY, MEDIA_WATCH_DIR
docker compose up -d --build
```

## Estructura

- `agent/watcher.py` - detecta archivos nuevos en la carpeta de media
- `agent/uploader.py` - sube a GetCondor con retry/backoff (Starlink flaky)
- `agent/geotiff.py` - descomprime .zip con GeoTIFF y sube como media_type=GEOTIFF
- `agent/telemetry.py` - reenvio de telemetria (pendiente)

## Pendiente
- Conectar watcher a la carpeta real del rack
- Definir formato de subida GeoTIFF
- Telemetria
