"""Allow terminally failed pickup shipments to be quarantined."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f31bd892a6e4"
down_revision: str | Sequence[str] | None = "e5a41b0c9d72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_shipments_status", "shipments", type_="check")
    op.create_check_constraint(
        "ck_shipments_status",
        "shipments",
        "status IN "
        "('READY_TO_SHIP', 'READY_FOR_PICKUP', 'SHIPPED', 'DELIVERED', "
        "'PICKED_UP', 'CANCELLED', 'FAILED')",
    )


def downgrade() -> None:
    failed_shipment_exists = op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM shipments WHERE status = 'FAILED')")
    )
    if failed_shipment_exists:
        raise RuntimeError("Cannot remove FAILED status while failed shipments exist")

    op.drop_constraint("ck_shipments_status", "shipments", type_="check")
    op.create_check_constraint(
        "ck_shipments_status",
        "shipments",
        "status IN "
        "('READY_TO_SHIP', 'READY_FOR_PICKUP', 'SHIPPED', 'DELIVERED', "
        "'PICKED_UP', 'CANCELLED')",
    )
