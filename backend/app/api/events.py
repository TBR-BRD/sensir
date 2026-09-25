from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Household
from app.schemas import SensorEventOut
from app.status_service import recent_events

router = APIRouter(prefix="/households/{household_id}/events", tags=["events"])


@router.get("", response_model=list[SensorEventOut])
def list_recent_events(household_id: int, limit: int = 10, db: Session = Depends(get_db)):
    """Die letzten `limit` (Standard 10) Sensorereignisse des Haushalts,
    neueste zuerst. Genutzt u. a. von der Home-Assistant-Lovelace-Karte."""
    household = db.get(Household, household_id)
    if household is None:
        raise HTTPException(404, "household not found")
    events = recent_events(db, household, limit=limit)
    return [
        {
            "received_at": e["received_at_local"],
            "sensor_name": e["sensor_name"],
            "kind": e["kind"],
            "value": e["value"],
            "safety": e["safety"],
        }
        for e in events
    ]
