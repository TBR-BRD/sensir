"""Tests für Offline-Meldungen (app/connectivity.py), den CSV-Komplettexport
per Telegram (app/export.py) und den Button „Alle Daten“ (app/telegram_bot.py)
auf einer In-Memory-SQLite-DB."""

import datetime as dt
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import connectivity, export, telegram_bot  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import AlertLog, Contact, Household, Sensor, SensorEvent, SensorKind  # noqa: E402

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 12, 14, 0, tzinfo=UTC)


@pytest.fixture()
def session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


@pytest.fixture()
def sent(monkeypatch):
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(settings, "telegram_bot_token", "token")
    monkeypatch.setattr(settings, "admin_telegram_chat_id", "999")
    monkeypatch.setattr(settings, "offline_alert_minutes", 30)
    monkeypatch.setattr(
        connectivity, "send_telegram_message",
        lambda chat_id, text, reply_markup=None: messages.append((chat_id, text)) or True,
    )
    return messages


def _household(session, name="Familie Test") -> Household:
    h = Household(name=name, timezone="UTC")
    session.add(h)
    session.commit()
    return h


def _sensor(session, household, name, external_id) -> Sensor:
    s = Sensor(household_id=household.id, name=name, kind=SensorKind.shelly, external_id=external_id)
    session.add(s)
    session.commit()
    return s


def _set(session, sensor, online, when):
    connectivity.apply_online_status(sensor, online, when)
    session.commit()


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), (1, True), (0, False), ("true", True), ("false", False), (None, None), ("?", None)],
)
def test_parse_online(value, expected):
    assert connectivity.parse_online(value) is expected


def test_short_outage_is_not_reported(session, sent):
    h = _household(session)
    plug = _sensor(session, h, "Zwischenstecker", "a")
    _set(session, plug, True, T0)
    _set(session, plug, False, T0 + dt.timedelta(minutes=1))
    connectivity.check_connectivity(session, now=T0 + dt.timedelta(minutes=20))
    _set(session, plug, True, T0 + dt.timedelta(minutes=25))
    connectivity.check_connectivity(session, now=T0 + dt.timedelta(minutes=40))
    assert sent == []
    assert plug.offline_since is None


def test_offline_reported_once_then_recovery(session, sent):
    h = _household(session)
    plug = _sensor(session, h, "Zwischenstecker", "a")
    _sensor(session, h, "Flur", "b")
    _set(session, plug, False, T0)

    connectivity.check_connectivity(session, now=T0 + dt.timedelta(minutes=29))
    assert sent == []
    connectivity.check_connectivity(session, now=T0 + dt.timedelta(minutes=30))
    assert len(sent) == 1
    chat, text = sent[0]
    assert chat == "999"
    assert "Sensor offline" in text and "Zwischenstecker" in text and "12.11. 14:00" in text

    connectivity.check_connectivity(session, now=T0 + dt.timedelta(minutes=60))
    assert len(sent) == 1  # keine Wiederholung

    _set(session, plug, True, T0 + dt.timedelta(minutes=70))
    connectivity.check_connectivity(session, now=T0 + dt.timedelta(minutes=75))
    assert len(sent) == 2
    assert "wieder online" in sent[1][1] and "Zwischenstecker" in sent[1][1]
    assert plug.offline_notified is False and plug.offline_since is None
    assert session.query(AlertLog).count() == 2


def test_all_sensors_offline_is_one_household_message(session, sent):
    h = _household(session)
    a = _sensor(session, h, "Zwischenstecker", "a")
    b = _sensor(session, h, "Flur", "b")
    _set(session, a, False, T0)
    _set(session, b, False, T0)
    connectivity.check_connectivity(session, now=T0 + dt.timedelta(minutes=31))
    assert len(sent) == 1
    assert "alle Sensoren offline" in sent[0][1]


def test_no_admin_chat_means_no_messages(session, sent, monkeypatch):
    monkeypatch.setattr(settings, "admin_telegram_chat_id", "")
    h = _household(session)
    plug = _sensor(session, h, "Zwischenstecker", "a")
    _set(session, plug, False, T0)
    connectivity.check_connectivity(session, now=T0 + dt.timedelta(hours=2))
    assert sent == []
    assert plug.offline_notified is False


def test_paused_household_is_ignored(session, sent):
    h = _household(session)
    h.is_active = False
    plug = _sensor(session, h, "Zwischenstecker", "a")
    _set(session, plug, False, T0)
    connectivity.check_connectivity(session, now=T0 + dt.timedelta(hours=2))
    assert sent == []


def _contact(session, household, chat_id):
    session.add(Contact(household_id=household.id, name="Admin", telegram_chat_id=chat_id, priority=0))
    session.commit()


def test_all_data_csv_requires_contact_in_every_household(session, monkeypatch):
    docs = []
    monkeypatch.setattr(settings, "telegram_bot_token", "token")
    monkeypatch.setattr(export, "send_telegram_document", lambda *a: docs.append(a) or True)
    a, b = _household(session, "Familie A"), _household(session, "Familie B")
    _contact(session, a, "42")

    result = export.send_all_data_to_chat(session, "42")
    assert result["authorized"] is False and docs == []

    _contact(session, b, "42")
    sensor = _sensor(session, a, "Flur", "x")
    for days in (40, 3):
        session.add(SensorEvent(sensor_id=sensor.id, kind="motion", received_at=T0 - dt.timedelta(days=days)))
    session.commit()

    result = export.send_all_data_to_chat(session, "42")
    assert result == {"authorized": True, "active_households": 2, "sent": 2, "failed": 0, "not_configured": False}
    chat_id, filename, csv_bytes, caption = docs[0]
    assert chat_id == "42" and "alle-daten" in filename
    assert csv_bytes.count(b"\n") == 3  # Kopfzeile + 2 Ereignisse, auch das 40 Tage alte
    assert "2 Ereignisse" in caption and "seit 03.10.2026" in caption
    assert "noch keine Ereignisse" in docs[1][3]


@pytest.mark.parametrize("text", ["Alle Daten", "alle  daten", "/daten", "/daten@SensirBot"])
def test_bot_recognizes_all_data_button(monkeypatch, text):
    calls, replies = [], []

    @contextmanager
    def fake_scope():
        yield None

    monkeypatch.setattr(telegram_bot, "session_scope", fake_scope)
    monkeypatch.setattr(
        telegram_bot, "send_all_data_to_chat",
        lambda session, chat_id: calls.append(chat_id)
        or {"not_configured": False, "active_households": 1, "authorized": True, "sent": 1, "failed": 0},
    )
    monkeypatch.setattr(telegram_bot, "send_telegram_message", lambda chat_id, text, reply_markup=None: replies.append(text))
    telegram_bot.telegram_bot._handle_update({"message": {"text": text, "chat": {"id": 42}}})
    assert calls == ["42"]
    assert "1 CSV-Dateien gesendet" in replies[0]
