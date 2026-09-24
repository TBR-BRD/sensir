from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Household
from app.schemas import HouseholdCreate, HouseholdOut, HouseholdUpdate

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


@router.delete("/{household_id}", status_code=204)
def delete_household(household_id: int, db: Session = Depends(get_db)):
    """Löscht den Haushalt inkl. Sensoren, Ereignisse, Kontakte, Historie
    (cascade). Zum reinen Pausieren ohne Datenverlust: PATCH is_active=false."""
    household = db.get(Household, household_id)
    if household is None:
        raise HTTPException(404, "household not found")
    db.delete(household)
    db.commit()
