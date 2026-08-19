"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

window_source = sa.Enum("manual", "ml", name="windowsource")
check_status = sa.Enum("positive", "negative", name="checkstatus")
alert_channel = sa.Enum("telegram", name="alertchannel")


def upgrade() -> None:
    op.create_table(
        "households",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Berlin"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "sensors",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("mqtt_topic", sa.String(120), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_sensors_mqtt_topic", "sensors", ["mqtt_topic"])

    op.create_table(
        "ir_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sensor_id", sa.Integer, sa.ForeignKey("sensors.id"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("protocol", sa.String(32), nullable=True),
        sa.Column("bits", sa.Integer, nullable=True),
        sa.Column("data_hex", sa.String(32), nullable=True),
        sa.Column("raw_payload", sa.JSON, nullable=False, server_default="{}"),
    )
    op.create_index("ix_ir_events_sensor_id", "ir_events", ["sensor_id"])
    op.create_index("ix_ir_events_received_at", "ir_events", ["received_at"])

    op.create_table(
        "observation_windows",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id"), nullable=False),
        sa.Column("weekday", sa.Integer, nullable=True),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.Column("min_actions", sa.Integer, nullable=False, server_default="2"),
        sa.Column("source", window_source, nullable=False, server_default="manual"),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "activity_checks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id"), nullable=False),
        sa.Column("window_id", sa.Integer, sa.ForeignKey("observation_windows.id"), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", check_status, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_activity_checks_household_id", "activity_checks", ["household_id"])

    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("telegram_chat_id", sa.String(64), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "alert_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id"), nullable=False),
        sa.Column("contact_id", sa.Integer, sa.ForeignKey("contacts.id"), nullable=True),
        sa.Column("channel", alert_channel, nullable=False, server_default="telegram"),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("success", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_alert_log_household_id", "alert_log", ["household_id"])


def downgrade() -> None:
    op.drop_table("alert_log")
    op.drop_table("contacts")
    op.drop_table("activity_checks")
    op.drop_table("observation_windows")
    op.drop_table("ir_events")
    op.drop_table("sensors")
    op.drop_table("households")
    check_status.drop(op.get_bind(), checkfirst=True)
    window_source.drop(op.get_bind(), checkfirst=True)
    alert_channel.drop(op.get_bind(), checkfirst=True)
