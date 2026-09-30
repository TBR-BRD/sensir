"""Tests für die ODER-Verknüpfung mehrerer Zeitfenster, Sensor-Scoping und
confirmation_only-Ausschluss in app/alerting/engine.py - auf einer
In-Memory-SQLite-DB, damit kein Postgres nötig ist."""

import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base  # noqa: E402
from app.models import (  # noqa: E402
    ActivityCheck,
    CheckStatus,
    Household,
    ObservationWindow,
    Sensor,
    SensorEvent,
    SensorKind,
)
from app.alerting import engine as alert_engine  # noqa: E402

TZ = ZoneInfo("Europe/Berlin")


@pytest.fixture()
def session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    yield s
    s.close()


def _household(session) -> Household:
    h = Household(name="Test", timezone="Europe/Berlin")
    session.add(h)
    session.commit()
    return h


def _sensor(session, household_id: int, name: str, config: dict | None = None) -> Sensor:
    s = Sensor(household_id=household_id, name=name, kind=SensorKind.shelly, external_id=name, config=config or {})
    session.add(s)
    session.commit()
    return s


def _event(session, sensor_id: int, when: dt.datetime, kind: str = "motion") -> None:
    session.add(SensorEvent(sensor_id=sensor_id, received_at=when, kind=kind))
    session.commit()


def test_countable_sensor_ids_excludes_confirmation_only(session):
    h = _household(session)
    motion = _sensor(session, h.id, "motion")
    cam = _sensor(session, h.id, "cam", config={"confirmation_only": True})

    ids = alert_engine._countable_sensor_ids(session, h.id, sensor_id=None)
    assert motion.id in ids
    assert cam.id not in ids

    # auch bei explizitem sensor_id=cam.id bleibt sie ausgeschlossen
    assert alert_engine._countable_sensor_ids(session, h.id, sensor_id=cam.id) == []


def test_camera_events_never_satisfy_a_window(session):
    h = _household(session)
    cam = _sensor(session, h.id, "cam", config={"confirmation_only": True})
    now_local = dt.datetime(2026, 9, 30, 20, 0, tzinfo=TZ)
    _event(session, cam.id, now_local - dt.timedelta(minutes=5))
    _event(session, cam.id, now_local - dt.timedelta(minutes=3))

    status = alert_engine._evaluate_window(
        session, h, window_id=None, sensor_id=None,
        start_t=dt.time(8, 0), end_t=dt.time(22, 0), min_actions=1,
        now_local=now_local, tz=TZ,
    )
    # Fenster noch offen (22:00 nicht erreicht), aber Kamera-Events zählen
    # nicht -> trotz 2 Ereignissen noch nicht "positive"
    assert status is None


def test_multiple_windows_are_or_linked(session):
    """Bewegungsmelder-Fenster (8-22) hat genug Ereignisse, IR-Fenster
    (18-22) nicht -> Haushalt insgesamt trotzdem "in Ordnung" (ein Treffer
    reicht), kein Alarm."""
    h = _household(session)
    motion = _sensor(session, h.id, "motion")
    ir = _sensor(session, h.id, "ir")

    now_local = dt.datetime(2026, 9, 30, 23, 0, tzinfo=TZ)  # beide Fenster schon vorbei
    _event(session, motion.id, dt.datetime(2026, 9, 30, 10, 0, tzinfo=TZ))
    _event(session, motion.id, dt.datetime(2026, 9, 30, 11, 0, tzinfo=TZ))
    # ir bekommt absichtlich keine Ereignisse

    session.add(ObservationWindow(
        household_id=h.id, start_time=dt.time(8, 0), end_time=dt.time(22, 0),
        min_actions=2, sensor_id=motion.id,
    ))
    session.add(ObservationWindow(
        household_id=h.id, start_time=dt.time(18, 0), end_time=dt.time(22, 0),
        min_actions=1, sensor_id=ir.id,
    ))
    session.commit()

    alert_engine._check_household(session, h, now_local=now_local)

    checks = session.query(ActivityCheck).all()
    statuses = {c.status for c in checks}
    assert CheckStatus.positive in statuses
    assert CheckStatus.negative in statuses
    # kein "alle negativ"-Sammelalarm, weil mindestens ein Fenster positiv war
    from app.models import AlertLog
    assert session.query(AlertLog).count() == 0


def test_alert_only_when_all_windows_negative(session, monkeypatch):
    h = _household(session)
    motion = _sensor(session, h.id, "motion")
    from app.models import Contact
    session.add(Contact(household_id=h.id, name="Test", telegram_chat_id="123", priority=0))
    session.commit()

    sent = []
    monkeypatch.setattr(
        alert_engine, "send_telegram_message",
        lambda chat_id, text: sent.append((chat_id, text)) or True,
    )

    now_local = dt.datetime(2026, 9, 30, 23, 0, tzinfo=TZ)  # Fenster vorbei, keine Ereignisse
    session.add(ObservationWindow(
        household_id=h.id, start_time=dt.time(8, 0), end_time=dt.time(22, 0),
        min_actions=2, sensor_id=motion.id,
    ))
    session.commit()

    alert_engine._check_household(session, h, now_local=now_local)

    from app.models import AlertLog
    assert session.query(ActivityCheck).one().status == CheckStatus.negative
    assert session.query(AlertLog).count() == 1
    assert sent and "Bitte melde dich bei" in sent[0][1]

    # zweiter Lauf (z. B. nächster Scheduler-Tick) darf nicht nochmal alarmieren
    alert_engine._check_household(session, h, now_local=now_local)
    assert session.query(AlertLog).count() == 1
