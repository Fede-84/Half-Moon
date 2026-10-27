import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.data_protection import decrypt_pii
from backend.app.db.database import commit_or_rollback
from backend.app.models.customer import Customer
from backend.app.models.inventory_movement import InventoryMovement
from backend.app.models.inventory_unit import InventoryUnit
from backend.app.models.order import Order
from backend.app.models.order_line import OrderLine
from backend.app.models.order_line_unit import OrderLineUnit
from backend.app.models.payment import Payment
from backend.app.models.refund import Refund
from backend.app.models.shipment import Shipment
from backend.app.services.audit import record_audit_event
from backend.app.services.notifications import enqueue_notification
from backend.app.schemas.orders import CustomerOrderShipmentStatusResponse

logger = logging.getLogger(__name__)


class PickupExpiryIntegrityError(ValueError):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


def _pickup_deadline_at(ready_at: datetime) -> datetime:
    ready_at_rome = ready_at.astimezone(ZoneInfo("Europe/Rome"))
    return (ready_at_rome + timedelta(days=7)).astimezone(timezone.utc)


def get_order_fulfillment_status(
    db: Session,
    order_id: UUID,
    customer_id: UUID,
) -> CustomerOrderShipmentStatusResponse:
    order = (
        db.query(Order)
        .filter(
            Order.id == order_id,
            Order.customer_id == customer_id,
        )
        .first()
    )

    if order is None:
        raise ValueError("Order not found")

    shipment = db.query(Shipment).filter(Shipment.order_id == order.id).first()

    if shipment is None:
        raise ValueError("Shipment not found")

    return CustomerOrderShipmentStatusResponse(
        shipment_id=shipment.id,
        order_id=order.id,
        order_number=order.order_number,
        status=shipment.status,
        order_status=order.order_status,
        mock_tracking_number=shipment.mock_tracking_number,
        shipped_at=shipment.shipped_at,
        delivered_at=shipment.delivered_at,
        ready_for_pickup_at=shipment.ready_for_pickup_at,
        pickup_deadline_at=shipment.pickup_deadline_at,
        picked_up_at=shipment.picked_up_at,
    )


def _expire_store_pickup_order(
    db: Session,
    order: Order,
    shipment: Shipment,
    now: datetime,
) -> tuple[Decimal, int]:
    paid_payments = (
        db.query(Payment)
        .filter(
            Payment.order_id == order.id,
            Payment.status == "PAID",
        )
        .order_by(Payment.created_at.desc(), Payment.id)
        .with_for_update()
        .all()
    )

    if len(paid_payments) != 1:
        raise PickupExpiryIntegrityError(
            "PICKUP_EXPIRY_PAYMENT_COUNT",
            "Expired pickup order must have exactly one paid payment",
        )

    payment = paid_payments[0]
    if payment.amount != order.total_gross:
        raise PickupExpiryIntegrityError(
            "PICKUP_EXPIRY_PAYMENT_TOTAL_MISMATCH",
            "Paid payment amount does not match the order total",
        )

    expected_unit_count = (
        db.query(func.sum(OrderLine.quantity))
        .filter(OrderLine.order_id == order.id)
        .scalar()
    )

    inventory_units = (
        db.query(InventoryUnit)
        .join(
            OrderLineUnit,
            OrderLineUnit.inventory_unit_id == InventoryUnit.id,
        )
        .join(
            OrderLine,
            OrderLine.id == OrderLineUnit.order_line_id,
        )
        .filter(OrderLine.order_id == order.id)
        .order_by(InventoryUnit.id)
        .with_for_update(of=InventoryUnit)
        .all()
    )

    if (
        expected_unit_count is None
        or expected_unit_count < 1
        or len(inventory_units) != expected_unit_count
        or len({unit.id for unit in inventory_units}) != len(inventory_units)
    ):
        raise PickupExpiryIntegrityError(
            "PICKUP_EXPIRY_INVENTORY_ALLOCATION",
            "Expired pickup order has an incomplete inventory allocation",
        )

    if any(
        unit.status != "SOLD" or unit.location_id is not None
        for unit in inventory_units
    ):
        raise PickupExpiryIntegrityError(
            "PICKUP_EXPIRY_INVENTORY_STATE",
            "Expired pickup inventory is not in the expected sold state",
        )

    for unit in inventory_units:
        unit.location_id = shipment.source_location_id
        unit.status = "IN_STOCK"

        db.add(
            InventoryMovement(
                inventory_unit_id=unit.id,
                movement_type="PICKUP_EXPIRY",
                from_location_id=None,
                to_location_id=shipment.source_location_id,
                from_status="SOLD",
                to_status="IN_STOCK",
                order_id=order.id,
                actor_type="SYSTEM",
                note="Pickup window expired; inventory restored",
            )
        )

    db.add(
        Refund(
            return_id=None,
            payment_id=payment.id,
            amount=payment.amount,
            method="ORIGINAL_PAYMENT",
            provider_ref=f"sim_ref_{uuid4().hex}",
            status="REFUNDED",
            completed_at=now,
        )
    )

    payment.status = "REFUNDED"
    order.payment_status = "REFUNDED"
    shipment.status = "CANCELLED"
    order.order_status = "CANCELLED"
    return payment.amount, len(inventory_units)


def advance_one_due_mock_shipment(db: Session) -> bool:
    now = datetime.now(timezone.utc)

    due_shipment = (
        db.query(Shipment, Order)
        .join(Order, Shipment.order_id == Order.id)
        .filter(
            Order.channel == "ECOM",
            Order.payment_status == "PAID",
            or_(
                and_(
                    Order.order_status == "PAID",
                    Shipment.status.in_(("READY_TO_SHIP", "READY_FOR_PICKUP")),
                ),
                and_(
                    Order.order_status == "READY_TO_SHIP",
                    Shipment.fulfillment_method == "SHIPPING",
                    Shipment.status == "READY_TO_SHIP",
                    Shipment.created_at
                    <= now - timedelta(seconds=settings.mock_ship_after_seconds),
                ),
                and_(
                    Order.order_status == "SHIPPED",
                    Shipment.fulfillment_method == "SHIPPING",
                    Shipment.status == "SHIPPED",
                    Shipment.shipped_at.is_not(None),
                    Shipment.shipped_at
                    <= now - timedelta(seconds=settings.mock_delivery_after_seconds),
                ),
                and_(
                    Order.order_status == "READY_FOR_PICKUP",
                    Shipment.fulfillment_method == "STORE_PICKUP",
                    Shipment.status == "READY_FOR_PICKUP",
                    Shipment.ready_for_pickup_at.is_not(None),
                    Shipment.ready_for_pickup_at
                    <= now - timedelta(seconds=settings.mock_ship_after_seconds),
                    Shipment.pickup_deadline_at.is_not(None),
                    Shipment.pickup_deadline_at >= now,
                    Shipment.picked_up_at.is_(None),
                ),
                and_(
                    Order.order_status == "READY_FOR_PICKUP",
                    Shipment.fulfillment_method == "STORE_PICKUP",
                    Shipment.status == "READY_FOR_PICKUP",
                    Shipment.ready_for_pickup_at.is_not(None),
                    Shipment.pickup_deadline_at.is_not(None),
                    Shipment.pickup_deadline_at < now,
                    Shipment.picked_up_at.is_(None),
                ),
            ),
        )
        .order_by(Shipment.created_at)
        .with_for_update(skip_locked=True)
        .first()
    )

    if due_shipment is None:
        return False

    shipment, order = due_shipment
    selected_shipment_id = shipment.id
    selected_status = shipment.status
    try:
        return _progress_due_shipment(db, shipment, order, now)
    except SQLAlchemyError:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        failed_shipment = (
            db.query(Shipment)
            .filter(
                Shipment.id == selected_shipment_id,
                Shipment.status == selected_status,
            )
            .with_for_update(skip_locked=True)
            .one_or_none()
        )
        if failed_shipment is None:
            logger.warning(
                "Shipment %s changed state before failure quarantine (%s)",
                selected_shipment_id,
                type(error).__name__,
            )
            return False

        failed_shipment.status = "FAILED"
        commit_or_rollback(db)
        logger.error(
            "Shipment %s was marked FAILED after unexpected "
            "fulfillment processing error (%s)",
            selected_shipment_id,
            type(error).__name__,
        )
        return True


def _progress_due_shipment(
    db: Session,
    shipment: Shipment,
    order: Order,
    now: datetime,
) -> bool:

    if order.customer_id is None:
        raise ValueError("Paid ECOM shipment has no customer")

    customer = db.get(Customer, order.customer_id)
    if customer is None:
        raise ValueError("Shipment customer not found")

    recipient_email = decrypt_pii(customer.email_ciphertext)
    if not recipient_email.strip():
        raise ValueError("Shipment customer email is missing")

    if order.order_status == "PAID" and shipment.status == "READY_TO_SHIP":
        order.order_status = "READY_TO_SHIP"
        commit_or_rollback(db)
        return True
    if order.order_status == "PAID" and shipment.status == "READY_FOR_PICKUP":
        shipment.ready_for_pickup_at = now
        shipment.pickup_deadline_at = _pickup_deadline_at(now)
        order.order_status = "READY_FOR_PICKUP"
        notification_type = "ORDER_READY_FOR_PICKUP"
        payload = {
            "order_number": order.order_number,
        }
    elif order.order_status == "READY_TO_SHIP" and shipment.status == "READY_TO_SHIP":
        shipment.status = "SHIPPED"
        shipment.shipped_at = now
        shipment.mock_tracking_number = (
            f"HM-SHP-{order.order_number.removeprefix('HM-')}"
        )
        order.order_status = "SHIPPED"
        notification_type = "ORDER_SHIPPED"
        payload = {
            "order_number": order.order_number,
            "shipment_reference": shipment.mock_tracking_number,
        }
    elif order.order_status == "SHIPPED" and shipment.status == "SHIPPED":
        item_count = (
            db.query(func.sum(OrderLine.quantity))
            .filter(OrderLine.order_id == order.id)
            .scalar()
        )
        if item_count is None or item_count < 1:
            raise ValueError("Delivered order has no items")

        shipment.status = "DELIVERED"
        shipment.delivered_at = now
        order.order_status = "DELIVERED"
        notification_type = "ORDER_DELIVERED"
        payload = {
            "order_number": order.order_number,
            "item_count": str(item_count),
        }
    elif (
        order.order_status == "READY_FOR_PICKUP"
        and shipment.status == "READY_FOR_PICKUP"
        and shipment.pickup_deadline_at is not None
        and shipment.pickup_deadline_at < now
    ):
        try:
            refund_amount, item_count = _expire_store_pickup_order(
                db,
                order,
                shipment,
                now,
            )
        except PickupExpiryIntegrityError as error:
            failed_shipment_id = shipment.id
            db.rollback()
            failed_shipment = (
                db.query(Shipment)
                .filter(
                    Shipment.id == failed_shipment_id,
                    Shipment.status == "READY_FOR_PICKUP",
                    Shipment.pickup_deadline_at < now,
                )
                .with_for_update(skip_locked=True)
                .one_or_none()
            )
            if failed_shipment is None:
                return False

            failed_shipment.status = "FAILED"
            record_audit_event(
                db,
                actor_type="SYSTEM",
                action_code="SYSTEM_PICKUP_EXPIRY",
                outcome="FAILED",
                target_type="SHIPMENT",
                target_id=failed_shipment.id,
                error_code=error.error_code,
            )
            commit_or_rollback(db)
            return True

        record_audit_event(
            db,
            actor_type="SYSTEM",
            action_code="SYSTEM_PICKUP_EXPIRY",
            outcome="SUCCEEDED",
            target_type="SHIPMENT",
            target_id=shipment.id,
            details={
                "amount": str(refund_amount),
                "item_count": item_count,
            },
        )
        notification_type = "ORDER_PICKUP_EXPIRED"
        payload = {
            "order_number": order.order_number,
        }
    elif (
        order.order_status == "READY_FOR_PICKUP"
        and shipment.status == "READY_FOR_PICKUP"
    ):
        shipment.status = "PICKED_UP"
        shipment.picked_up_at = now
        order.order_status = "PICKED_UP"
        notification_type = "ORDER_PICKED_UP"
        payload = {
            "order_number": order.order_number,
        }
    else:
        raise ValueError("Shipment is not eligible for progression")

    enqueue_notification(
        db,
        customer_id=order.customer_id,
        notification_type=notification_type,
        recipient_email=recipient_email,
        payload=payload,
        order_id=order.id,
    )
    commit_or_rollback(db)
    return True
