from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Sensor, SensorKind
from app.schemas import SensorCreate, SensorOut, SensorUpdate

router = APIRouter(prefix="/sensors", tags=["sensors"])


@router.get("", response_model=list[SensorOut])
def list_sensors(household_id: int | None = None, db: Session = Depends(get_db)):
    query = select(Sensor)
    if household_id is not None:
        query = query.where(Sensor.household_id == household_id)
    return db.execute(query).scalars().all()


@router.post("", response_model=SensorOut, status_code=201)
def create_sensor(payload: SensorCreate, db: Session = Depends(get_db)):
    if payload.kind == SensorKind.ir_bridge and not payload.mqtt_topic:
        raise HTTPException(422, "mqtt_topic ist für kind=ir_bridge erforderlich")
    if payload.kind in (SensorKind.tuya, SensorKind.shelly) and not payload.external_id:
        raise HTTPException(422, "external_id ist für Cloud-Sensoren erforderlich")

    sensor = Sensor(**payload.model_dump())
    db.add(sensor)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Sensor mit diesem Topic bzw. dieser Geräte-ID existiert bereits")
    db.refresh(sensor)
    return sensor


@router.patch("/{sensor_id}", response_model=SensorOut)
def update_sensor(sensor_id: int, payload: SensorUpdate, db: Session = Depends(get_db)):
    sensor = db.get(Sensor, sensor_id)
    if sensor is None:
        raise HTTPException(404, "Sensor nicht gefunden")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(sensor, field, value)
    db.commit()
    db.refresh(sensor)
    return sensor


@router.delete("/{sensor_id}", status_code=204)
def delete_sensor(sensor_id: int, db: Session = Depends(get_db)):
    sensor = db.get(Sensor, sensor_id)
    if sensor is None:
        raise HTTPException(404, "Sensor nicht gefunden")
    db.delete(sensor)
    db.commit()
