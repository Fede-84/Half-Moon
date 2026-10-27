"""add active reservation inventory unit uniqueness

Revision ID: 8d72372141d5
Revises: 464acebca437
Create Date: 2026-10-07 14:07:05.078068

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8d72372141d5'
down_revision: Union[str, Sequence[str], None] = '464acebca437'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_reservations_active_inventory_unit",
        "reservations",
        ["inventory_unit_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_reservations_active_inventory_unit",
        table_name="reservations",
    )
