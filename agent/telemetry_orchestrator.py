"""
Telemetry orchestrator, store-and-forward architecture.

Two independent loops:

- WRITER: reads samples from the configured source (raw/mavlink/
  generic_json) and immediately persists them to telemetry_queue.py's
  durable sqlite queue. Never touches the network. A source read that
  succeeds is never lost, even if MQTT/Starlink is down at that moment.

- SENDER: runs in its own thread, continuously drains the queue oldest-
  first over MQTT, and only removes a row once the broker has ACTUALLY
  CONFIRMED delivery via paho's on_publish callback (QoS 1 PUBACK) --
  not merely because client.publish() didn't raise. A row that isn't
  confirmed within a timeout is left in the queue and retried on the
  next pass, with exponential backoff on repeated connection failures.

This mirrors the industry-standard "store-and-forward" pattern used in
telemetry systems built for unreliable links (satellite, cellular in
motion, etc): the local disk is the source of truth for "sent or not",
never the network call's return value.
"""

import logging
import os
import threading
import time

import paho.mqtt.client as mqtt

import telemetry_pb2 as pb
import telemetry_queue as tq
from agent.telemetry_sources.base import TelemetrySource
from agent.telemetry_sources.raw_udp import RawUDPSource
from agent.telemetry_sources.mavlink_udp import MAVLinkUDPSource
from agent.telemetry_sources.generic_json_udp import GenericJSONUDPSource

logger = logging.getLogger("telemetry")

MQTT_BROKER = os.getenv("MQTT_BROKER_HOST", "getcondor.win")
MQTT_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
DRONE_ID = os.getenv("DRONE_ID", "UNKNOWN")
MQTT_TOKEN = os.getenv("MQTT_TOKEN", "")

PUBLISH_CONFIRM_TIMEOUT = float(os.getenv("TELEMETRY_PUBLISH_TIMEOUT", "10"))
SENDER_BATCH_SIZE = int(os.getenv("TELEMETRY_SENDER_BATCH_SIZE", "20"))
SENDER_IDLE_SLEEP = float(os.getenv("TELEMETRY_SENDER_IDLE_SLEEP", "1"))
SENDER_MAX_BACKOFF = float(os.getenv("TELEMETRY_SENDER_MAX_BACKOFF", "60"))

SOURCE_REGISTRY = {
    "raw": lambda: RawUDPSource(
        host=os.getenv("TELEMETRY_UDP_HOST", "0.0.0.0"),
        port=int(os.getenv("TELEMETRY_UDP_PORT", "14550")),
    ),
    "mavlink": lambda: MAVLinkUDPSource(
        host=os.getenv("TELEMETRY_UDP_HOST", "0.0.0.0"),
        port=int(os.getenv("TELEMETRY_UDP_PORT", "14550")),
    ),
    "generic_json": lambda: GenericJSONUDPSource(
        host=os.getenv("TELEMETRY_UDP_HOST", "0.0.0.0"),
        port=int(os.getenv("TELEMETRY_UDP_PORT", "14550")),
        field_lat=os.getenv("TELEMETRY_JSON_FIELD_LAT", "lat"),
        field_lon=os.getenv("TELEMETRY_JSON_FIELD_LON", "lon"),
        field_alt=os.getenv("TELEMETRY_JSON_FIELD_ALT", "alt"),
        field_heading=os.getenv("TELEMETRY_JSON_FIELD_HEADING", "heading"),
        field_speed=os.getenv("TELEMETRY_JSON_FIELD_SPEED", "speed"),
    ),
}


def _build_source() -> TelemetrySource:
    source_type = os.getenv("TELEMETRY_SOURCE_TYPE", "raw")
    factory = SOURCE_REGISTRY.get(source_type)
    if factory is None:
        raise RuntimeError(
            f"TELEMETRY_SOURCE_TYPE='{source_type}' unknown -- options: {list(SOURCE_REGISTRY.keys())}"
        )
    return factory()


def _sample_to_protobuf(sample, mission_id: str) -> bytes:
    """Mirrors tools/rpi-agent/agent.py build_telemetry() field mapping.
    battery.* is ALWAYS set (even to 0.0) -- telemetry-svc dereferences
    it without a nil check and crashes the whole service otherwise
    (confirmed in testing: it took down telemetry-svc for every drone,
    not just this one, until Docker auto-restarted it)."""
    msg = pb.TelemetryMessage()
    msg.drone_id = DRONE_ID
    msg.mission_id = mission_id
    msg.timestamp = int(sample["timestamp"].timestamp() * 1000)
    msg.position.latitude = sample["latitude"]
    msg.position.longitude = sample["longitude"]
    if sample.get("altitude") is not None:
        msg.position.altitude = sample["altitude"]
    if sample.get("heading") is not None:
        msg.position.heading = sample["heading"]
    if sample.get("speed") is not None:
        msg.position.speed = sample["speed"]
    msg.status.state = pb.FLYING
    msg.battery.percentage = sample.get("battery_pct") or 0.0
    msg.battery.voltage = sample.get("battery_voltage") or 0.0
    return msg.SerializeToString()


def _writer_loop(source: TelemetrySource, mission_id: str, stop_event: threading.Event) -> None:
    """Reads from the hardware source and durably enqueues -- never
    touches the network. This loop's only failure mode is the source
    itself misbehaving; it is completely decoupled from connectivity."""
    logger.info("telemetry writer started -- source=%s", os.getenv("TELEMETRY_SOURCE_TYPE", "raw"))
    while not stop_event.is_set():
        try:
            sample = source.read_next(timeout_seconds=5.0)
        except Exception:
            logger.exception("error reading from telemetry source -- retrying")
            time.sleep(2)
            continue

        if sample is None:
            continue

        try:
            payload = _sample_to_protobuf(sample, mission_id)
            tq.enqueue(sample["timestamp"], payload)
        except Exception:
            logger.exception("failed to enqueue telemetry sample -- sample dropped (this is a bug, not a network issue)")


def _sender_loop(stop_event: threading.Event) -> None:
    """Drains the durable queue to MQTT, oldest first, only removing a
    row once the broker confirms delivery. Reconnects with exponential
    backoff when the link is down; the queue just keeps growing on
    disk (bounded by TELEMETRY_QUEUE_MAX_ROWS) until it can drain."""
    topic = f"drones/{DRONE_ID}/telemetry"
    backoff = 5.0

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{DRONE_ID}-telemetry")
    client.username_pw_set(DRONE_ID, MQTT_TOKEN)

    connected = threading.Event()
    pending_confirms = {}
    pending_confirms_lock = threading.Lock()

    def on_connect(c, userdata, flags, rc, properties=None):
        rc_code = rc.value if hasattr(rc, "value") else rc
        if rc_code == 0:
            logger.info("telemetry sender: MQTT connected")
            connected.set()
        else:
            logger.error("telemetry sender: MQTT connect failed, code=%s", rc_code)
            connected.clear()

    def on_disconnect(c, userdata, flags, rc, properties=None):
        logger.warning("telemetry sender: MQTT disconnected (rc=%s) -- queue keeps growing until reconnect", rc)
        connected.clear()

    def on_publish(c, userdata, mid, reason_codes=None, properties=None):
        with pending_confirms_lock:
            ev = pending_confirms.get(mid)
            if ev is not None:
                ev.set()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_publish = on_publish

    while not stop_event.is_set():
        try:
            client.connect(MQTT_BROKER, MQTT_PORT, 60)
            client.loop_start()
        except Exception:
            wait = min(backoff, SENDER_MAX_BACKOFF)
            logger.warning("telemetry sender: connect() raised, retrying in %.0fs", wait)
            time.sleep(wait)
            backoff = min(backoff * 2, SENDER_MAX_BACKOFF)
            continue

        if not connected.wait(timeout=15):
            wait = min(backoff, SENDER_MAX_BACKOFF)
            logger.warning("telemetry sender: no connection after 15s, retrying in %.0fs", wait)
            client.loop_stop()
            time.sleep(wait)
            backoff = min(backoff * 2, SENDER_MAX_BACKOFF)
            continue

        backoff = 5.0

        while not stop_event.is_set() and connected.is_set():
            rows = tq.peek_oldest(SENDER_BATCH_SIZE)
            if not rows:
                time.sleep(SENDER_IDLE_SLEEP)
                continue

            for row_id, captured_at, payload in rows:
                if stop_event.is_set() or not connected.is_set():
                    break
                confirm_event = threading.Event()
                with pending_confirms_lock:
                    pending_confirms[row_id] = confirm_event
                try:
                    info = client.publish(topic, payload, qos=1)
                    with pending_confirms_lock:
                        pending_confirms[info.mid] = confirm_event
                except Exception:
                    logger.exception("telemetry sender: publish() raised for row %d -- left in queue", row_id)
                    tq.mark_attempt(row_id)
                    break

                tq.mark_attempt(row_id)
                if confirm_event.wait(timeout=PUBLISH_CONFIRM_TIMEOUT):
                    tq.mark_sent(row_id)
                else:
                    logger.warning(
                        "telemetry sender: no PUBACK for row %d within %.0fs -- left in queue, will retry",
                        row_id, PUBLISH_CONFIRM_TIMEOUT,
                    )
                with pending_confirms_lock:
                    pending_confirms.pop(row_id, None)

        client.loop_stop()
        try:
            client.disconnect()
        except Exception:
            pass


def run() -> None:
    source = _build_source()
    mission_id = os.getenv("MISSION_ID", "")
    stop_event = threading.Event()

    while True:
        try:
            source.connect()
            break
        except Exception:
            logger.exception("failed connecting telemetry source -- retrying in 10s")
            time.sleep(10)

    sender_thread = threading.Thread(target=_sender_loop, args=(stop_event,), daemon=True)
    sender_thread.start()

    try:
        _writer_loop(source, mission_id, stop_event)
    finally:
        stop_event.set()
        source.close()
        sender_thread.join(timeout=5)
