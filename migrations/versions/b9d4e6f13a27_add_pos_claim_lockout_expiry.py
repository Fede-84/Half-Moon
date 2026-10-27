"""add POS claim lockout expiry

Revision ID: b9d4e6f13a27
Revises: a41c7e92b6d3
Create Date: 2026-10-21 10:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9d4e6f13a27"
down_revision: str | Sequence[str] | None = "a41c7e92b6d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pos_order_claims",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pos_order_claims", "locked_until")
