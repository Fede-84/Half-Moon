"""add order number sequence

Revision ID: 39265b1a70b8
Revises: ffff38014400
Create Date: 2026-09-29 11:24:31.284866

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "39265b1a70b8"
down_revision: Union[str, Sequence[str], None] = "ffff38014400"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE half_moon_order_number_seq START WITH 1 INCREMENT BY 1")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS half_moon_order_number_seq")
