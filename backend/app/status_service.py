"""Shared status computation, used by both the JSON API (app/api/status.py)
and the web dashboard (app/web/routes.py) so the two never drift apart."""

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.alerting.engine import active_window
from app.ingest.normalize import NON_ACTIVITY_KINDS
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
        .where(
            Sensor.household_id == household.id,
            SensorEvent.safety.is_(False),
            SensorEvent.kind.notin_(NON_ACTIVITY_KINDS),
        )
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
            SensorEvent.kind.notin_(NON_ACTIVITY_KINDS),
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


APPLIANCE_KINDS = NON_ACTIVITY_KINDS


def local_midnight(household: Household) -> dt.datetime:
    """Beginn des heutigen Tages in der Zeitzone des Haushalts."""
    tz = ZoneInfo(household.timezone)
    return dt.datetime.combine(dt.datetime.now(tz).date(), dt.time.min, tzinfo=tz)


def recent_events(
    db: Session,
    household: Household,
    limit: int | None = None,
    since: dt.datetime | None = None,
) -> list[dict]:
    """SensorEvents des Haushalts, angereichert um Sensornamen und lokale Zeit.
    Ohne limit/since werden alle gespeicherten Ereignisse zurückgegeben; mit
    `since` nur Ereignisse ab diesem Zeitpunkt. Geteilt zwischen der
    Web-Detailseite (app/web/routes.py) und der JSON-API
    (app/api/events.py, u. a. für die Home-Assistant-Lovelace-Karte)."""
    tz = ZoneInfo(household.timezone)
    query = (
        select(SensorEvent, Sensor)
        .join(Sensor, Sensor.id == SensorEvent.sensor_id)
        .where(Sensor.household_id == household.id)
        .order_by(SensorEvent.received_at.desc())
    )
    if since is not None:
        query = query.where(SensorEvent.received_at >= since)
    if limit is not None:
        query = query.limit(limit)
    rows = db.execute(query).all()
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


def _activity_events_between(
    db: Session,
    household: Household,
    start: dt.datetime,
    end: dt.datetime,
) -> list[tuple[SensorEvent, Sensor]]:
    return db.execute(
        select(SensorEvent, Sensor)
        .join(Sensor, Sensor.id == SensorEvent.sensor_id)
        .where(
            Sensor.household_id == household.id,
            SensorEvent.received_at >= start,
            SensorEvent.received_at < end,
            SensorEvent.safety.is_(False),
            SensorEvent.kind.notin_(NON_ACTIVITY_KINDS),
        )
        .order_by(SensorEvent.received_at.asc())
    ).all()


def _as_utc(value: dt.datetime) -> dt.datetime:
    # Postgres liefert timezone-aware Werte; SQLite (Tests) naive UTC-Werte.
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def format_duration(minutes: float) -> str:
    """Dauer in Minuten als z. B. „3 h 25 min“, „45 min“ oder „0 min“."""
    hours, rest = divmod(int(round(minutes)), 60)
    if hours and rest:
        return f"{hours} h {rest} min"
    if hours:
        return f"{hours} h"
    return f"{rest} min"


def tv_intervals(
    db: Session,
    household: Household,
    start: dt.datetime,
    end: dt.datetime,
    now: dt.datetime | None = None,
) -> dict:
    """TV-Einschaltzeiten zwischen `start` und `end` aus den appliance_on/-off-
    Ereignissen der Zwischenstecker des Haushalts.

    Ein Zeitraum beginnt mit appliance_on und endet mit dem nächsten
    appliance_off. War der TV bei `start` bereits an (letztes Ereignis davor
    war appliance_on), beginnt der Zeitraum bei `start`; ist er noch an, endet
    er bei `now` (höchstens `end`) und gilt als laufend. Doppelte on- oder
    off-Ereignisse werden ignoriert. Minutenangaben zählen ab `start`.
    """
    tz = ZoneInfo(household.timezone)
    now = now or dt.datetime.now(dt.timezone.utc)
    sensors = db.execute(select(Sensor).where(Sensor.household_id == household.id)).scalars().all()
    intervals: list[dict] = []
    for sensor in sensors:
        previous_kind = db.execute(
            select(SensorEvent.kind)
            .where(
                SensorEvent.sensor_id == sensor.id,
                SensorEvent.kind.in_(APPLIANCE_KINDS),
                SensorEvent.received_at < start,
            )
            .order_by(SensorEvent.received_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        events = db.execute(
            select(SensorEvent)
            .where(
                SensorEvent.sensor_id == sensor.id,
                SensorEvent.kind.in_(APPLIANCE_KINDS),
                SensorEvent.received_at >= start,
                SensorEvent.received_at < end,
            )
            .order_by(SensorEvent.received_at.asc())
        ).scalars().all()
        if previous_kind is None and not events:
            continue

        def add(on_at: dt.datetime, off_at: dt.datetime, carried_over: bool, ongoing: bool) -> None:
            if off_at <= on_at:
                return
            on_local, off_local = on_at.astimezone(tz), off_at.astimezone(tz)
            start_minute = (on_at - start).total_seconds() / 60
            end_minute = (off_at - start).total_seconds() / 60
            intervals.append(
                {
                    "sensor_name": sensor.name,
                    "start_minute": start_minute,
                    "end_minute": end_minute,
                    "minutes": end_minute - start_minute,
                    "start": on_local.strftime("%H:%M"),
                    "end": "24:00" if off_at >= end else off_local.strftime("%H:%M"),
                    "carried_over": carried_over,
                    "ongoing": ongoing,
                }
            )

        on_since = start if previous_kind == "appliance_on" else None
        carried_over = on_since is not None
        for event in events:
            received_at = _as_utc(event.received_at)
            if event.kind == "appliance_on" and on_since is None:
                on_since, carried_over = received_at, False
            elif event.kind == "appliance_off" and on_since is not None:
                add(on_since, received_at, carried_over, ongoing=False)
                on_since = None
        if on_since is not None:
            add(on_since, min(end, now), carried_over, ongoing=now < end)

    intervals.sort(key=lambda item: item["start_minute"])
    total = sum(item["minutes"] for item in intervals)
    return {
        "intervals": intervals,
        "minutes": round(total, 1),
        "label": format_duration(total),
        "on_count": sum(1 for item in intervals if not item["carried_over"]),
        "is_on": any(item["ongoing"] for item in intervals),
    }


def today_activity_chart(db: Session, household: Household) -> dict:
    """Return today's local-time activity events and hourly counts for charts."""
    tz = ZoneInfo(household.timezone)
    today = dt.datetime.now(tz).date()
    start = dt.datetime.combine(today, dt.time.min, tzinfo=tz)
    end = dt.datetime.combine(today + dt.timedelta(days=1), dt.time.min, tzinfo=tz)
    rows = _activity_events_between(db, household, start, end)

    hourly_counts = [0] * 24
    events = []
    for event, sensor in rows:
        local_time = event.received_at.astimezone(tz)
        hourly_counts[local_time.hour] += 1
        events.append(
            {
                "minute_of_day": local_time.hour * 60 + local_time.minute + local_time.second / 60,
                "time": local_time.strftime("%H:%M:%S"),
                "sensor_name": sensor.name,
            }
        )

    now_local = dt.datetime.now(tz)
    tv = tv_intervals(db, household, start, end, now=now_local)
    return {
        "date": today,
        "timezone": household.timezone,
        "updated_at": now_local,
        "events": events,
        "hourly_counts": hourly_counts,
        "event_count": len(events),
        "max_hourly_count": max(hourly_counts, default=0),
        "tv_intervals": tv["intervals"],
        "tv_minutes": tv["minutes"],
        "tv_label": tv["label"],
        "tv_on_count": tv["on_count"],
        "tv_is_on": tv["is_on"],
    }


def weekly_activity_chart(db: Session, household: Household) -> dict:
    """Return hourly activity counts for the current local Monday–Sunday week."""
    tz = ZoneInfo(household.timezone)
    today = dt.datetime.now(tz).date()
    week_start = today - dt.timedelta(days=today.weekday())
    week_end = week_start + dt.timedelta(days=7)
    start = dt.datetime.combine(week_start, dt.time.min, tzinfo=tz)
    end = dt.datetime.combine(week_end, dt.time.min, tzinfo=tz)

    weekday_names = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
    days = [
        {
            "date": week_start + dt.timedelta(days=offset),
            "label": f"{weekday_names[offset]} {(week_start + dt.timedelta(days=offset)):%d.%m.}",
            "hourly_counts": [0] * 24,
            "total": 0,
        }
        for offset in range(7)
    ]

    for event, _sensor in _activity_events_between(db, household, start, end):
        local_time = event.received_at.astimezone(tz)
        day_index = (local_time.date() - week_start).days
        days[day_index]["hourly_counts"][local_time.hour] += 1
        days[day_index]["total"] += 1

    now_local = dt.datetime.now(tz)
    for day in days:
        day_start = dt.datetime.combine(day["date"], dt.time.min, tzinfo=tz)
        day_end = dt.datetime.combine(day["date"] + dt.timedelta(days=1), dt.time.min, tzinfo=tz)
        tv = tv_intervals(db, household, day_start, day_end, now=now_local)
        day["tv_intervals"] = tv["intervals"]
        day["tv_minutes"] = tv["minutes"]
        day["tv_label"] = tv["label"]
    tv_week_minutes = sum(day["tv_minutes"] for day in days)
    return {
        "week_start": week_start,
        "week_end": week_end - dt.timedelta(days=1),
        "timezone": household.timezone,
        "updated_at": now_local,
        "days": days,
        "tv_minutes": round(tv_week_minutes, 1),
        "tv_label": format_duration(tv_week_minutes),
        "event_count": sum(day["total"] for day in days),
        "max_hourly_count": max(
            (count for day in days for count in day["hourly_counts"]),
            default=0,
        ),
    }
