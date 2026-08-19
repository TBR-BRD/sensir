from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Household, ObservationWindow, WindowSource
from app.schemas import ObservationWindowCreate, ObservationWindowOut

router = APIRouter(prefix="/households/{household_id}/windows", tags=["windows"])


@router.get("", response_model=list[ObservationWindowOut])
def list_windows(household_id: int, db: Session = Depends(get_db)):
    return db.execute(select(ObservationWindow).where(ObservationWindow.household_id == household_id)).scalars().all()


@router.post("", response_model=ObservationWindowOut, status_code=201)
def create_window(household_id: int, payload: ObservationWindowCreate, db: Session = Depends(get_db)):
    if db.get(Household, household_id) is None:
        raise HTTPException(404, "household not found")
    window = ObservationWindow(household_id=household_id, source=WindowSource.manual, **payload.model_dump())
    db.add(window)
    db.commit()
    db.refresh(window)
    return window
