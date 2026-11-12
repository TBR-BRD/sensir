"""CSV-Export der Sensor-Rohdaten eines Haushalts - Grundlage für eigenes
ML-Training außerhalb von sensir (das eingebaute Modell in ml/window_model.py
trainiert nur ein 1D-KernelDensity über die Tageszeit; für alles andere
- z. B. ein Modell über mehrere Wochen Rohdaten mit allen Sensoren als
Features - exportiert dieses Modul die zugrunde liegenden Events).

Format: CSV, eine Zeile pro SensorEvent. CSV statt JSON/Parquet, weil es
ohne zusätzliche Bibliotheken direkt in pandas/Excel/Numbers lesbar ist und
für die zu erwartende Datenmenge (paar hundert Events/Woche/Haushalt) völlig
ausreicht.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.alerting.telegram import send_telegram_document
from app.config import settings
from app.models import AlertLog, Contact, Household, Sensor, SensorEvent

logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    "received_at_utc",
    "received_at_local",
    "sensor_id",
    "sensor_name",
    "sensor_kind",
    "sensor_external_id_or_topic",
    "event_kind",
    "value",
    "safety",
]


def build_household_csv(
    session: Session, household: Household, since: dt.datetime, until: dt.datetime
) -> bytes:
    tz = ZoneInfo(household.timezone)
    rows = session.execute(
        select(SensorEvent, Sensor)
        .join(Sensor, Sensor.id == SensorEvent.sensor_id)
        .where(
            Sensor.household_id == household.id,
            SensorEvent.received_at >= since,
            SensorEvent.received_at < until,
        )
        .order_by(SensorEvent.received_at)
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for ev, sensor in rows:
        writer.writerow(
            [
                ev.received_at.isoformat(),
                ev.received_at.astimezone(tz).isoformat(),
                sensor.id,
                sensor.name,
                sensor.kind.value,
                sensor.mqtt_topic or sensor.external_id or "",
                ev.kind,
                ev.value if ev.value is not None else "",
                ev.safety,
            ]
        )
    return buf.getvalue().encode("utf-8")


def export_and_send(
    session: Session, household: Household, since: dt.datetime, until: dt.datetime
) -> list[tuple[Contact, bool]]:
    """Baut die CSV für den Zeitraum und schickt sie an alle aktiven
    Telegram-Kontakte des Haushalts. Gibt (Contact, success) je Kontakt
    zurück, damit Aufrufer (API/Scheduler) das Ergebnis loggen/melden können."""
    csv_bytes = build_household_csv(session, household, since, until)
    filename = f"sensir_{_slug(household.name)}_{since:%Y-%m-%d}_{until:%Y-%m-%d}.csv"
    caption = (
        f"📊 Sensordaten {household.name}: {since:%d.%m.%Y} – {until:%d.%m.%Y} "
        f"({_row_count(csv_bytes)} Ereignisse)"
    )

    contacts = session.execute(
        select(Contact).where(Contact.household_id == household.id, Contact.is_active.is_(True))
    ).scalars().all()

    results: list[tuple[Contact, bool]] = []
    for contact in contacts:
        if not contact.telegram_chat_id:
            continue
        success = send_telegram_document(contact.telegram_chat_id, filename, csv_bytes, caption)
        session.add(
            AlertLog(
                household_id=household.id,
                contact_id=contact.id,
                message=f"[Datenexport {since:%Y-%m-%d}–{until:%Y-%m-%d}] {filename}",
                success=success,
            )
        )
        results.append((contact, success))
    session.commit()
    return results


def _row_count(csv_bytes: bytes) -> int:
    return max(csv_bytes.count(b"\n") - 1, 0)  # minus Header-Zeile


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


def export_and_send_all_households(session: Session) -> None:
    """Wöchentlicher Scheduler-Job (siehe scheduler.py) - ein Export pro
    aktivem Haushalt für die vergangenen 7 Tage."""
    until = dt.datetime.now(dt.timezone.utc)
    since = until - dt.timedelta(days=7)
    households = session.execute(
        select(Household).where(Household.is_active.is_(True))
    ).scalars().all()
    for household in households:
        try:
            results = export_and_send(session, household, since, until)
            if not results:
                logger.info("household %s: wöchentlicher Export, aber kein Telegram-Kontakt", household.id)
        except Exception:
            logger.exception("household %s: wöchentlicher Datenexport fehlgeschlagen", household.id)


ALL_DATA_SINCE = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)


def send_all_data_to_chat(session: Session, chat_id: str) -> dict[str, int | bool]:
    """Alle bisher gespeicherten Ereignisse jedes aktiven Haushalts als CSV an
    den anfragenden Telegram-Chat (Button „Alle Daten“). Gleiche Berechtigung
    wie bei den Heatmaps: Der Chat muss in allen aktiven Familien aktiver
    Kontakt sein."""
    from app.weekly_heatmap import _active_households_and_chat_contacts  # vermeidet Importzyklus

    result: dict[str, int | bool] = {
        "authorized": False,
        "active_households": 0,
        "sent": 0,
        "failed": 0,
        "not_configured": not bool(settings.telegram_bot_token),
    }
    if result["not_configured"]:
        return result
    households, contacts = _active_households_and_chat_contacts(session, chat_id)
    result["active_households"] = len(households)
    if not households or any(household.id not in contacts for household in households):
        return result
    result["authorized"] = True

    until = dt.datetime.now(dt.timezone.utc)
    for household in households:
        csv_bytes = build_household_csv(session, household, ALL_DATA_SINCE, until)
        count = _row_count(csv_bytes)
        first = session.execute(
            select(func.min(SensorEvent.received_at))
            .join(Sensor, Sensor.id == SensorEvent.sensor_id)
            .where(Sensor.household_id == household.id)
        ).scalar_one()
        since_label = f"seit {first:%d.%m.%Y}" if first else "noch keine Ereignisse"
        filename = f"sensir_{_slug(household.name)}_alle-daten_{until:%Y-%m-%d}.csv"
        caption = f"📦 Alle Sensordaten {household.name}: {since_label} ({count} Ereignisse)"
        success = send_telegram_document(chat_id, filename, csv_bytes, caption)
        result["sent" if success else "failed"] += 1
        session.add(
            AlertLog(
                household_id=household.id,
                contact_id=contacts[household.id].id,
                message=f"[Datenexport alle Daten bis {until:%Y-%m-%d}] {filename}",
                success=success,
            )
        )
    session.commit()
    return result
