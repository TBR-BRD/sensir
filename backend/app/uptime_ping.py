"""Externer Totmann-Schalter (z. B. healthchecks.io).

Hintergrund: am 2026-11-17 lief der Docker-Dienst der Synology rund sechs
Stunden nicht (ca. 14:00-19:58) - SensIR war komplett weg, aber niemand
wurde benachrichtigt, weil SensIR sich selbst nicht als ausgefallen melden
kann. Home Assistant prüft zwar /healthz, läuft aber als VM auf derselben
Synology und fällt bei einem Komplettausfall (Strom, Internet, Neustart)
mit aus.

Deshalb meldet SensIR sich alle paar Minuten *ausgehend* bei einer externen
Ping-URL. Bleibt der Ping aus, alarmiert der externe Dienst (Mail,
Telegram, ...). Es braucht keinen Zugriff von außen auf die Synology.

- Heartbeat zu alt (Scheduler eingefroren) -> kein Ping (der Job würde
  dann ohnehin nicht laufen)
- Datenbank nicht erreichbar -> Ping an ``<url>/fail``: sofortiger Alarm
- alles in Ordnung -> Ping an ``<url>``

Leere ``HEALTHCHECK_PING_URL`` = ausgeschaltet.
"""

import logging

import httpx
from sqlalchemy import text

from app import health
from app.config import settings
from app.db import session_scope

logger = logging.getLogger(__name__)


def _database_ok() -> bool:
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("uptime ping: database check failed")
        return False


def ping_url_for_state() -> str | None:
    """Welche URL gepingt werden soll - None = gar nicht pingen."""
    url = settings.healthcheck_ping_url.strip().rstrip("/")
    if not url or not health.is_healthy():
        return None
    return url if _database_ok() else f"{url}/fail"


def send_uptime_ping() -> None:
    url = ping_url_for_state()
    if url is None:
        return
    try:
        httpx.get(url, timeout=10).raise_for_status()
    except Exception as exc:
        # Kein Stacktrace: ohne Internet würde das sonst alle 5 Minuten
        # das Log fluten. Der externe Dienst alarmiert ohnehin.
        logger.warning("uptime ping failed: %s", exc)
