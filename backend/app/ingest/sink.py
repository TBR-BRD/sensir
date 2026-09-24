"""Kanonische Ereignisse in die DB schreiben, auf einen Sensor gematcht."""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select

from app.db import session_scope
from app.ingest.normalize import CanonEvent
from app.models import Sensor, SensorEvent, SensorKind

logger = logging.getLogger(__name__)


def sensor_by_external(session, kind: SensorKind, external_id: str) -> Sensor | None:
    return session.execute(
        select(Sensor).where(Sensor.kind == kind, Sensor.external_id == str(external_id))
    ).scalar_one_or_none()


def record_events(
    kind: SensorKind,
    external_id: str,
    events: list[CanonEvent],
    raw: dict | None = None,
) -> int:
    """Schreibt SensorEvents für ein Cloud-Gerät. Gibt die Anzahl zurück.

    `Sensor.config["emergency"] = true` markiert einen Sensor (z. B. einen
    Notrufknopf) so, dass JEDES seiner Ereignisse als `safety=True` gilt,
    unabhängig vom erkannten `kind` (normalerweise nur Rauch/Gas). Für jedes
    so markierte Ereignis wird sofort ein Alarm ausgelöst - siehe
    `app.alerting.engine.send_immediate_safety_alert` - statt bis zum
    nächsten periodischen Scheduler-Tick zu warten (der würde für einen
    Notrufknopf bis zu CHECK_INTERVAL_MINUTES zu spät kommen).
    """
    if not events:
        return 0
    with session_scope() as session:
        sensor = sensor_by_external(session, kind, external_id)
        if sensor is None:
            logger.debug("Ereignis von unbekanntem %s-Gerät '%s' verworfen", kind.value, external_id)
            return 0
        if not sensor.is_active:
            return 0
        forced_safety = bool((sensor.config or {}).get("emergency"))
        now = dt.datetime.now(dt.timezone.utc)
        safety_alerts: list[tuple[int, str]] = []
        for ev in events:
            is_safety = ev.safety or forced_safety
            session.add(
                SensorEvent(
                    sensor_id=sensor.id,
                    received_at=now,
                    kind=ev.kind,
                    value=ev.value,
                    safety=is_safety,
                    raw_payload=raw or {},
                )
            )
            if is_safety:
                safety_alerts.append((sensor.id, ev.kind))
        sensor.last_seen_at = now
        session.commit()

    for sensor_id, ev_kind in safety_alerts:
        try:
            from app.alerting.engine import send_immediate_safety_alert

            send_immediate_safety_alert(sensor_id, ev_kind, now)
        except Exception:  # noqa: BLE001
            logger.exception("Sofort-Alarm für Sensor %s fehlgeschlagen", sensor_id)

    return len(events)
