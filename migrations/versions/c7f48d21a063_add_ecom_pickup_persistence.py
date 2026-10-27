"""Add ECOM pickup persistence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7f48d21a063"
down_revision: str | Sequence[str] | None = "8d72372141d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("fulfillment_method", sa.String(length=20), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE orders SET fulfillment_method = 'SHIPPING' WHERE channel = 'ECOM'"
        )
    )

    op.drop_constraint("ck_orders_fulfillment_status", "orders", type_="check")
    op.create_check_constraint(
        "ck_orders_fulfillment_status",
        "orders",
        "fulfillment_status IN "
        "('NOT_STARTED', 'READY_TO_SHIP', 'READY_FOR_PICKUP', 'SHIPPED', "
        "'DELIVERED', 'PICKED_UP', 'CANCELLED', 'COMPLETED')",
    )
    op.create_check_constraint(
        "ck_orders_fulfillment_method",
        "orders",
        "(channel = 'ECOM' AND fulfillment_method IS NOT NULL "
        "AND fulfillment_method IN ('SHIPPING', 'STORE_PICKUP')) "
        "OR (channel = 'POS' AND fulfillment_method IS NULL)",
    )

    op.add_column(
        "shipments",
        sa.Column("ready_for_pickup_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "shipments",
        sa.Column("pickup_deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("ck_shipments_status", "shipments", type_="check")
    op.create_check_constraint(
        "ck_shipments_status",
        "shipments",
        "status IN "
        "('READY_TO_SHIP', 'READY_FOR_PICKUP', 'SHIPPED', 'DELIVERED', "
        "'PICKED_UP', 'CANCELLED')",
    )

    op.drop_constraint(
        "ck_inventory_movements_movement_type",
        "inventory_movements",
        type_="check",
    )
    op.create_check_constraint(
        "ck_inventory_movements_movement_type",
        "inventory_movements",
        "movement_type IN ("
        "'RECEIPT', 'TRANSFER', 'RESERVATION', 'RELEASE', 'SALE', 'RETURN', "
        "'PICKUP_EXPIRY', 'REPAIR', 'DAMAGE', 'LOSS')",
    )


def downgrade() -> None:
    connection = op.get_bind()

    has_pickup_orders = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM orders "
            "WHERE fulfillment_method = 'STORE_PICKUP')"
        )
    )
    has_pickup_order_status = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM orders "
            "WHERE fulfillment_status = 'READY_FOR_PICKUP')"
        )
    )
    has_pickup_shipments = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM shipments "
            "WHERE fulfillment_method = 'STORE_PICKUP' "
            "OR status = 'READY_FOR_PICKUP' "
            "OR ready_for_pickup_at IS NOT NULL "
            "OR pickup_deadline_at IS NOT NULL)"
        )
    )
    has_pickup_expiry_movements = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM inventory_movements "
            "WHERE movement_type = 'PICKUP_EXPIRY')"
        )
    )

    if (
        has_pickup_orders
        or has_pickup_order_status
        or has_pickup_shipments
        or has_pickup_expiry_movements
    ):
        raise RuntimeError(
            "Cannot downgrade ECOM pickup support while pickup orders, "
            "pickup shipment data, or PICKUP_EXPIRY movements exist"
        )

    op.drop_constraint(
        "ck_inventory_movements_movement_type",
        "inventory_movements",
        type_="check",
    )
    op.create_check_constraint(
        "ck_inventory_movements_movement_type",
        "inventory_movements",
        "movement_type IN ("
        "'RECEIPT', 'TRANSFER', 'RESERVATION', 'RELEASE', 'SALE', 'RETURN', "
        "'REPAIR', 'DAMAGE', 'LOSS')",
    )

    op.drop_constraint("ck_shipments_status", "shipments", type_="check")
    op.create_check_constraint(
        "ck_shipments_status",
        "shipments",
        "status IN ('READY_TO_SHIP', 'SHIPPED', 'DELIVERED', 'PICKED_UP', 'CANCELLED')",
    )
    op.drop_column("shipments", "pickup_deadline_at")
    op.drop_column("shipments", "ready_for_pickup_at")

    op.drop_constraint(
        "ck_orders_fulfillment_method",
        "orders",
        type_="check",
    )
    op.drop_constraint("ck_orders_fulfillment_status", "orders", type_="check")
    op.create_check_constraint(
        "ck_orders_fulfillment_status",
        "orders",
        "fulfillment_status IN "
        "('NOT_STARTED', 'READY_TO_SHIP', 'SHIPPED', 'DELIVERED', "
        "'PICKED_UP', 'CANCELLED', 'COMPLETED')",
    )
    op.drop_column("orders", "fulfillment_method")
