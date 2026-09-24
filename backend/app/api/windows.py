from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Household, ObservationWindow, WindowSource
from app.schemas import ObservationWindowCreate, ObservationWindowOut, ObservationWindowUpdate

router = APIRouter(prefix="/households/{household_id}/windows", tags=["windows"])


def _get_window(db: Session, household_id: int, window_id: int) -> ObservationWindow:
    window = db.execute(
        select(ObservationWindow).where(
            ObservationWindow.id == window_id, ObservationWindow.household_id == household_id
        )
    ).scalar_one_or_none()
    if window is None:
        raise HTTPException(404, "window not found")
    return window


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


@router.patch("/{window_id}", response_model=ObservationWindowOut)
def update_window(
    household_id: int, window_id: int, payload: ObservationWindowUpdate, db: Session = Depends(get_db)
):
    window = _get_window(db, household_id, window_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(window, field, value)
    # eine manuell bearbeitete Wert bleibt vom Nutzer verantwortet, nicht ML
    window.source = WindowSource.manual
    db.commit()
    db.refresh(window)
    return window


@router.delete("/{window_id}", status_code=204)
def delete_window(household_id: int, window_id: int, db: Session = Depends(get_db)):
    window = _get_window(db, household_id, window_id)
    db.delete(window)
    db.commit()
