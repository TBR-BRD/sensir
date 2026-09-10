"""Discovery: Cloud-Geräte auflisten, damit man daraus Sensoren anlegen kann."""

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.schemas import CloudDeviceOut

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/tuya/devices", response_model=list[CloudDeviceOut])
def tuya_devices():
    if not settings.tuya_enabled or not settings.tuya_access_id:
        raise HTTPException(400, "Tuya ist nicht konfiguriert (TUYA_* in .env)")
    try:
        from app.ingest.tuya import list_cloud_devices

        return list_cloud_devices()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Tuya-Cloud: {exc}")


@router.get("/shelly/devices", response_model=list[CloudDeviceOut])
def shelly_devices():
    if not settings.shelly_enabled or not settings.shelly_auth_key:
        raise HTTPException(400, "Shelly ist nicht konfiguriert (SHELLY_* in .env)")
    try:
        from app.ingest.shelly import list_cloud_devices

        return list_cloud_devices()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Shelly-Cloud: {exc}")
