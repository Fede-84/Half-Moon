"""Unify order and fulfillment lifecycle states."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2f40e7a91bd"
down_revision: str | Sequence[str] | None = "b9d4e6f13a27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDER_STATUS_CONSTRAINT = (
    "order_status IN "
    "('PENDING_PAYMENT', 'PAID', 'CANCELLED', 'EXPIRED', "
    "'READY_TO_SHIP', 'READY_FOR_PICKUP', 'SHIPPED', 'DELIVERED', "
    "'PICKED_UP', 'COMPLETED')"
)

LEGACY_ORDER_STATUS_CONSTRAINT = (
    "order_status IN ('PENDING_PAYMENT', 'CANCELLED', 'EXPIRED', 'PAID')"
)

LEGACY_FULFILLMENT_STATUS_CONSTRAINT = (
    "fulfillment_status IN "
    "('NOT_STARTED', 'READY_TO_SHIP', 'READY_FOR_PICKUP', 'SHIPPED', "
    "'DELIVERED', 'PICKED_UP', 'CANCELLED', 'COMPLETED')"
)


def upgrade() -> None:
    connection = op.get_bind()
    has_ambiguous_statuses = connection.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM orders "
            "WHERE ("
            "fulfillment_status IN ("
            "'READY_TO_SHIP', 'READY_FOR_PICKUP', 'SHIPPED', "
            "'DELIVERED', 'PICKED_UP', 'COMPLETED'"
            ") AND (order_status <> 'PAID' OR payment_status NOT IN ("
            "'PAID', 'PARTIALLY_REFUNDED', 'REFUNDED'"
            "))"
            ") OR ("
            "fulfillment_status = 'CANCELLED' "
            "AND order_status NOT IN ('CANCELLED', 'PAID')"
            ") OR ("
            "fulfillment_status = 'CANCELLED' "
            "AND order_status = 'PAID' "
            "AND NOT ("
            "channel = 'ECOM' "
            "AND fulfillment_method = 'STORE_PICKUP' "
            "AND payment_status = 'REFUNDED' "
            "AND EXISTS ("
            "SELECT 1 FROM shipments "
            "WHERE shipments.order_id = orders.id "
            "AND shipments.status = 'CANCELLED'"
            ")"
            ")"
            ")"
            ")"
        )
    )
    if has_ambiguous_statuses:
        raise RuntimeError(
            "Cannot unify order statuses while orders contain unsupported "
            "order/payment/fulfillment combinations"
        )

    op.drop_constraint("ck_orders_order_status", "orders", type_="check")
    op.drop_constraint("ck_orders_fulfillment_status", "orders", type_="check")
    op.execute(
        sa.text(
            "UPDATE orders SET order_status = CASE "
            "WHEN fulfillment_status = 'NOT_STARTED' THEN order_status "
            "ELSE fulfillment_status END"
        )
    )
    op.create_check_constraint(
        "ck_orders_order_status",
        "orders",
        ORDER_STATUS_CONSTRAINT,
    )
    op.drop_column("orders", "fulfillment_status")


def downgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "fulfillment_status",
            sa.String(length=20),
            server_default="NOT_STARTED",
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE orders SET fulfillment_status = CASE "
            "WHEN order_status IN ("
            "'READY_TO_SHIP', 'READY_FOR_PICKUP', 'SHIPPED', "
            "'DELIVERED', 'PICKED_UP', 'COMPLETED'"
            ") THEN order_status "
            "WHEN order_status = 'CANCELLED' THEN 'CANCELLED' "
            "WHEN order_status = 'PAID' AND channel = 'ECOM' THEN "
            "COALESCE(("
            "SELECT CASE "
            "WHEN shipments.status = 'FAILED' "
            "AND shipments.fulfillment_method = 'STORE_PICKUP' "
            "THEN 'READY_FOR_PICKUP' "
            "WHEN shipments.status = 'FAILED' THEN 'READY_TO_SHIP' "
            "ELSE shipments.status END "
            "FROM shipments WHERE shipments.order_id = orders.id"
            "), 'NOT_STARTED') "
            "WHEN order_status = 'PAID' AND channel = 'POS' "
            "THEN 'COMPLETED' "
            "ELSE 'NOT_STARTED' END"
        )
    )
    op.execute(
        sa.text(
            "UPDATE orders SET order_status = 'PAID' "
            "WHERE order_status IN ("
            "'READY_TO_SHIP', 'READY_FOR_PICKUP', 'SHIPPED', "
            "'DELIVERED', 'PICKED_UP', 'COMPLETED'"
            ") OR ("
            "order_status = 'CANCELLED' "
            "AND channel = 'ECOM' "
            "AND fulfillment_method = 'STORE_PICKUP' "
            "AND payment_status = 'REFUNDED' "
            "AND EXISTS ("
            "SELECT 1 FROM shipments "
            "WHERE shipments.order_id = orders.id "
            "AND shipments.status = 'CANCELLED'"
            ")"
            ")"
        )
    )
    op.drop_constraint("ck_orders_order_status", "orders", type_="check")
    op.create_check_constraint(
        "ck_orders_order_status",
        "orders",
        LEGACY_ORDER_STATUS_CONSTRAINT,
    )
    op.create_check_constraint(
        "ck_orders_fulfillment_status",
        "orders",
        LEGACY_FULFILLMENT_STATUS_CONSTRAINT,
    )
