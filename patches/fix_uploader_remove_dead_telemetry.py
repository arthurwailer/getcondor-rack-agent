with open('agent/uploader.py', 'r') as f:
    content = f.read()

dead_block = '''TELEMETRY_URL_PATH = "/telemetry/ingest"  # TODO: confirmar contra telemetry-svc real


def upload_telemetry(drone_id, timestamp, latitude, longitude, altitude=None, heading=None, speed=None) -> bool:
    """Sube una muestra de telemetria. Sin retry/backoff a proposito --
    una muestra perdida no vale la pena reintentar, la siguiente llega
    en segundos (a diferencia de upload_file, donde el archivo solo
    existe una vez)."""
    url = f"{GETCONDOR_API_URL}{TELEMETRY_URL_PATH}"
    payload = {
        "drone_id": drone_id,
        "token": MQTT_TOKEN,
        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latitude": latitude,
        "longitude": longitude,
    }
    if altitude is not None:
        payload["altitude"] = altitude
    if heading is not None:
        payload["heading"] = heading
    if speed is not None:
        payload["speed"] = speed

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("upload de telemetria fallo: %s", exc)
        return False
'''

assert dead_block in content, "dead block not found"
content = content.replace("\n" + dead_block, "\n", 1)

with open('agent/uploader.py', 'w') as f:
    f.write(content)
print("OK: uploader.py - dead HTTP telemetry code removed (telemetry now goes via MQTT)")
