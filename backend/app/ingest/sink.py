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
    """Schreibt SensorEvents für ein Cloud-Gerät. Gibt die Anzahl zurück."""
    if not events:
        return 0
    with session_scope() as session:
        sensor = sensor_by_external(session, kind, external_id)
        if sensor is None:
            logger.debug("Ereignis von unbekanntem %s-Gerät '%s' verworfen", kind.value, external_id)
            return 0
        if not sensor.is_active:
            return 0
        now = dt.datetime.now(dt.timezone.utc)
        for ev in events:
            session.add(
                SensorEvent(
                    sensor_id=sensor.id,
                    received_at=now,
                    kind=ev.kind,
                    value=ev.value,
                    safety=ev.safety,
                    raw_payload=raw or {},
                )
            )
        sensor.last_seen_at = now
        session.commit()
        return len(events)
