"""Startet/stoppt die je nach Konfiguration aktiven Ingest-Quellen."""

from __future__ import annotations

import logging

from app.config import settings
from app.ingest.base import Source

logger = logging.getLogger(__name__)

_sources: list[Source] = []


def start_all() -> None:
    global _sources
    _sources = []

    if settings.mqtt_enabled:
        from app.ingest.mqtt_ir import MqttIrSource

        _sources.append(MqttIrSource())

    if settings.tuya_enabled and settings.tuya_access_id:
        from app.ingest.tuya import TuyaSource

        _sources.append(TuyaSource())

    if settings.shelly_enabled and settings.shelly_auth_key:
        from app.ingest.shelly import ShellySource

        _sources.append(ShellySource())

    for src in _sources:
        try:
            src.start()
        except Exception:  # noqa: BLE001
            logger.exception("Quelle '%s' konnte nicht starten", src.name)

    logger.info("Ingest-Quellen aktiv: %s", [s.name for s in _sources] or "keine")


def stop_all() -> None:
    for src in _sources:
        try:
            src.stop()
        except Exception:  # noqa: BLE001
            pass
