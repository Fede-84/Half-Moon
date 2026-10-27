"""align payment status names

Revision ID: 0d28888aaa51
Revises: 0a1fa2af00ec
Create Date: 2026-09-29 16:34:03.813506

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0d28888aaa51"
down_revision: Union[str, Sequence[str], None] = "0a1fa2af00ec"
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
        "status IN ('PENDING', 'PAID', 'FAILED', 'REFUNDED')",
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
        "status IN ('PENDING', 'SUCCEEDED', 'FAILED', 'REFUNDED')",
    )
