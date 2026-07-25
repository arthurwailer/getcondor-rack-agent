"""
Entry point del rack agent.
TODO: arrancar watcher.py (media/geotiff) y telemetry.py segun config.
"""

import os

if __name__ == "__main__":
    drone_id = os.getenv("DRONE_ID", "UNKNOWN")
    print(f"[getcondor-rack-agent] arrancando para drone_id={drone_id} (placeholder)")
