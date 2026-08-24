with open('agent/photo.py', 'r') as f:
    content = f.read()

old = '''def find_drone_id(path: Path) -> Optional[str]:
    """Busca un componente tipo 'OscarNN' en cualquier parte del path."""
    for part in path.parts:
        m = DRONE_RE.match(part)
        if m:
            return f"OSCAR{m.group(1)}"
    return None'''
new = '''def find_drone_id(path: Path) -> Optional[str]:
    """Busca un componente tipo 'OscarNN' en cualquier parte del path
    (estructura que usa shamrock-adc, donde Insync junta varios OSCAR en
    un mismo arbol). Si no lo encuentra -- caso normal corriendo DENTRO
    de un rack individual, donde todo lo que hay en la carpeta ya es de
    ese avion -- cae al DRONE_ID fijo del entorno en vez de descartar
    el archivo."""
    for part in path.parts:
        m = DRONE_RE.match(part)
        if m:
            return f"OSCAR{m.group(1)}"
    env_drone_id = os.getenv("DRONE_ID")
    return env_drone_id if env_drone_id and env_drone_id != "UNKNOWN" else None'''
assert old in content, "old block not found"
content = content.replace(old, new, 1)

# necesita import os
old_import = "import json\nimport logging\nimport re"
new_import = "import json\nimport logging\nimport os\nimport re"
assert old_import in content, "import block not found"
content = content.replace(old_import, new_import, 1)

with open('agent/photo.py', 'w') as f:
    f.write(content)
print("OK: find_drone_id ahora cae al DRONE_ID del entorno si no hay subcarpeta OscarNN")
