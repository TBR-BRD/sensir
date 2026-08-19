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


class Household(Base):
    __tablename__ = "households"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Berlin")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    sensors: Mapped[list["Sensor"]] = relationship(back_populates="household", cascade="all, delete-orphan")
    windows: Mapped[list["ObservationWindow"]] = relationship(back_populates="household", cascade="all, delete-orphan")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="household", cascade="all, delete-orphan")


class Sensor(Base):
    __tablename__ = "sensors"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"))
    name: Mapped[str] = mapped_column(String(120))
    # Tasmota base topic, e.g. "sensir-01" -> listens on tele/sensir-01/RESULT
    mqtt_topic: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    installed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    household: Mapped[Household] = relationship(back_populates="sensors")
    ir_events: Mapped[list["IrEvent"]] = relationship(back_populates="sensor", cascade="all, delete-orphan")


class IrEvent(Base):
    """One received IR remote signal, as reported by the Tasmota IR bridge over MQTT."""

    __tablename__ = "ir_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensors.id"), index=True)
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True, default=dt.datetime.utcnow)
    protocol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_hex: Mapped[str | None] = mapped_column(String(32), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)

    sensor: Mapped[Sensor] = relationship(back_populates="ir_events")


class ObservationWindow(Base):
    """A time window in which a minimum number of IR actions is expected.

    Manually configured to start, later superseded per household by
    ML-derived windows once enough history exists (see app/ml/window_model.py).
    """

    __tablename__ = "observation_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"))
    # NULL weekday = applies every day
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
    """Outcome of evaluating one observation window instance for one day."""

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
