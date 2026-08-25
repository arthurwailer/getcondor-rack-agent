with open('agent/telemetry_orchestrator.py', 'r') as f:
    content = f.read()

old = '''    msg.status.state = pb.FLYING
    return msg.SerializeToString()'''
new = '''    msg.status.state = pb.FLYING
    # telemetry-svc dereferences msg.Battery.* directly without a nil
    # check, so this submessage MUST always be set -- an unset Battery
    # field crashes the whole service (confirmed: it killed telemetry-svc
    # for every drone during testing, docker restarted it automatically).
    # We don't have real battery data from this source yet, so 0 is an
    # honest "unknown" placeholder, not a fabricated reading.
    msg.battery.percentage = sample.get("battery_pct") or 0.0
    msg.battery.voltage = sample.get("battery_voltage") or 0.0
    return msg.SerializeToString()'''

assert old in content, "pattern not found"
content = content.replace(old, new, 1)

with open('agent/telemetry_orchestrator.py', 'w') as f:
    f.write(content)
print("OK: telemetry_orchestrator.py - battery submessage always populated to prevent telemetry-svc crash")
