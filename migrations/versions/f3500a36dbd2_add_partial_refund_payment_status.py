"""add partial refund payment status

Revision ID: f3500a36dbd2
Revises: 49290ab28428
Create Date: 2026-10-01 16:13:43.498463

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f3500a36dbd2'
down_revision: Union[str, Sequence[str], None] = '49290ab28428'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_payments_status",
        "payments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_payments_status",
        "payments",
        "status IN ('PENDING', 'PAID', 'PARTIALLY_REFUNDED', 'FAILED', 'REFUNDED')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_payments_status",
        "payments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_payments_status",
        "payments",
        "status IN ('PENDING', 'PAID', 'FAILED', 'REFUNDED')",
    )
