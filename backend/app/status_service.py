"""Shared status computation, used by both the JSON API (app/api/status.py)
and the web dashboard (app/web/routes.py) so the two never drift apart."""

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.alerting.engine import active_window
from app.models import ActivityCheck, Household, SensorEvent, Sensor
from app.schemas import HouseholdStatusOut


def compute_status(db: Session, household: Household) -> HouseholdStatusOut:
    tz = ZoneInfo(household.timezone)
    now_local = dt.datetime.now(tz)
    midnight = dt.datetime.combine(now_local.date(), dt.time.min, tzinfo=tz)

    last_check = db.execute(
        select(ActivityCheck)
        .where(ActivityCheck.household_id == household.id)
        .order_by(ActivityCheck.checked_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    last_event_at = db.execute(
        select(func.max(SensorEvent.received_at))
        .join(Sensor, Sensor.id == SensorEvent.sensor_id)
        .where(Sensor.household_id == household.id)
    ).scalar_one()
    if last_event_at is not None:
        # in der DB liegt alles in UTC (DateTime(timezone=True)) - fürs
        # Dashboard/die API auf die Haushalts-Zeitzone umrechnen, sonst
        # sieht ein UTC-Zeitstempel wie eine 2h alte/verpasste Aktivität aus
        # (live beobachtet: Shelly-App zeigte 20:09 Lokalzeit, Dashboard
        # 18:10 - beides dasselbe Ereignis, nur ohne Umrechnung angezeigt).
        last_event_at = last_event_at.astimezone(tz)

    events_since_midnight = db.execute(
        select(func.count(SensorEvent.id))
        .join(Sensor, Sensor.id == SensorEvent.sensor_id)
        .where(
            Sensor.household_id == household.id,
            SensorEvent.received_at >= midnight,
            SensorEvent.safety.is_(False),
        )
    ).scalar_one()

    window = active_window(db, household.id, now_local)

    return HouseholdStatusOut(
        household_id=household.id,
        household_name=household.name,
        household_active=household.is_active,
        status=last_check.status if last_check else None,
        last_event_at=last_event_at,
        events_since_midnight=events_since_midnight,
        active_window=window,
    )


def recent_events(db: Session, household: Household, limit: int = 20) -> list[dict]:
    """Die letzten `limit` SensorEvents des Haushalts, angereichert um den
    Sensornamen und auf die Haushalts-Zeitzone umgerechnet. Geteilt zwischen
    der Web-Detailseite (app/web/routes.py) und der JSON-API
    (app/api/events.py, u. a. für die Home-Assistant-Lovelace-Karte)."""
    tz = ZoneInfo(household.timezone)
    rows = db.execute(
        select(SensorEvent, Sensor)
        .join(Sensor, Sensor.id == SensorEvent.sensor_id)
        .where(Sensor.household_id == household.id)
        .order_by(SensorEvent.received_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "received_at_local": ev.received_at.astimezone(tz),
            "sensor_name": sensor.name,
            "kind": ev.kind,
            "protocol": ev.protocol,
            "data_hex": ev.data_hex,
            "value": ev.value,
            "safety": ev.safety,
        }
        for ev, sensor in rows
    ]
