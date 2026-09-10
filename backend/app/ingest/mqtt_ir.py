"""IR-Bridge-Quelle: Tasmota-RESULT-Topics -> SensorEvent(kind="ir").

paho-mqtt läuft in einem eigenen Netzwerk-Thread (loop_start), getrennt vom
asyncio-Loop von FastAPI. Jede Nachricht löst einen synchronen DB-Write aus –
bei der Sensormenge ist ein async-MQTT-Client den Aufwand nicht wert.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import paho.mqtt.client as mqtt
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.ingest.base import Source
from app.models import Sensor, SensorEvent, SensorKind

logger = logging.getLogger(__name__)


def _topic_base(topic: str) -> str:
    parts = topic.split("/")
    return parts[1] if len(parts) >= 3 else topic


def _handle_message(topic: str, payload: bytes) -> None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return
    ir = data.get("IrReceived")
    if ir is None:
        return

    sensor_topic = _topic_base(topic)
    with session_scope() as session:
        sensor = session.execute(
            select(Sensor).where(
                Sensor.mqtt_topic == sensor_topic,
                Sensor.kind == SensorKind.ir_bridge,
            )
        ).scalar_one_or_none()
        if sensor is None:
            logger.warning("IR-Ereignis von unregistriertem Topic '%s' verworfen", sensor_topic)
            return
        if not sensor.is_active:
            return
        now = dt.datetime.now(dt.timezone.utc)
        session.add(
            SensorEvent(
                sensor_id=sensor.id,
                received_at=now,
                kind="ir",
                protocol=ir.get("Protocol"),
                bits=ir.get("Bits"),
                data_hex=ir.get("Data"),
                raw_payload=ir,
            )
        )
        sensor.last_seen_at = now
        session.commit()


class MqttIrSource(Source):
    name = "mqtt-ir"

    def __init__(self) -> None:
        self._client: mqtt.Client | None = None

    def start(self) -> None:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.connect(settings.mqtt_host, settings.mqtt_port)
        client.loop_start()
        self._client = client
        logger.info("Ingest-Quelle 'mqtt-ir' verbunden (%s:%s)", settings.mqtt_host, settings.mqtt_port)

    def stop(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()

    @staticmethod
    def _on_connect(client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code != 0:
            logger.error("MQTT connect failed: %s", reason_code)
            return
        for topic in settings.mqtt_topics:
            client.subscribe(topic)
            logger.info("subscribed to %s", topic)

    @staticmethod
    def _on_message(client, userdata, msg: mqtt.MQTTMessage) -> None:
        try:
            _handle_message(msg.topic, msg.payload)
        except Exception:  # noqa: BLE001
            logger.exception("MQTT-Nachricht auf %s fehlgeschlagen", msg.topic)
