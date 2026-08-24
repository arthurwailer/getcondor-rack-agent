with open('agent/main.py', 'r') as f:
    content = f.read()

old = '''    telemetry_enabled = os.getenv("TELEMETRY_ENABLED", "false").lower() == "true"
    if telemetry_enabled:
        logger.info("telemetria habilitada (aun no implementada)")'''
new = '''    telemetry_enabled = os.getenv("TELEMETRY_ENABLED", "false").lower() == "true"
    if telemetry_enabled:
        import threading
        from agent import telemetry_orchestrator
        threading.Thread(target=telemetry_orchestrator.run, daemon=True).start()
        logger.info("telemetria habilitada, corriendo en background")'''

assert old in content, "pattern not found"
content = content.replace(old, new, 1)

with open('agent/main.py', 'w') as f:
    f.write(content)
print("OK: main.py - telemetry_orchestrator conectado en background thread")
