"""multi-source: ir_events -> sensor_events, sensors gain kind/external_id/config

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

sensor_kind = sa.Enum("ir_bridge", "tuya", "shelly", name="sensorkind")


def upgrade() -> None:
    bind = op.get_bind()
    sensor_kind.create(bind, checkfirst=True)

    # -- sensors ------------------------------------------------------------
    op.add_column(
        "sensors",
        sa.Column("kind", sensor_kind, nullable=False, server_default="ir_bridge"),
    )
    op.add_column("sensors", sa.Column("external_id", sa.String(120), nullable=True))
    op.add_column(
        "sensors",
        sa.Column("config", sa.JSON, nullable=False, server_default="{}"),
    )
    op.add_column(
        "sensors", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.alter_column("sensors", "mqtt_topic", existing_type=sa.String(120), nullable=True)
    op.create_index("ix_sensors_external_id", "sensors", ["external_id"])
    op.create_unique_constraint(
        "uq_sensor_kind_external", "sensors", ["kind", "external_id"]
    )

    # -- ir_events -> sensor_events --------------------------------------
    op.rename_table("ir_events", "sensor_events")
    op.add_column(
        "sensor_events",
        sa.Column("kind", sa.String(24), nullable=False, server_default="ir"),
    )
    op.add_column("sensor_events", sa.Column("value", sa.Float, nullable=True))
    op.add_column(
        "sensor_events",
        sa.Column("safety", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_sensor_events_safety", "sensor_events", ["safety"])
    # Indizes umbenennen (SQLite ignoriert das ggf. still)
    try:
        op.execute("ALTER INDEX ix_ir_events_sensor_id RENAME TO ix_sensor_events_sensor_id")
        op.execute("ALTER INDEX ix_ir_events_received_at RENAME TO ix_sensor_events_received_at")
    except Exception:  # noqa: BLE001
        pass


def downgrade() -> None:
    op.drop_index("ix_sensor_events_safety", table_name="sensor_events")
    op.drop_column("sensor_events", "safety")
    op.drop_column("sensor_events", "value")
    op.drop_column("sensor_events", "kind")
    op.rename_table("sensor_events", "ir_events")

    op.drop_constraint("uq_sensor_kind_external", "sensors", type_="unique")
    op.drop_index("ix_sensors_external_id", table_name="sensors")
    op.alter_column("sensors", "mqtt_topic", existing_type=sa.String(120), nullable=False)
    op.drop_column("sensors", "last_seen_at")
    op.drop_column("sensors", "config")
    op.drop_column("sensors", "external_id")
    op.drop_column("sensors", "kind")
    sensor_kind.drop(op.get_bind(), checkfirst=True)
