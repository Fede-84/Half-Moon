"""allow partially refunded order status

Revision ID: 3af7864d66b0
Revises: 6c42aeb1f903
Create Date: 2026-10-01 18:44:59.925768

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3af7864d66b0"
down_revision: Union[str, Sequence[str], None] = "6c42aeb1f903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_orders_payment_status",
        "orders",
        type_="check",
    )
    op.create_check_constraint(
        "ck_orders_payment_status",
        "orders",
        "payment_status IN "
        "('UNPAID', 'PAID', 'FAILED', 'PARTIALLY_REFUNDED', 'REFUNDED')",
    )


def downgrade() -> None:
    has_partial_refunds = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS "
            "(SELECT 1 FROM orders WHERE payment_status = 'PARTIALLY_REFUNDED')"
        )
    )

    if has_partial_refunds:
        raise RuntimeError(
            "Cannot downgrade while orders have PARTIALLY_REFUNDED status"
        )

    op.drop_constraint(
        "ck_orders_payment_status",
        "orders",
        type_="check",
    )
    op.create_check_constraint(
        "ck_orders_payment_status",
        "orders",
        "payment_status IN ('UNPAID', 'PAID', 'FAILED', 'REFUNDED')",
    )
