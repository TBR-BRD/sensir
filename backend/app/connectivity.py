"""Erreichbarkeit der Cloud-Sensoren (Shelly/Tuya) überwachen.

Die Quellen melden bei jeder Abfrage den "online"-Status der Cloud
(`record_online_status`). Der Scheduler ruft `check_connectivity` regelmäßig
auf: Ist ein Sensor länger als OFFLINE_ALERT_MINUTES offline, geht eine
Meldung an den Admin-Chat (ADMIN_TELEGRAM_CHAT_ID), und sobald er zurück ist,
eine Entwarnung. Kurze Aussetzer unter der Wartezeit bleiben still.

Bewusst nur an den Admin, nicht an die Familien: Das ist eine technische
Störung, kein Inaktivitätsalarm.
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerting.telegram import send_telegram_message
from app.config import settings
from app.db import session_scope
from app.models import AlertLog, Household, Sensor, SensorKind

logger = logging.getLogger(__name__)

CLOUD_KINDS = (SensorKind.shelly, SensorKind.tuya)
SOURCE_LABELS = {SensorKind.shelly: "Shelly", SensorKind.tuya: "Tuya"}


def parse_online(value) -> bool | None:
    """Cloud-Angaben wie true/false, 1/0 oder "true" in bool umwandeln."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "1", "online"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"false", "0", "offline"}:
        return False
    return None


def apply_online_status(sensor: Sensor, online: bool, now: dt.datetime) -> None:
    """Zustandswechsel auf dem Sensor festhalten (ohne Commit)."""
    if online:
        if sensor.online is False and not sensor.offline_notified:
            sensor.offline_since = None  # kurzer Aussetzer, nie gemeldet
        sensor.online = True
        return
    if sensor.online is not False:
        sensor.offline_since = now
    sensor.online = False


def record_online_status(kind: SensorKind, external_id: str, online_value) -> None:
    """Von den Quellen nach jeder Cloud-Abfrage aufgerufen."""
    online = parse_online(online_value)
    if online is None or not external_id:
        return
    with session_scope() as session:
        sensor = session.execute(
            select(Sensor).where(Sensor.kind == kind, Sensor.external_id == external_id)
        ).scalar_one_or_none()
        if sensor is None:
            return
        apply_online_status(sensor, online, dt.datetime.now(dt.timezone.utc))
        session.commit()


def _as_utc(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def _monitored_sensors(session: Session) -> list[Sensor]:
    return session.execute(
        select(Sensor)
        .join(Household, Household.id == Sensor.household_id)
        .where(
            Sensor.kind.in_(CLOUD_KINDS),
            Sensor.is_active.is_(True),
            Sensor.is_archived.is_(False),
            Household.is_active.is_(True),
        )
        .order_by(Sensor.household_id, Sensor.name)
    ).scalars().all()


def check_connectivity(session: Session, now: dt.datetime | None = None) -> dict[str, int]:
    """Offline-Meldungen und Entwarnungen an den Admin-Chat verschicken."""
    now = now or dt.datetime.now(dt.timezone.utc)
    result = {"offline_sent": 0, "online_sent": 0, "failed": 0}
    chat_id = settings.admin_telegram_chat_id.strip()
    if not chat_id or not settings.telegram_bot_token:
        return result

    threshold = dt.timedelta(minutes=settings.offline_alert_minutes)
    sensors = _monitored_sensors(session)
    by_household: dict[int, list[Sensor]] = {}
    for sensor in sensors:
        by_household.setdefault(sensor.household_id, []).append(sensor)

    for household_id, household_sensors in by_household.items():
        household = session.get(Household, household_id)
        tz = ZoneInfo(household.timezone)
        went_offline = [
            s for s in household_sensors
            if s.online is False and not s.offline_notified and s.offline_since is not None
            and now - _as_utc(s.offline_since) >= threshold
        ]
        came_back = [s for s in household_sensors if s.online is True and s.offline_notified]

        if went_offline:
            all_offline = all(s.online is False for s in household_sensors)
            heading = (
                f"⚠️ SensIR · {household.name}: alle Sensoren offline (Internet oder Strom weg?)"
                if all_offline and len(household_sensors) > 1
                else f"⚠️ SensIR · {household.name}: Sensor offline"
            )
            lines = [
                f"• {s.name} ({SOURCE_LABELS.get(s.kind, s.kind.value)}) – offline seit "
                f"{_as_utc(s.offline_since).astimezone(tz):%d.%m. %H:%M}"
                for s in went_offline
            ]
            if _send(session, household, chat_id, "\n".join([heading, *lines]), "[Offline]"):
                for s in went_offline:
                    s.offline_notified = True
                result["offline_sent"] += 1
            else:
                result["failed"] += 1

        if came_back:
            lines = []
            for s in came_back:
                since = _as_utc(s.offline_since).astimezone(tz) if s.offline_since else None
                span = f" (offline seit {since:%d.%m. %H:%M})" if since else ""
                lines.append(f"• {s.name}{span}")
            message = "\n".join([f"✅ SensIR · {household.name}: wieder online", *lines])
            if _send(session, household, chat_id, message, "[Wieder online]"):
                for s in came_back:
                    s.offline_notified = False
                    s.offline_since = None
                result["online_sent"] += 1
            else:
                result["failed"] += 1

    session.commit()
    return result


def _send(session: Session, household: Household, chat_id: str, message: str, tag: str) -> bool:
    success = send_telegram_message(chat_id, message)
    session.add(
        AlertLog(
            household_id=household.id,
            contact_id=None,
            message=f"{tag} {message}"[:500],
            success=success,
        )
    )
    return success


def run_connectivity_check() -> None:
    """Scheduler-Einstieg."""
    with session_scope() as session:
        result = check_connectivity(session)
    if any(result.values()):
        logger.info("connectivity check: %s", result)
