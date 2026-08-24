with open('agent/photo.py', 'r') as f:
    content = f.read()

old = '''def parse_exif_usercomment(image_path: Path) -> dict:'''
new = '''def _dms_to_decimal(dms, ref: str) -> float:
    degrees, minutes, seconds = dms
    value = float(degrees) + float(minutes) / 60 + float(seconds) / 3600
    if ref in ("S", "W"):
        value = -value
    return value


def parse_standard_exif_gps(image_path: Path) -> dict:
    """Fallback cuando la foto no trae el UserComment custom de
    metaOSD.py (camara distinta, o UserComment ausente): lee los tags
    GPS EXIF estandar (GPSLatitude/GPSLongitude/GPSAltitude/
    GPSImgDirection), que la mayoria de camaras si escribe de forma
    nativa. Devuelve {} si no hay GPS EXIF tampoco -- nunca se inventan
    coordenadas."""
    from PIL.ExifTags import TAGS, GPSTAGS
    try:
        img = Image.open(image_path)
        raw = img._getexif()
        if not raw:
            return {}
        exif = {TAGS.get(k, k): v for k, v in raw.items()}
        gps_info = exif.get("GPSInfo")
        if not gps_info:
            return {}
        gps = {GPSTAGS.get(k, k): v for k, v in gps_info.items()}
        result = {}
        if "GPSLatitude" in gps and "GPSLatitudeRef" in gps:
            result["LATITUDE"] = _dms_to_decimal(gps["GPSLatitude"], gps["GPSLatitudeRef"])
        if "GPSLongitude" in gps and "GPSLongitudeRef" in gps:
            result["LONGITUDE"] = _dms_to_decimal(gps["GPSLongitude"], gps["GPSLongitudeRef"])
        if "GPSAltitude" in gps:
            try:
                result["ALTITUDE"] = float(gps["GPSAltitude"])
            except (TypeError, ValueError):
                pass
        if "GPSImgDirection" in gps:
            try:
                result["FRAME_HEADING"] = float(gps["GPSImgDirection"])
            except (TypeError, ValueError):
                pass
        return result
    except Exception as exc:
        logger.warning("no se pudo leer EXIF GPS estandar de %s: %s", image_path.name, exc)
        return {}


def parse_exif_usercomment(image_path: Path) -> dict:'''

assert old in content, "pattern not found"
content = content.replace(old, new, 1)

old2 = '''    meta = parse_exif_usercomment(path)
    mission_id = find_mission_label(path, f"Oscar{drone_id[-2:]}")'''
new2 = '''    meta = parse_exif_usercomment(path)
    if not meta:
        # No hay UserComment de metaOSD.py -- cae a GPS EXIF estandar
        # en vez de subir la foto sin coordenadas.
        meta = parse_standard_exif_gps(path)
    mission_id = find_mission_label(path, f"Oscar{drone_id[-2:]}")'''

assert old2 in content, "pattern2 not found"
content = content.replace(old2, new2, 1)

with open('agent/photo.py', 'w') as f:
    f.write(content)
print("OK: photo.py - fallback a GPS EXIF estandar cuando falta UserComment")
