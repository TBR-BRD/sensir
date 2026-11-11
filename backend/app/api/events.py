from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Household
from app.schemas import SensorEventOut
from app.status_service import recent_events

router = APIRouter(prefix="/households/{household_id}/events", tags=["events"])


@router.get("", response_model=list[SensorEventOut])
def list_recent_events(
    household_id: int,
    limit: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
):
    """Alle Sensorereignisse des Haushalts, neueste zuerst.

    Mit optionalem `limit` kann die Antwort auf die neuesten Ereignisse
    begrenzt werden. Genutzt u. a. von der Home-Assistant-Lovelace-Karte.
    """
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
