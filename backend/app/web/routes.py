import datetime as dt

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.alerting.telegram import send_telegram_message
from app.config import settings
from app.db import get_db
from app.models import AlertLog, Contact, Household, ObservationWindow, Sensor, SensorKind, WindowSource
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
    if household is None:
        raise HTTPException(404, "household not found")
    status = compute_status(db, household)
    sensors = db.execute(select(Sensor).where(Sensor.household_id == household_id)).scalars().all()
    contacts = db.execute(
        select(Contact).where(Contact.household_id == household_id).order_by(Contact.priority)
    ).scalars().all()
    windows = db.execute(select(ObservationWindow).where(ObservationWindow.household_id == household_id)).scalars().all()
    events = recent_events_service(db, household, limit=20)
    sensor_names = {s.id: s.name for s in sensors}
    return templates.TemplateResponse(
        "household.html",
        {
            "request": request,
            "household": household,
            "status": status,
            "sensors": sensors,
            "sensor_names": sensor_names,
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


def _send_contact_test_message(db: Session, household_id: int, contact_id: int) -> None:
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


@router.post("/households/{household_id}/contacts/{contact_id}/test")
def test_contact_web(household_id: int, contact_id: int, db: Session = Depends(get_db)):
    _send_contact_test_message(db, household_id, contact_id)
    return RedirectResponse(f"/households/{household_id}", status_code=303)


@router.post("/households/{household_id}/toggle-active")
def toggle_household_active(household_id: int, db: Session = Depends(get_db)):
    household = db.get(Household, household_id)
    if household is not None:
        household.is_active = not household.is_active
        db.commit()
    return RedirectResponse(f"/households/{household_id}", status_code=303)


def _discover_unassigned_devices(db: Session) -> dict:
    """Tuya-/Shelly-Cloud-Geräte, die noch keinem Sensor zugeordnet sind -
    gemeinsam genutzt vom Onboarding-Assistenten und vom "Sensor
    hinzufügen"-Formular auf der Haushaltsseite."""
    already_assigned = {
        (s.kind, s.external_id) for s in db.execute(select(Sensor)).scalars().all()
    }

    tuya_devices: list[dict] | None = None
    tuya_error: str | None = None
    if settings.tuya_enabled:
        try:
            from app.ingest.tuya import list_cloud_devices as list_tuya_devices

            tuya_devices = [
                d for d in list_tuya_devices()
                if (SensorKind.tuya, d.get("external_id")) not in already_assigned
            ]
        except Exception as exc:  # noqa: BLE001
            tuya_error = str(exc)
            tuya_devices = []

    shelly_devices: list[dict] | None = None
    shelly_error: str | None = None
    if settings.shelly_enabled:
        try:
            from app.ingest.shelly import list_cloud_devices as list_shelly_devices

            shelly_devices = [
                d for d in list_shelly_devices()
                if (SensorKind.shelly, d.get("external_id")) not in already_assigned
            ]
        except Exception as exc:  # noqa: BLE001
            shelly_error = str(exc)
            shelly_devices = []

    return {
        "tuya_devices": tuya_devices,
        "tuya_error": tuya_error,
        "shelly_devices": shelly_devices,
        "shelly_error": shelly_error,
    }


def _add_sensors_from_form(db: Session, household_id: int, form) -> None:
    """Legt Sensoren aus einem `_sensor_picker.html`-Formular an (Tuya-/
    Shelly-Checkboxen `add_<kind>_<external_id>` + optionale IR-Bridge-
    Felder). Wirft `IntegrityError` unverändert weiter, damit der Aufrufer
    entscheidet, wohin bei einer Race-Condition (Gerät zwischenzeitlich
    anderswo zugeordnet) zurückgeleitet wird."""
    for key in form.keys():
        if key.startswith("add_tuya_"):
            external_id = key[len("add_tuya_"):]
            name = (form.get(f"name_tuya_{external_id}") or "").strip() or external_id
            db.add(Sensor(household_id=household_id, name=name, kind=SensorKind.tuya, external_id=external_id))
        elif key.startswith("add_shelly_"):
            external_id = key[len("add_shelly_"):]
            name = (form.get(f"name_shelly_{external_id}") or "").strip() or external_id
            db.add(Sensor(household_id=household_id, name=name, kind=SensorKind.shelly, external_id=external_id))

    ir_topic = (form.get("ir_mqtt_topic") or "").strip()
    if ir_topic:
        ir_name = (form.get("ir_name") or "").strip() or ir_topic
        db.add(Sensor(household_id=household_id, name=ir_name, kind=SensorKind.ir_bridge, mqtt_topic=ir_topic))

    db.commit()


@router.get("/households/{household_id}/sensors/add")
def household_add_sensors_form(request: Request, household_id: int, db: Session = Depends(get_db)):
    household = db.get(Household, household_id)
    if household is None:
        raise HTTPException(404, "household not found")
    return templates.TemplateResponse(
        "household_add_sensors.html",
        {"request": request, "household": household, **_discover_unassigned_devices(db)},
    )


@router.post("/households/{household_id}/sensors/add")
async def household_add_sensors(household_id: int, request: Request, db: Session = Depends(get_db)):
    if db.get(Household, household_id) is None:
        raise HTTPException(404, "household not found")
    form = await request.form()
    try:
        _add_sensors_from_form(db, household_id, form)
    except IntegrityError:
        # z. B. ein Gerät wurde zwischenzeitlich schon anderswo zugeordnet
        # (Race) - einfach zur selben Seite zurück, dort taucht es dann nicht
        # mehr in der Auswahl auf.
        db.rollback()
        return RedirectResponse(f"/households/{household_id}/sensors/add", status_code=303)
    return RedirectResponse(f"/households/{household_id}", status_code=303)


@router.post("/households/{household_id}/sensors/{sensor_id}/edit")
def household_edit_sensor(
    household_id: int,
    sensor_id: int,
    name: str = Form(...),
    is_active: str = Form(""),
    db: Session = Depends(get_db),
):
    sensor = db.execute(
        select(Sensor).where(Sensor.id == sensor_id, Sensor.household_id == household_id)
    ).scalar_one_or_none()
    if sensor is not None:
        sensor.name = name.strip() or sensor.name
        sensor.is_active = is_active == "on"
        db.commit()
    return RedirectResponse(f"/households/{household_id}", status_code=303)


@router.post("/households/{household_id}/sensors/{sensor_id}/delete")
def household_delete_sensor(household_id: int, sensor_id: int, db: Session = Depends(get_db)):
    sensor = db.execute(
        select(Sensor).where(Sensor.id == sensor_id, Sensor.household_id == household_id)
    ).scalar_one_or_none()
    if sensor is not None:
        db.delete(sensor)
        db.commit()
    return RedirectResponse(f"/households/{household_id}", status_code=303)


@router.post("/households/{household_id}/delete")
def household_delete(household_id: int, db: Session = Depends(get_db)):
    household = db.get(Household, household_id)
    if household is not None:
        db.delete(household)
        db.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/households/{household_id}/windows")
def add_window(
    household_id: int,
    start_time: str = Form(...),
    end_time: str = Form(...),
    min_actions: int = Form(2),
    weekday: str = Form(""),
    sensor_id: str = Form(""),
    db: Session = Depends(get_db),
):
    db.add(
        ObservationWindow(
            household_id=household_id,
            weekday=int(weekday) if weekday != "" else None,
            start_time=dt.time.fromisoformat(start_time),
            end_time=dt.time.fromisoformat(end_time),
            min_actions=min_actions,
            sensor_id=int(sensor_id) if sensor_id != "" else None,
            source=WindowSource.manual,
        )
    )
    db.commit()
    return RedirectResponse(f"/households/{household_id}", status_code=303)


# -- Onboarding: geführter Assistent für neue Haushalte --------------------
# Schritt 1 Haushalt -> Schritt 2 Sensoren (aus Tuya/Shelly-Cloud auswählen,
# IR-Bridge manuell) -> Schritt 3 Kontakt(e) + Testnachricht -> fertig, zur
# normalen Haushaltsseite. Vorher ging das Anlegen eines Haushalts nur per
# curl/API, alles Weitere (Sensor/Kontakt/Fenster) schon über die normale
# Haushaltsseite - dieser Assistent führt nur linear durch denselben Weg.


@router.get("/onboarding")
def onboarding_start(request: Request):
    return templates.TemplateResponse("onboarding_household.html", {"request": request})


@router.post("/onboarding")
def onboarding_create_household(
    name: str = Form(...), timezone: str = Form("Europe/Berlin"), db: Session = Depends(get_db)
):
    household = Household(name=name, timezone=timezone or "Europe/Berlin")
    db.add(household)
    db.commit()
    db.refresh(household)
    return RedirectResponse(f"/onboarding/{household.id}/sensors", status_code=303)


@router.get("/onboarding/{household_id}/sensors")
def onboarding_sensors(request: Request, household_id: int, db: Session = Depends(get_db)):
    household = db.get(Household, household_id)
    if household is None:
        raise HTTPException(404, "household not found")

    own_sensors = db.execute(select(Sensor).where(Sensor.household_id == household_id)).scalars().all()

    return templates.TemplateResponse(
        "onboarding_sensors.html",
        {
            "request": request,
            "household": household,
            "own_sensors": own_sensors,
            **_discover_unassigned_devices(db),
        },
    )


@router.post("/onboarding/{household_id}/sensors")
async def onboarding_add_sensors(household_id: int, request: Request, db: Session = Depends(get_db)):
    if db.get(Household, household_id) is None:
        raise HTTPException(404, "household not found")

    form = await request.form()
    try:
        _add_sensors_from_form(db, household_id, form)
    except IntegrityError:
        # z. B. ein Gerät wurde zwischenzeitlich schon anderswo zugeordnet
        # (Race) - einfach zur selben Seite zurück, dort taucht es dann nicht
        # mehr in der Auswahl auf.
        db.rollback()
        return RedirectResponse(f"/onboarding/{household_id}/sensors", status_code=303)

    return RedirectResponse(f"/onboarding/{household_id}/contact", status_code=303)


@router.get("/onboarding/{household_id}/contact")
def onboarding_contact_step(request: Request, household_id: int, db: Session = Depends(get_db)):
    household = db.get(Household, household_id)
    if household is None:
        raise HTTPException(404, "household not found")
    contacts = db.execute(
        select(Contact).where(Contact.household_id == household_id).order_by(Contact.priority)
    ).scalars().all()
    return templates.TemplateResponse(
        "onboarding_contact.html",
        {"request": request, "household": household, "contacts": contacts},
    )


@router.post("/onboarding/{household_id}/contact")
def onboarding_add_contact(
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
    return RedirectResponse(f"/onboarding/{household_id}/contact", status_code=303)


@router.post("/onboarding/{household_id}/contact/{contact_id}/test")
def onboarding_test_contact(household_id: int, contact_id: int, db: Session = Depends(get_db)):
    _send_contact_test_message(db, household_id, contact_id)
    return RedirectResponse(f"/onboarding/{household_id}/contact", status_code=303)
