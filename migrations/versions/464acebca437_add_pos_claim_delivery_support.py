"""add POS claim and receipt delivery support

Revision ID: 464acebca437
Revises: a6f90c12d435
Create Date: 2026-10-06 16:11:41.895790

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "464acebca437"
down_revision: Union[str, Sequence[str], None] = "a6f90c12d435"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.drop_constraint(
        "ck_orders_fulfillment_status",
        "orders",
        type_="check",
    )
    op.create_check_constraint(
        "ck_orders_fulfillment_status",
        "orders",
        "fulfillment_status IN "
        "('NOT_STARTED', 'READY_TO_SHIP', 'SHIPPED', "
        "'DELIVERED', 'PICKED_UP', 'CANCELLED', 'COMPLETED')",
    )

    op.create_table(
        "pos_order_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("claim_code_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "failed_attempt_count",
            sa.SmallInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "receipt_delivery_acknowledged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("last_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reset_by_staff_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "failed_attempt_count BETWEEN 0 AND 5",
            name="ck_pos_order_claims_failed_attempt_count",
        ),
        sa.CheckConstraint(
            "("
            "last_reset_at IS NULL "
            "AND last_reset_by_staff_id IS NULL"
            ") OR ("
            "last_reset_at IS NOT NULL "
            "AND last_reset_by_staff_id IS NOT NULL"
            ")",
            name="ck_pos_order_claims_reset_metadata",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_pos_order_claims_order_id_orders",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_reset_by_staff_id"],
            ["staff_users.id"],
            name="fk_pos_order_claims_last_reset_by_staff_id_staff_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_id",
            name="uq_pos_order_claims_order_id",
        ),
    )

    op.create_table(
        "pos_receipt_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("encrypted_claim_code", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["pos_order_claims.order_id"],
            name="fk_pos_receipt_deliveries_order_id_pos_order_claims",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_id",
            name="uq_pos_receipt_deliveries_order_id",
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()

    has_claims = connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM pos_order_claims)")
    )
    has_deliveries = connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM pos_receipt_deliveries)")
    )
    has_paid_timestamps = connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM orders WHERE paid_at IS NOT NULL)")
    )
    has_completed_orders = connection.scalar(
        sa.text(
            "SELECT EXISTS "
            "(SELECT 1 FROM orders WHERE fulfillment_status = 'COMPLETED')"
        )
    )

    if has_claims or has_deliveries or has_paid_timestamps or has_completed_orders:
        raise RuntimeError(
            "Cannot downgrade POS claim support while claim/delivery data, "
            "paid_at timestamps, or completed orders exist"
        )

    op.drop_table("pos_receipt_deliveries")
    op.drop_table("pos_order_claims")

    op.drop_constraint(
        "ck_orders_fulfillment_status",
        "orders",
        type_="check",
    )
    op.create_check_constraint(
        "ck_orders_fulfillment_status",
        "orders",
        "fulfillment_status IN "
        "('NOT_STARTED', 'READY_TO_SHIP', 'SHIPPED', "
        "'DELIVERED', 'PICKED_UP', 'CANCELLED')",
    )

    op.drop_column("orders", "paid_at")
