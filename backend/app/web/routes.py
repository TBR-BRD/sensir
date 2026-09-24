import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerting.telegram import send_telegram_message
from app.db import get_db
from app.models import AlertLog, Contact, Household, SensorEvent, ObservationWindow, Sensor, WindowSource
from app.status_service import compute_status

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory="app/web/templates")


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    households = db.execute(select(Household)).scalars().all()
    statuses = [compute_status(db, h) for h in households]
    return templates.TemplateResponse("dashboard.html", {"request": request, "statuses": statuses})


@router.get("/households/{household_id}")
def household_detail(request: Request, household_id: int, db: Session = Depends(get_db)):
    household = db.get(Household, household_id)
    status = compute_status(db, household)
    sensors = db.execute(select(Sensor).where(Sensor.household_id == household_id)).scalars().all()
    contacts = db.execute(
        select(Contact).where(Contact.household_id == household_id).order_by(Contact.priority)
    ).scalars().all()
    windows = db.execute(select(ObservationWindow).where(ObservationWindow.household_id == household_id)).scalars().all()
    recent_events = db.execute(
        select(SensorEvent)
        .join(Sensor, Sensor.id == SensorEvent.sensor_id)
        .where(Sensor.household_id == household_id)
        .order_by(SensorEvent.received_at.desc())
        .limit(20)
    ).scalars().all()
    return templates.TemplateResponse(
        "household.html",
        {
            "request": request,
            "household": household,
            "status": status,
            "sensors": sensors,
            "contacts": contacts,
            "windows": windows,
            "recent_events": recent_events,
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
