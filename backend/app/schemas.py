import datetime as dt

from pydantic import BaseModel, ConfigDict

from app.models import CheckStatus, SensorKind, WindowSource


class HouseholdCreate(BaseModel):
    name: str
    timezone: str = "Europe/Berlin"


class HouseholdOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    timezone: str
    created_at: dt.datetime


class SensorCreate(BaseModel):
    household_id: int
    name: str
    kind: SensorKind = SensorKind.ir_bridge
    mqtt_topic: str | None = None      # nur kind=ir_bridge
    external_id: str | None = None     # nur kind=tuya|shelly (Cloud-Geräte-ID)
    config: dict = {}                  # z. B. {"room": "flur", "on_threshold_w": 15}


class SensorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    household_id: int
    name: str
    kind: SensorKind
    mqtt_topic: str | None
    external_id: str | None
    config: dict
    is_active: bool
    installed_at: dt.datetime
    last_seen_at: dt.datetime | None


class CloudDeviceOut(BaseModel):
    external_id: str | None
    name: str | None = None
    type: str | None = None
    online: bool | None = None


class ContactCreate(BaseModel):
    name: str
    telegram_chat_id: str | None = None
    phone: str | None = None
    priority: int = 0


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    household_id: int
    name: str
    telegram_chat_id: str | None
    phone: str | None
    priority: int
    is_active: bool


class ObservationWindowCreate(BaseModel):
    weekday: int | None = None
    start_time: dt.time
    end_time: dt.time
    min_actions: int = 2


class ObservationWindowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    household_id: int
    weekday: int | None
    start_time: dt.time
    end_time: dt.time
    min_actions: int
    source: WindowSource
    confidence: float | None
    is_active: bool


class ActivityCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    period_start: dt.datetime
    period_end: dt.datetime
    action_count: int
    status: CheckStatus
    checked_at: dt.datetime


class HouseholdStatusOut(BaseModel):
    household_id: int
    household_name: str
    status: CheckStatus | None
    last_event_at: dt.datetime | None
    events_since_midnight: int
    active_window: ObservationWindowOut | None
