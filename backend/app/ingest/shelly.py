"""Shelly-Cloud-Quelle: Status-Polling + Cloud-WebSocket-Echtzeit.

Shelly-Cloud-Konto -> Einstellungen -> "Autorisierungs-Cloud-Key". Dort steht
auch der Server-Host (z. B. https://shelly-59-eu.shelly.cloud) fuer
SHELLY_API_HOST.
"""

from __future__ import annotations

import json
import logging
import threading
import time

import httpx
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.ingest.base import PollingSource
from app.ingest.normalize import normalize_shelly
from app.ingest.sink import record_events
from app.models import Sensor, SensorKind

logger = logging.getLogger(__name__)


class ShellySource(PollingSource):
    name = "shelly"

    def __init__(self) -> None:
        super().__init__()
        self.poll_interval = float(settings.source_poll_interval_seconds)
        self._plug_state: dict[str, bool] = {}
        self._ws_thread: threading.Thread | None = None
        self._ws_stop = threading.Event()

    def start(self) -> None:
        super().start()
        if settings.shelly_ws_enabled:
            self._ws_stop.clear()
            self._ws_thread = threading.Thread(
                target=self._ws_loop, name="shelly-ws", daemon=True
            )
            self._ws_thread.start()

    def stop(self) -> None:
        super().stop()
        self._ws_stop.set()

    # -- Polling ------------------------------------------------------
    def _shelly_sensors(self) -> list[Sensor]:
        with session_scope() as session:
            return list(
                session.execute(
                    select(Sensor).where(
                        Sensor.kind == SensorKind.shelly, Sensor.is_active.is_(True)
                    )
                ).scalars().all()
            )

    def poll_once(self) -> None:
        url = f"{settings.shelly_api_host.rstrip('/')}/device/status"
        for i, sensor in enumerate(self._shelly_sensors()):
            if not sensor.external_id:
                continue
            if i:
                # Shellys Cloud-API limitiert Requests pro Sekunde recht knapp;
                # ohne Pause zwischen den Sensoren kommen sofortige 429er.
                # 0.5s reichte im Live-Test nicht zuverlässig, 1.1s schon.
                time.sleep(1.1)
            try:
                r = httpx.post(
                    url,
                    data={"id": sensor.external_id, "auth_key": settings.shelly_auth_key},
                    timeout=15.0,
                )
                if r.status_code == 429:
                    # Einmaliger Retry nach kurzer Pause statt den Sensor für
                    # diesen ganzen Poll-Zyklus zu überspringen.
                    time.sleep(2.0)
                    r = httpx.post(
                        url,
                        data={"id": sensor.external_id, "auth_key": settings.shelly_auth_key},
                        timeout=15.0,
                    )
                r.raise_for_status()
                body = r.json()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Shelly-Status %s: %s", sensor.external_id, exc)
                continue
            status = (body.get("data") or {}).get("device_status") or {}
            self._ingest(str(sensor.external_id), status, sensor, body)

    def _ingest(self, external_id: str, status: dict, sensor: Sensor | None, raw: dict) -> None:
        threshold = float((sensor.config or {}).get("on_threshold_w", 10.0)) if sensor else 10.0
        events = normalize_shelly(
            status, on_threshold_w=threshold, plug_state=self._plug_state, key=external_id
        )
        if events:
            record_events(SensorKind.shelly, external_id, events, raw)

    # -- WebSocket ---------------------------------------------------
    def _ws_loop(self) -> None:
        try:
            import websocket  # websocket-client
        except ImportError:
            logger.info("websocket-client fehlt – Shelly nur per Polling")
            return
        ws_url = (
            settings.shelly_api_host.replace("https://", "wss://").rstrip("/")
            + f"/shelly/wss/hk/events?t={settings.shelly_auth_key}"
        )
        while not self._ws_stop.is_set():
            try:
                ws = websocket.create_connection(ws_url, timeout=20)
                logger.info("Shelly-Cloud-WebSocket verbunden")
                while not self._ws_stop.is_set():
                    raw = ws.recv()
                    if not raw:
                        break
                    self._on_ws_message(raw)
                ws.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Shelly-WS: %s", exc)
                self._ws_stop.wait(15)

    def _on_ws_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
            dev_id = str(
                msg.get("device", {}).get("id")
                or msg.get("deviceId")
                or msg.get("id")
                or ""
            )
            status = msg.get("status") or msg.get("data", {}).get("device_status") or {}
            if not dev_id or not status:
                return
            with session_scope() as session:
                sensor = session.execute(
                    select(Sensor).where(
                        Sensor.kind == SensorKind.shelly, Sensor.external_id == dev_id
                    )
                ).scalar_one_or_none()
            self._ingest(dev_id, status, sensor, msg)
        except Exception:  # noqa: BLE001
            logger.debug("Shelly-WS-Nachricht nicht verwertbar", exc_info=True)


# -- Discovery-Helfer -------------------------------------------------
def list_cloud_devices() -> list[dict]:
    url = f"{settings.shelly_api_host.rstrip('/')}/interface/device/list"
    r = httpx.post(url, data={"auth_key": settings.shelly_auth_key}, timeout=20.0)
    r.raise_for_status()
    body = r.json()
    devices = (body.get("data") or {}).get("devices") or body.get("devices") or {}
    if isinstance(devices, dict):
        items = [{"external_id": k, **(v if isinstance(v, dict) else {})} for k, v in devices.items()]
    else:
        items = list(devices)
    return [
        {
            "external_id": d.get("external_id") or d.get("id"),
            "name": d.get("name") or d.get("label"),
            "type": d.get("type") or d.get("cloud_type"),
            "online": d.get("online"),
        }
        for d in items
    ]
