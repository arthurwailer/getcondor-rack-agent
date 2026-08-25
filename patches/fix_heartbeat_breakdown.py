with open('agent/heartbeat.py', 'r') as f:
    content = f.read()

old = '''def _build_payload() -> dict:
    last_upload = state.get_last_upload_time()
    return {
        "drone_id": DRONE_ID,
        "token": MQTT_TOKEN,
        "reported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "uptime_seconds": int(time.time() - _started_at),
        "disk_free_gb": _disk_free_gb(os.getenv("MEDIA_WATCH_DIR", "/data/watch")),
        "last_upload_at": last_upload,
        "uploads_last_hour": state.count_uploaded_last_hour(),
    }'''
new = '''def _build_payload() -> dict:
    last_upload = state.get_last_upload_time()
    by_type = state.get_last_upload_time_by_type()
    return {
        "drone_id": DRONE_ID,
        "token": MQTT_TOKEN,
        "reported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "uptime_seconds": int(time.time() - _started_at),
        "disk_free_gb": _disk_free_gb(os.getenv("MEDIA_WATCH_DIR", "/data/watch")),
        "last_upload_at": last_upload,
        "uploads_last_hour": state.count_uploaded_last_hour(),
        "last_photo_at": by_type.get("PHOTO"),
        "last_video_at": by_type.get("VIDEO"),
        "last_geotiff_at": by_type.get("GEOTIFF_ZIP"),
    }'''

assert old in content, "pattern not found"
content = content.replace(old, new, 1)

with open('agent/heartbeat.py', 'w') as f:
    f.write(content)
print("OK: heartbeat.py - per-type last upload timestamps added")
