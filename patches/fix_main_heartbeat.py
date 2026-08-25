with open('agent/main.py', 'r') as f:
    content = f.read()

old = '''    drone_id = os.getenv("DRONE_ID", "UNKNOWN")
    logger.info("getcondor-rack-agent arrancando para drone_id=%s", drone_id)'''
new = '''    drone_id = os.getenv("DRONE_ID", "UNKNOWN")
    logger.info("getcondor-rack-agent arrancando para drone_id=%s", drone_id)

    import threading
    from agent import heartbeat
    threading.Thread(target=heartbeat.run, daemon=True).start()'''

assert old in content, "pattern not found"
content = content.replace(old, new, 1)

with open('agent/main.py', 'w') as f:
    f.write(content)
print("OK: main.py - heartbeat wired in as background thread")
