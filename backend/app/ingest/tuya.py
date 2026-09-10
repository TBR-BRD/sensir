"""Tuya-Cloud-Quelle: Status-Polling + Pulsar-Echtzeit-Stream."""

from __future__ import annotations

import json
import logging
import threading

from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.ingest.base import PollingSource
from app.ingest.normalize import normalize_tuya
from app.ingest.sink import record_events
from app.models import Sensor, SensorKind

logger = logging.getLogger(__name__)

try:
    from tuya_connector import TuyaOpenAPI, TuyaOpenPulsar, TuyaCloudPulsarTopic
except ImportError:  # pragma: no cover
    TuyaOpenAPI = TuyaOpenPulsar = TuyaCloudPulsarTopic = None  # type: ignore


class TuyaSource(PollingSource):
    name = "tuya"

    def __init__(self) -> None:
        super().__init__()
        self.poll_interval = float(settings.source_poll_interval_seconds)
        self._api = None
        self._pulsar = None
        self._plug_state: dict[str, bool] = {}

    # -- Verbindung ---------------------------------------------------
    def connect(self) -> None:
        if TuyaOpenAPI is None:
            raise RuntimeError("tuya-connector-python nicht installiert")
        self._api = TuyaOpenAPI(
            settings.tuya_api_base, settings.tuya_access_id, settings.tuya_access_secret
        )
        self._api.connect()
        logger.info("Tuya-API verbunden (%s)", settings.tuya_api_base)

    def start(self) -> None:
        super().start()
        if settings.tuya_pulsar_enabled and TuyaOpenPulsar is not None:
            self._start_pulsar()

    def stop(self) -> None:
        super().stop()
        if self._pulsar is not None:
            try:
                self._pulsar.stop()
            except Exception:  # noqa: BLE001
                pass

    # -- Polling ------------------------------------------------------
    def _tuya_sensors(self) -> list[Sensor]:
        with session_scope() as session:
            return list(
                session.execute(
                    select(Sensor).where(
                        Sensor.kind == SensorKind.tuya, Sensor.is_active.is_(True)
                    )
                ).scalars().all()
            )

    def poll_once(self) -> None:
        if self._api is None:
            return
        for sensor in self._tuya_sensors():
            if not sensor.external_id:
                continue
            resp = self._api.get(f"/v1.0/devices/{sensor.external_id}/status")
            if not resp.get("success"):
                logger.debug("Tuya-Status %s: %s", sensor.external_id, resp.get("msg"))
                continue
            status_list = resp.get("result", [])
            self._ingest(sensor, status_list, {"source": "poll", "status": status_list})

    def _ingest(self, sensor: Sensor, status_list: list[dict], raw: dict) -> None:
        threshold = float((sensor.config or {}).get("on_threshold_w", 10.0))
        events = normalize_tuya(
            status_list,
            on_threshold_w=threshold,
            plug_state=self._plug_state,
            key=str(sensor.external_id),
        )
        if events:
            record_events(SensorKind.tuya, str(sensor.external_id), events, raw)

    # -- Pulsar-Stream --------------------------------------------------
    def _start_pulsar(self) -> None:
        self._pulsar = TuyaOpenPulsar(
            settings.tuya_access_id,
            settings.tuya_access_secret,
            settings.tuya_pulsar_url,
            TuyaCloudPulsarTopic.PROD,
        )
        self._pulsar.add_message_listener(self._on_pulsar)
        threading.Thread(target=self._pulsar.start, name="tuya-pulsar", daemon=True).start()
        logger.info("Tuya-Pulsar-Stream gestartet")

    def _on_pulsar(self, msg) -> None:
        try:
            payload = msg if isinstance(msg, dict) else json.loads(msg)
            data = payload.get("data", payload)
            dev_id = data.get("devId") or data.get("device_id")
            status = data.get("status") or []
            if not dev_id:
                return
            with session_scope() as session:
                sensor = session.execute(
                    select(Sensor).where(
                        Sensor.kind == SensorKind.tuya, Sensor.external_id == str(dev_id)
                    )
                ).scalar_one_or_none()
                threshold = float((sensor.config or {}).get("on_threshold_w", 10.0)) if sensor else 10.0
            events = normalize_tuya(
                status, on_threshold_w=threshold, plug_state=self._plug_state, key=str(dev_id)
            )
            if events:
                record_events(SensorKind.tuya, str(dev_id), events, payload)
        except Exception:  # noqa: BLE001
            logger.debug("Tuya-Pulsar-Nachricht nicht verwertbar", exc_info=True)


# -- Discovery-Helfer für die API -------------------------------------
def list_cloud_devices() -> list[dict]:
    if TuyaOpenAPI is None:
        raise RuntimeError("tuya-connector-python nicht installiert")
    api = TuyaOpenAPI(
        settings.tuya_api_base, settings.tuya_access_id, settings.tuya_access_secret
    )
    api.connect()
    uid = settings.tuya_app_account_uid
    resp = api.get(f"/v1.0/users/{uid}/devices")
    devices = resp.get("result", []) if resp.get("success") else []
    return [
        {
            "external_id": d.get("id"),
            "name": d.get("name"),
            "category": d.get("category"),
            "product_name": d.get("product_name"),
            "online": d.get("online"),
        }
        for d in devices
    ]
