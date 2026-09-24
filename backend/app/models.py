import datetime as dt
import enum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class WindowSource(str, enum.Enum):
    manual = "manual"
    ml = "ml"


class CheckStatus(str, enum.Enum):
    positive = "positive"
    negative = "negative"


class AlertChannel(str, enum.Enum):
    telegram = "telegram"


class SensorKind(str, enum.Enum):
    """Wie ein Sensor angebunden ist - bestimmt die Ingest-Quelle."""

    ir_bridge = "ir_bridge"   # Pearl/Tasmota IR-Bridge über MQTT
    tuya = "tuya"             # Tuya/SmartLife-Gerät über die Tuya Cloud
    shelly = "shelly"         # Shelly-Gerät über die Shelly Cloud


class Household(Base):
    __tablename__ = "households"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Berlin")
    # Pausiert Zeitfenster-/ML-Auswertung und Alarme, ohne Haushalt + Historie
    # zu löschen (z. B. während eines Klinikaufenthalts der beobachteten Person).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    sensors: Mapped[list["Sensor"]] = relationship(back_populates="household", cascade="all, delete-orphan")
    windows: Mapped[list["ObservationWindow"]] = relationship(back_populates="household", cascade="all, delete-orphan")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="household", cascade="all, delete-orphan")


class Sensor(Base):
    __tablename__ = "sensors"
    __table_args__ = (
        UniqueConstraint("kind", "external_id", name="uq_sensor_kind_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[SensorKind] = mapped_column(Enum(SensorKind), default=SensorKind.ir_bridge)

    # ir_bridge: Tasmota base topic ("sensir-01" -> tele/sensir-01/RESULT)
    mqtt_topic: Mapped[str | None] = mapped_column(String(120), unique=True, index=True, nullable=True)
    # tuya/shelly: Geräte-ID beim Cloud-Anbieter
    external_id: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)

    # Raum + quellenspezifische Feinjustierung, z. B. {"room": "flur", "on_threshold_w": 15}
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    installed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    household: Mapped[Household] = relationship(back_populates="sensors")
    events: Mapped[list["SensorEvent"]] = relationship(back_populates="sensor", cascade="all, delete-orphan")


class SensorEvent(Base):
    """Ein kanonisches Aktivitätsereignis - unabhängig von der Sensormarke.

    Für IR-Bridges: ein empfangenes Fernbedienungssignal. Für Tuya/Shelly:
    eine als "jemand aktiv" geltende Statusänderung (Bewegung, Tür, Gerät an …).
    Sicherheitsereignisse (Rauch/Gas) sind über `safety=True` markiert.
    """

    __tablename__ = "sensor_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensors.id"), index=True)
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True, default=dt.datetime.utcnow)

    kind: Mapped[str] = mapped_column(String(24), default="ir")   # ir|motion|door|window|button|light|appliance_on|appliance_off|presence|smoke|gas
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    safety: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # nur IR
    protocol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_hex: Mapped[str | None] = mapped_column(String(32), nullable=True)

    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)

    sensor: Mapped[Sensor] = relationship(back_populates="events")


class ObservationWindow(Base):
    __tablename__ = "observation_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"))
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_time: Mapped[dt.time] = mapped_column(Time)
    end_time: Mapped[dt.time] = mapped_column(Time)
    min_actions: Mapped[int] = mapped_column(Integer, default=2)
    source: Mapped[WindowSource] = mapped_column(Enum(WindowSource), default=WindowSource.manual)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )

    household: Mapped[Household] = relationship(back_populates="windows")


class ActivityCheck(Base):
    __tablename__ = "activity_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    window_id: Mapped[int | None] = mapped_column(ForeignKey("observation_windows.id"), nullable=True)
    period_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    action_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[CheckStatus] = mapped_column(Enum(CheckStatus))
    checked_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    household: Mapped[Household] = relationship()


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"))
    name: Mapped[str] = mapped_column(String(120))
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    household: Mapped[Household] = relationship(back_populates="contacts")


class AlertLog(Base):
    __tablename__ = "alert_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    channel: Mapped[AlertChannel] = mapped_column(Enum(AlertChannel), default=AlertChannel.telegram)
    message: Mapped[str] = mapped_column(String(500))
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    household: Mapped[Household] = relationship()
