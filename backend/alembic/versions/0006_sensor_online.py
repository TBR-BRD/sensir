"""Track sensor reachability for offline notifications.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sensors", sa.Column("online", sa.Boolean(), nullable=True))
    op.add_column("sensors", sa.Column("offline_since", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "sensors",
        sa.Column("offline_notified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("sensors", "offline_notified")
    op.drop_column("sensors", "offline_since")
    op.drop_column("sensors", "online")
