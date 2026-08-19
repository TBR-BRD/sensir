from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Contact, Household
from app.schemas import ContactCreate, ContactOut

router = APIRouter(prefix="/households/{household_id}/contacts", tags=["contacts"])


@router.get("", response_model=list[ContactOut])
def list_contacts(household_id: int, db: Session = Depends(get_db)):
    return db.execute(select(Contact).where(Contact.household_id == household_id)).scalars().all()


@router.post("", response_model=ContactOut, status_code=201)
def create_contact(household_id: int, payload: ContactCreate, db: Session = Depends(get_db)):
    if db.get(Household, household_id) is None:
        raise HTTPException(404, "household not found")
    contact = Contact(household_id=household_id, **payload.model_dump())
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact
