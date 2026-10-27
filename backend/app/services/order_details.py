from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.models.order import Order
from backend.app.models.order_line import OrderLine
from backend.app.models.order_line_unit import OrderLineUnit
from backend.app.models.shipment import Shipment
from backend.app.models.product import Product
from backend.app.models.product_variant import ProductVariant
from backend.app.models.return_item import ReturnItem
from backend.app.schemas.orders import (
    CustomerOrderSummaryResponse,
    OrderDetailsResponse,
    OrderLineDetailsResponse,
    OrderLineUnitDetailsResponse,
)


class OrderDetailsIntegrityError(RuntimeError):
    pass


class POSOrderNotClaimedError(ValueError):
    pass


def list_customer_ecom_orders(
    db: Session,
    customer_id: UUID,
) -> list[CustomerOrderSummaryResponse]:
    orders = (
        db.query(Order)
        .filter(
            Order.customer_id == customer_id,
            Order.channel == "ECOM",
        )
        .order_by(Order.created_at.desc(), Order.order_number.desc())
        .all()
    )

    return [
        CustomerOrderSummaryResponse(
            order_id=order.id,
            order_number=order.order_number,
            order_status=order.order_status,
            payment_status=order.payment_status,
            currency=order.currency,
            total_gross=str(order.total_gross),
            created_at=order.created_at,
        )
        for order in orders
    ]


def list_customer_pos_orders(
    db: Session,
    customer_id: UUID,
) -> list[CustomerOrderSummaryResponse]:
    orders = (
        db.query(Order)
        .filter(
            Order.customer_id == customer_id,
            Order.channel == "POS",
        )
        .order_by(Order.created_at.desc(), Order.order_number.desc())
        .all()
    )

    return [
        CustomerOrderSummaryResponse(
            order_id=order.id,
            order_number=order.order_number,
            order_status=order.order_status,
            payment_status=order.payment_status,
            currency=order.currency,
            total_gross=str(order.total_gross),
            created_at=order.created_at,
        )
        for order in orders
    ]


def _serialize_order_details(
    db: Session,
    order: Order,
) -> OrderDetailsResponse:
    order_line_rows = (
        db.query(
            OrderLine,
            ProductVariant.image_url,
            Product.image_url,
            Product.category,
        )
        .join(ProductVariant, ProductVariant.id == OrderLine.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .filter(OrderLine.order_id == order.id)
        .order_by(OrderLine.id)
        .all()
    )
    allocation_rows = (
        db.query(
            OrderLineUnit.order_line_id,
            OrderLineUnit.id,
            ReturnItem.id,
        )
        .join(
            OrderLine,
            OrderLine.id == OrderLineUnit.order_line_id,
        )
        .outerjoin(
            ReturnItem,
            ReturnItem.order_line_unit_id == OrderLineUnit.id,
        )
        .filter(OrderLine.order_id == order.id)
        .order_by(OrderLineUnit.order_line_id, OrderLineUnit.id)
        .all()
    )

    units_by_line: dict[UUID, list[OrderLineUnitDetailsResponse]] = {}
    for order_line_id, allocation_id, return_item_id in allocation_rows:
        units_by_line.setdefault(order_line_id, []).append(
            OrderLineUnitDetailsResponse(
                order_line_unit_id=allocation_id,
                already_returned=return_item_id is not None,
            )
        )

    allocated_quantity = len(allocation_rows)
    expected_quantity = sum(
        order_line.quantity
        for order_line, _, _, _ in order_line_rows
    )
    sale_statuses = {
        "PAID",
        "READY_TO_SHIP",
        "READY_FOR_PICKUP",
        "SHIPPED",
        "DELIVERED",
        "PICKED_UP",
        "COMPLETED",
    }
    pickup_expired_after_payment = (
        order.order_status == "CANCELLED"
        and order.channel == "ECOM"
        and order.fulfillment_method == "STORE_PICKUP"
        and order.payment_status == "REFUNDED"
        and db.query(Shipment.id)
        .filter(
            Shipment.order_id == order.id,
            Shipment.status == "CANCELLED",
        )
        .first()
        is not None
    )
    sale_was_committed = (
        order.order_status in sale_statuses or pickup_expired_after_payment
    )
    if sale_was_committed and expected_quantity != allocated_quantity:
        raise OrderDetailsIntegrityError(
            "Order line quantities do not match serialized allocations"
        )
    if not sale_was_committed and allocated_quantity:
        raise OrderDetailsIntegrityError(
            "Unpaid order unexpectedly has serialized sale allocations"
        )

    lines: list[OrderLineDetailsResponse] = []
    for (
        order_line,
        variant_image_url,
        product_image_url,
        product_category,
    ) in order_line_rows:
        line_units = units_by_line.get(order_line.id, [])
        if sale_was_committed and len(line_units) != order_line.quantity:
            raise OrderDetailsIntegrityError(
                "Order line has an incomplete serialized allocation"
            )

        lines.append(
            OrderLineDetailsResponse(
                order_line_id=order_line.id,
                product_name=order_line.product_name_snapshot,
                variant={
                    **order_line.variant_snapshot,
                    "category": product_category,
                },
                image_url=variant_image_url or product_image_url,
                sku=order_line.sku_snapshot,
                quantity=order_line.quantity,
                unit_price_gross=str(order_line.retail_price_gross),
                total_gross=str(order_line.total_gross),
                units=line_units,
            )
        )

    return OrderDetailsResponse(
        order_id=order.id,
        order_number=order.order_number,
        channel=order.channel,
        order_status=order.order_status,
        payment_status=order.payment_status,
        currency=order.currency,
        total_gross=str(order.total_gross),
        created_at=order.created_at,
        paid_at=order.paid_at,
        lines=lines,
    )


def get_customer_order_details(
    db: Session,
    order_id: UUID,
    customer_id: UUID,
) -> OrderDetailsResponse:
    order = (
        db.query(Order)
        .filter(
            Order.id == order_id,
            Order.customer_id == customer_id,
        )
        .one_or_none()
    )
    if order is None:
        raise ValueError("Order not found")

    return _serialize_order_details(db, order)


def get_staff_pos_order_details(
    db: Session,
    order_number: str,
) -> OrderDetailsResponse:
    order = (
        db.query(Order)
        .filter(
            Order.order_number == order_number.strip(),
            Order.channel == "POS",
        )
        .one_or_none()
    )
    if order is None:
        raise ValueError("POS order not found")
    if order.customer_id is None:
        raise POSOrderNotClaimedError(
            "This POS order must be claimed by a customer before staff can "
            "view its details"
        )

    return _serialize_order_details(db, order)
