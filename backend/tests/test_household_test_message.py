"""Tests für den Button „Testnachricht senden“ auf der Haushaltsseite
(app/web/routes.py) auf einer In-Memory-SQLite-DB."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.models import AlertLog, Contact, Household  # noqa: E402
from app.web import routes  # noqa: E402


@pytest.fixture()
def session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


@pytest.fixture()
def sent(monkeypatch):
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(settings, "telegram_bot_token", "token")
    monkeypatch.setattr(
        routes, "send_telegram_message",
        lambda chat_id, text, reply_markup=None: messages.append((chat_id, text)) or chat_id != "bad",
    )
    return messages


def _household(session) -> Household:
    h = Household(name="Familie 1", timezone="UTC")
    session.add(h)
    session.commit()
    return h


def _contact(session, household, name, chat_id, active=True, priority=0) -> Contact:
    c = Contact(household_id=household.id, name=name, telegram_chat_id=chat_id, is_active=active, priority=priority)
    session.add(c)
    session.commit()
    return c


def test_household_test_message_goes_to_all_active_telegram_contacts(session, sent):
    h = _household(session)
    _contact(session, h, "Anna", "1")
    _contact(session, h, "Ben", "2", priority=1)
    _contact(session, h, "Inaktiv", "3", active=False)
    _contact(session, h, "Nur Telefon", None)

    response = routes.send_household_test_message(h.id, session)

    assert [chat for chat, _ in sent] == ["1", "2"]
    assert "Testnachricht von SensIR für Familie 1" in sent[0][1]
    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/households/{h.id}?test_requested=1&test_sent=2&test_failed=0&test_not_configured=0"
    )
    assert session.query(AlertLog).count() == 2


def test_failed_delivery_is_counted_and_logged(session, sent):
    h = _household(session)
    _contact(session, h, "Anna", "1")
    _contact(session, h, "Falsch", "bad")

    response = routes.send_household_test_message(h.id, session)

    assert "test_sent=1&test_failed=1" in response.headers["location"]
    logs = session.query(AlertLog).order_by(AlertLog.id).all()
    assert [log.success for log in logs] == [True, False]


def test_single_contact_button_reports_result(session, sent):
    h = _household(session)
    _contact(session, h, "Anna", "1")
    ben = _contact(session, h, "Ben", "2")

    response = routes.test_contact_web(h.id, ben.id, session)

    assert [chat for chat, _ in sent] == ["2"]
    assert "test_sent=1&test_failed=0" in response.headers["location"]


@pytest.mark.parametrize(
    ("query", "text"),
    [
        ("test_requested=1&test_sent=2&test_failed=0", "Testnachricht an 2 Kontakte gesendet"),
        ("test_requested=1&test_sent=1&test_failed=0", "Testnachricht an 1 Kontakt gesendet"),
        ("test_requested=1&test_sent=1&test_failed=1", "1 gesendet, 1 fehlgeschlagen"),
        ("test_requested=1&test_sent=0&test_failed=0", "Kein aktiver Kontakt mit Telegram-Chat"),
        ("test_requested=1&test_not_configured=1", "Telegram ist nicht konfiguriert"),
    ],
)
def test_household_page_shows_button_and_result(session, query, text):
    from app.main import app

    h = _household(session)
    app.dependency_overrides[get_db] = lambda: session
    try:
        client = TestClient(app)  # ohne "with": Lifespan (Scheduler, Quellen) startet nicht
        page = client.get(f"/households/{h.id}?{query}").text
        assert f'action="/households/{h.id}/test-message"' in page
        assert text in page
        assert "test-notice" not in client.get(f"/households/{h.id}").text
    finally:
        app.dependency_overrides.clear()


def test_stylesheet_url_changes_with_css_content(session):
    """Die CSS-URL enthält einen Hash des Dateiinhalts, damit Browser nach
    einem Update nicht die alte Fassung aus dem Cache nehmen."""
    import hashlib

    from app.main import app

    expected = hashlib.sha256(Path("app/web/static/style.css").read_bytes()).hexdigest()[:10]
    h = _household(session)
    app.dependency_overrides[get_db] = lambda: session
    try:
        page = TestClient(app).get(f"/households/{h.id}").text
    finally:
        app.dependency_overrides.clear()
    assert f'/static/style.css?v={expected}"' in page
