import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerting.telegram import send_telegram_message
from app.config import settings
from app.db import get_db
from app.models import AlertLog, Contact, Household, ObservationWindow, Sensor, WindowSource
from app.status_service import compute_status
from app.status_service import recent_events as recent_events_service

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory="app/web/templates")


def _tuya_trial_warning() -> str | None:
    """Erinnerung fürs Dashboard, wenn TUYA_TRIAL_EXPIRES (manuell in .env
    gepflegt, siehe app/config.py) bald abläuft oder schon abgelaufen ist -
    Tuya bietet dafür kein API-Feld an, das man automatisch abfragen könnte."""
    if not settings.tuya_trial_expires:
        return None
    days_left = (settings.tuya_trial_expires - dt.date.today()).days
    if days_left > settings.tuya_trial_warn_days_before:
        return None
    expiry = f"{settings.tuya_trial_expires:%d.%m.%Y}"
    if days_left < 0:
        return (
            f"⚠️ Das Tuya-IoT-Core-Trial-Abo ist am {expiry} abgelaufen — "
            f"Tuya-Geräte liefern keine Daten mehr, bis es bei iot.tuya.com "
            f"(Cloud → Service API → IoT Core → View Details → Extend Trial "
            f"Period) verlängert wird."
        )
    return (
        f"⚠️ Das Tuya-IoT-Core-Trial-Abo läuft am {expiry} ab (noch "
        f"{days_left} Tage) — rechtzeitig bei iot.tuya.com verlängern."
    )


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    households = db.execute(select(Household)).scalars().all()
    statuses = [compute_status(db, h) for h in households]
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "statuses": statuses,
            "tuya_trial_warning": _tuya_trial_warning(),
        },
    )


@router.get("/households/{household_id}")
def household_detail(request: Request, household_id: int, db: Session = Depends(get_db)):
    household = db.get(Household, household_id)
    status = compute_status(db, household)
    sensors = db.execute(select(Sensor).where(Sensor.household_id == household_id)).scalars().all()
    contacts = db.execute(
        select(Contact).where(Contact.household_id == household_id).order_by(Contact.priority)
    ).scalars().all()
    windows = db.execute(select(ObservationWindow).where(ObservationWindow.household_id == household_id)).scalars().all()
    events = recent_events_service(db, household, limit=20)
    return templates.TemplateResponse(
        "household.html",
        {
            "request": request,
            "household": household,
            "status": status,
            "sensors": sensors,
            "contacts": contacts,
            "windows": windows,
            "recent_events": events,
        },
    )


@router.post("/households/{household_id}/contacts")
def add_contact(
    household_id: int,
    name: str = Form(...),
    telegram_chat_id: str = Form(""),
    phone: str = Form(""),
    priority: int = Form(0),
    db: Session = Depends(get_db),
):
    db.add(
        Contact(
            household_id=household_id,
            name=name,
            telegram_chat_id=telegram_chat_id or None,
            phone=phone or None,
            priority=priority,
        )
    )
    db.commit()
    return RedirectResponse(f"/households/{household_id}", status_code=303)


@router.post("/households/{household_id}/contacts/{contact_id}/test")
def test_contact_web(household_id: int, contact_id: int, db: Session = Depends(get_db)):
    contact = db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.household_id == household_id)
    ).scalar_one_or_none()
    household = db.get(Household, household_id)
    if contact is not None and contact.telegram_chat_id and household is not None:
        message = f"✅ Testnachricht von SensIR für {household.name}."
        success = send_telegram_message(contact.telegram_chat_id, message)
        db.add(
            AlertLog(
                household_id=household_id,
                contact_id=contact.id,
                message=f"[Testnachricht] {message}",
                success=success,
            )
        )
        db.commit()
    return RedirectResponse(f"/households/{household_id}", status_code=303)


@router.post("/households/{household_id}/toggle-active")
def toggle_household_active(household_id: int, db: Session = Depends(get_db)):
    household = db.get(Household, household_id)
    if household is not None:
        household.is_active = not household.is_active
        db.commit()
    return RedirectResponse(f"/households/{household_id}", status_code=303)


@router.post("/households/{household_id}/windows")
def add_window(
    household_id: int,
    start_time: str = Form(...),
    end_time: str = Form(...),
    min_actions: int = Form(2),
    weekday: str = Form(""),
    db: Session = Depends(get_db),
):
    db.add(
        ObservationWindow(
            household_id=household_id,
            weekday=int(weekday) if weekday != "" else None,
            start_time=dt.time.fromisoformat(start_time),
            end_time=dt.time.fromisoformat(end_time),
            min_actions=min_actions,
            source=WindowSource.manual,
        )
    )
    db.commit()
    return RedirectResponse(f"/households/{household_id}", status_code=303)
