"""observation_windows: add sensor_id (scope a window to one sensor)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "observation_windows",
        sa.Column("sensor_id", sa.Integer, sa.ForeignKey("sensors.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("observation_windows", "sensor_id")
