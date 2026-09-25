import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.export import export_and_send
from app.models import Household
from app.schemas import ExportResult, HouseholdCreate, HouseholdOut, HouseholdUpdate

router = APIRouter(prefix="/households", tags=["households"])


@router.get("", response_model=list[HouseholdOut])
def list_households(db: Session = Depends(get_db)):
    return db.execute(select(Household)).scalars().all()


@router.post("", response_model=HouseholdOut, status_code=201)
def create_household(payload: HouseholdCreate, db: Session = Depends(get_db)):
    household = Household(**payload.model_dump())
    db.add(household)
    db.commit()
    db.refresh(household)
    return household


@router.get("/{household_id}", response_model=HouseholdOut)
def get_household(household_id: int, db: Session = Depends(get_db)):
    household = db.get(Household, household_id)
    if household is None:
        raise HTTPException(404, "household not found")
    return household


@router.patch("/{household_id}", response_model=HouseholdOut)
def update_household(household_id: int, payload: HouseholdUpdate, db: Session = Depends(get_db)):
    household = db.get(Household, household_id)
    if household is None:
        raise HTTPException(404, "household not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(household, field, value)
    db.commit()
    db.refresh(household)
    return household


@router.post("/{household_id}/export", response_model=ExportResult)
def trigger_export(household_id: int, days: int = 7, db: Session = Depends(get_db)):
    """Löst sofort einen CSV-Datenexport aus (statt auf den wöchentlichen
    Scheduler-Job zu warten, siehe app/export.py) - nützlich zum Testen und
    für einen Export außerhalb des normalen Wochenrhythmus."""
    household = db.get(Household, household_id)
    if household is None:
        raise HTTPException(404, "household not found")
    until = dt.datetime.now(dt.timezone.utc)
    since = until - dt.timedelta(days=days)
    results = export_and_send(db, household, since, until)
    if not results:
        return ExportResult(sent_to=0, failed=0, detail="Kein aktiver Kontakt mit telegram_chat_id")
    sent = sum(1 for _, ok in results if ok)
    failed = len(results) - sent
    return ExportResult(
        sent_to=sent, failed=failed,
        detail=f"{sent} von {len(results)} Kontakten erreicht" if failed else "an alle Kontakte gesendet",
    )


@router.delete("/{household_id}", status_code=204)
def delete_household(household_id: int, db: Session = Depends(get_db)):
    """Löscht den Haushalt inkl. Sensoren, Ereignisse, Kontakte, Historie
    (cascade). Zum reinen Pausieren ohne Datenverlust: PATCH is_active=false."""
    household = db.get(Household, household_id)
    if household is None:
        raise HTTPException(404, "household not found")
    db.delete(household)
    db.commit()
