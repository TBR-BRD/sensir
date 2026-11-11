"""Tests für die TV-Zeiten aus appliance_on/-off-Ereignissen der Zwischenstecker
(app/status_service.py) auf einer In-Memory-SQLite-DB.

SQLite speichert Zeitstempel ohne Zeitzone, daher arbeiten die Tests komplett
in UTC; Postgres vergleicht im Betrieb zeitzonenrichtig."""

import datetime as dt
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base  # noqa: E402
from app.models import Household, Sensor, SensorEvent  # noqa: E402
from app.status_service import format_duration, recent_events, tv_intervals  # noqa: E402

UTC = dt.timezone.utc
DAY_START = dt.datetime(2026, 11, 10, tzinfo=UTC)
DAY_END = DAY_START + dt.timedelta(days=1)
AFTER_DAY = DAY_END + dt.timedelta(hours=1)


@pytest.fixture()
def session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


@pytest.fixture()
def household(session):
    h = Household(name="Test", timezone="UTC")
    session.add(h)
    session.flush()
    return h


@pytest.fixture()
def plug(session, household):
    s = Sensor(household_id=household.id, name="TV-Stecker")
    session.add(s)
    session.flush()
    return s


def at(hours: float, day: dt.datetime = DAY_START) -> dt.datetime:
    return day + dt.timedelta(hours=hours)


def add_events(session, sensor, *events):
    for kind, when in events:
        session.add(SensorEvent(sensor_id=sensor.id, kind=kind, received_at=when))
    session.commit()


def test_on_off_pair_gives_one_interval(session, household, plug):
    add_events(session, plug, ("appliance_on", at(10)), ("appliance_off", at(11.5)))
    tv = tv_intervals(session, household, DAY_START, DAY_END, now=AFTER_DAY)
    assert [(i["start"], i["end"], i["minutes"]) for i in tv["intervals"]] == [("10:00", "11:30", 90)]
    assert tv["label"] == "1 h 30 min"
    assert tv["on_count"] == 1
    assert tv["is_on"] is False


def test_tv_on_since_previous_day_starts_at_midnight(session, household, plug):
    add_events(session, plug, ("appliance_on", at(-1)), ("appliance_off", at(1)))
    tv = tv_intervals(session, household, DAY_START, DAY_END, now=AFTER_DAY)
    (interval,) = tv["intervals"]
    assert interval["start_minute"] == 0
    assert interval["minutes"] == 60
    assert interval["carried_over"] is True
    assert tv["on_count"] == 0


def test_running_tv_ends_now_and_is_ongoing(session, household, plug):
    add_events(session, plug, ("appliance_on", at(20)))
    tv = tv_intervals(session, household, DAY_START, DAY_END, now=at(21))
    (interval,) = tv["intervals"]
    assert interval["minutes"] == 60
    assert interval["ongoing"] is True
    assert tv["is_on"] is True


def test_tv_on_until_end_of_past_day(session, household, plug):
    add_events(session, plug, ("appliance_on", at(23)))
    tv = tv_intervals(session, household, DAY_START, DAY_END, now=AFTER_DAY)
    (interval,) = tv["intervals"]
    assert interval["end"] == "24:00"
    assert interval["minutes"] == 60
    assert interval["ongoing"] is False


def test_duplicate_and_orphan_events_are_ignored(session, household, plug):
    add_events(
        session,
        plug,
        ("appliance_off", at(8)),  # off ohne vorheriges on
        ("appliance_on", at(9)),
        ("appliance_on", at(9.5)),  # doppeltes on
        ("appliance_off", at(10)),
        ("appliance_off", at(10.5)),  # doppeltes off
    )
    tv = tv_intervals(session, household, DAY_START, DAY_END, now=AFTER_DAY)
    assert [(i["start"], i["end"]) for i in tv["intervals"]] == [("09:00", "10:00")]


def test_future_day_has_no_tv_time(session, household, plug):
    add_events(session, plug, ("appliance_on", at(-2)))
    tv = tv_intervals(session, household, DAY_START, DAY_END, now=at(-1))
    assert tv["intervals"] == []
    assert tv["label"] == "0 min"


def test_other_event_kinds_are_not_tv(session, household, plug):
    add_events(session, plug, ("motion", at(10)), ("ir", at(11)))
    tv = tv_intervals(session, household, DAY_START, DAY_END, now=AFTER_DAY)
    assert tv["intervals"] == []


def test_recent_events_since_filters_older_events(session, household, plug):
    add_events(session, plug, ("motion", at(-3)), ("motion", at(2)), ("appliance_on", at(5)))
    events = recent_events(session, household, since=DAY_START)
    assert [e["kind"] for e in events] == ["appliance_on", "motion"]
    assert len(recent_events(session, household)) == 3


@pytest.mark.parametrize(
    ("minutes", "label"),
    [(0, "0 min"), (45, "45 min"), (60, "1 h"), (205, "3 h 25 min"), (59.6, "1 h")],
)
def test_format_duration(minutes, label):
    assert format_duration(minutes) == label
