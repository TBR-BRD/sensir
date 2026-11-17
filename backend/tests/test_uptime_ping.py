"""Tests für den externen Totmann-Ping (app/uptime_ping.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import health, uptime_ping  # noqa: E402
from app.config import settings  # noqa: E402

URL = "https://hc-ping.com/beispiel-uuid"


@pytest.fixture()
def calls(monkeypatch):
    sent: list[str] = []

    class _Response:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(uptime_ping.httpx, "get", lambda url, timeout: sent.append(url) or _Response())
    monkeypatch.setattr(settings, "healthcheck_ping_url", URL)
    monkeypatch.setattr(uptime_ping, "_database_ok", lambda: True)
    health.touch()
    return sent


def test_healthy_pings_base_url(calls):
    uptime_ping.send_uptime_ping()
    assert calls == [URL]


def test_trailing_slash_is_ignored(calls, monkeypatch):
    monkeypatch.setattr(settings, "healthcheck_ping_url", URL + "/")
    uptime_ping.send_uptime_ping()
    assert calls == [URL]


def test_database_down_reports_fail(calls, monkeypatch):
    monkeypatch.setattr(uptime_ping, "_database_ok", lambda: False)
    uptime_ping.send_uptime_ping()
    assert calls == [URL + "/fail"]


def test_stale_heartbeat_sends_nothing(calls, monkeypatch):
    monkeypatch.setattr(health, "is_healthy", lambda: False)
    uptime_ping.send_uptime_ping()
    assert calls == []


def test_disabled_without_url(calls, monkeypatch):
    monkeypatch.setattr(settings, "healthcheck_ping_url", "")
    uptime_ping.send_uptime_ping()
    assert calls == []


def test_network_error_is_swallowed(monkeypatch):
    def boom(url, timeout):
        raise uptime_ping.httpx.ConnectError("kein Internet")

    monkeypatch.setattr(uptime_ping.httpx, "get", boom)
    monkeypatch.setattr(settings, "healthcheck_ping_url", URL)
    monkeypatch.setattr(uptime_ping, "_database_ok", lambda: True)
    health.touch()
    uptime_ping.send_uptime_ping()  # darf nicht werfen


def test_database_check_against_sqlite(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(uptime_ping, "session_scope", sessionmaker(bind=create_engine("sqlite://")))
    assert uptime_ping._database_ok() is True

    def broken():
        raise RuntimeError("db weg")

    monkeypatch.setattr(uptime_ping, "session_scope", broken)
    assert uptime_ping._database_ok() is False
