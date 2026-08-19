from fastapi import APIRouter

from app.api import contacts, households, sensors, status, windows

router = APIRouter(prefix="/api")
router.include_router(households.router)
router.include_router(sensors.router)
router.include_router(contacts.router)
router.include_router(windows.router)
router.include_router(status.router)
