"""commit reservations for paid orders

Revision ID: 6c42aeb1f903
Revises: f7582083fb05
Create Date: 2026-10-01

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6c42aeb1f903"
down_revision: Union[str, Sequence[str], None] = "f7582083fb05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE reservations AS reservation
            SET status = 'COMMITTED'
            FROM orders AS order_record
            WHERE order_record.id = reservation.order_id
              AND order_record.order_status = 'PAID'
              AND reservation.status = 'ACTIVE'
            """
        )
    )


def downgrade() -> None:
    # Preserve committed history; reverting it could reopen paid reservations.
    pass
