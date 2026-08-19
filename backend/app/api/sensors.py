from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Sensor
from app.schemas import SensorCreate, SensorOut

router = APIRouter(prefix="/sensors", tags=["sensors"])


@router.get("", response_model=list[SensorOut])
def list_sensors(db: Session = Depends(get_db)):
    return db.execute(select(Sensor)).scalars().all()


@router.post("", response_model=SensorOut, status_code=201)
def create_sensor(payload: SensorCreate, db: Session = Depends(get_db)):
    sensor = Sensor(**payload.model_dump())
    db.add(sensor)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "mqtt_topic already registered to a sensor")
    db.refresh(sensor)
    return sensor
