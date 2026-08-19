"""Subscribes to the Tasmota IR bridges' result topics and persists every
received IR signal as an IrEvent, matched to a Sensor by its MQTT base topic.

Runs paho-mqtt's own network loop in a background thread (loop_start), kept
separate from FastAPI's asyncio event loop since each message triggers a
synchronous DB write - not worth the complexity of an async MQTT client at
this scale (a handful of sensors, low message rate).
"""

import json
import logging

import paho.mqtt.client as mqtt
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import IrEvent, Sensor

logger = logging.getLogger(__name__)


def _topic_base(topic: str) -> str:
    # "tele/sensir-01/RESULT" -> "sensir-01"
    parts = topic.split("/")
    return parts[1] if len(parts) >= 3 else topic


def _handle_message(topic: str, payload: bytes) -> None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        logger.debug("ignoring non-JSON payload on %s", topic)
        return

    ir = data.get("IrReceived")
    if ir is None:
        return  # RESULT messages also carry other Tasmota state, not just IR

    sensor_topic = _topic_base(topic)
    with session_scope() as session:
        sensor = session.execute(select(Sensor).where(Sensor.mqtt_topic == sensor_topic)).scalar_one_or_none()
        if sensor is None:
            logger.warning("IR event from unregistered sensor topic '%s', dropping", sensor_topic)
            return
        if not sensor.is_active:
            return

        session.add(
            IrEvent(
                sensor_id=sensor.id,
                protocol=ir.get("Protocol"),
                bits=ir.get("Bits"),
                data_hex=ir.get("Data"),
                raw_payload=ir,
            )
        )
        session.commit()


def _on_connect(client: mqtt.Client, userdata, flags, reason_code, properties=None) -> None:
    if reason_code != 0:
        logger.error("MQTT connect failed: %s", reason_code)
        return
    for topic in settings.mqtt_topics:
        client.subscribe(topic)
        logger.info("subscribed to %s", topic)


def _on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    try:
        _handle_message(msg.topic, msg.payload)
    except Exception:
        logger.exception("failed to process message on %s", msg.topic)


def build_client() -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
    client.on_connect = _on_connect
    client.on_message = _on_message
    return client


_client: mqtt.Client | None = None


def start() -> None:
    global _client
    _client = build_client()
    _client.connect(settings.mqtt_host, settings.mqtt_port)
    _client.loop_start()
    logger.info("MQTT listener started (%s:%s)", settings.mqtt_host, settings.mqtt_port)


def stop() -> None:
    if _client is not None:
        _client.loop_stop()
        _client.disconnect()
