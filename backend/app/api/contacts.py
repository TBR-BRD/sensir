from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerting.telegram import send_telegram_message
from app.db import get_db
from app.models import AlertLog, Contact, Household
from app.schemas import ContactCreate, ContactOut, ContactTestResult, ContactUpdate

router = APIRouter(prefix="/households/{household_id}/contacts", tags=["contacts"])


def _get_contact(db: Session, household_id: int, contact_id: int) -> Contact:
    contact = db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.household_id == household_id)
    ).scalar_one_or_none()
    if contact is None:
        raise HTTPException(404, "contact not found")
    return contact


@router.get("", response_model=list[ContactOut])
def list_contacts(household_id: int, db: Session = Depends(get_db)):
    return (
        db.execute(
            select(Contact)
            .where(Contact.household_id == household_id)
            .order_by(Contact.priority)
        )
        .scalars()
        .all()
    )


@router.post("", response_model=ContactOut, status_code=201)
def create_contact(household_id: int, payload: ContactCreate, db: Session = Depends(get_db)):
    if db.get(Household, household_id) is None:
        raise HTTPException(404, "household not found")
    contact = Contact(household_id=household_id, **payload.model_dump())
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


@router.patch("/{contact_id}", response_model=ContactOut)
def update_contact(
    household_id: int, contact_id: int, payload: ContactUpdate, db: Session = Depends(get_db)
):
    contact = _get_contact(db, household_id, contact_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)
    db.commit()
    db.refresh(contact)
    return contact


@router.delete("/{contact_id}", status_code=204)
def delete_contact(household_id: int, contact_id: int, db: Session = Depends(get_db)):
    contact = _get_contact(db, household_id, contact_id)
    db.delete(contact)
    db.commit()


@router.post("/{contact_id}/test", response_model=ContactTestResult)
def test_contact(household_id: int, contact_id: int, db: Session = Depends(get_db)):
    """Schickt sofort eine Testnachricht - so lässt sich eine chat_id beim
    Anlegen verifizieren, statt erst beim nächsten echten Alarm zu merken,
    dass sie falsch ist."""
    contact = _get_contact(db, household_id, contact_id)
    if not contact.telegram_chat_id:
        raise HTTPException(422, "Kontakt hat keine telegram_chat_id hinterlegt")

    household = db.get(Household, household_id)
    message = (
        f"✅ Testnachricht von SensIR für {household.name if household else household_id}. "
        f"Wenn du das liest, ist dein Telegram-Kontakt korrekt eingerichtet."
    )
    success = send_telegram_message(contact.telegram_chat_id, message)
    db.add(
        AlertLog(
            household_id=household_id,
            contact_id=contact.id,
            message=f"[Testnachricht] {message}",
            success=success,
        )
    )
    db.commit()
    return ContactTestResult(
        success=success,
        detail="Nachricht gesendet." if success else "Senden fehlgeschlagen - Bot-Token/chat_id prüfen.",
    )
