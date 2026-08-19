from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Household
from app.schemas import HouseholdStatusOut
from app.status_service import compute_status

router = APIRouter(prefix="/households/{household_id}/status", tags=["status"])


@router.get("", response_model=HouseholdStatusOut)
def get_status(household_id: int, db: Session = Depends(get_db)):
    household = db.get(Household, household_id)
    if household is None:
        raise HTTPException(404, "household not found")
    return compute_status(db, household)
