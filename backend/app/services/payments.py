from datetime import datetime, timedelta, timezone
from uuid import UUID

import stripe
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from stripe import SignatureVerificationError, Webhook

from backend.app.core.config import settings
from backend.app.core.data_protection import decrypt_pii
from backend.app.db.database import commit_or_rollback, flush_or_rollback
from backend.app.integrations.stripe.client import get_stripe_client
from backend.app.models.customer import Customer
from backend.app.models.inventory_movement import InventoryMovement
from backend.app.models.inventory_unit import InventoryUnit
from backend.app.models.location import Location
from backend.app.models.order import Order
from backend.app.models.order_address import OrderAddress
from backend.app.models.order_line import OrderLine
from backend.app.models.order_line_unit import OrderLineUnit
from backend.app.models.payment import Payment
from backend.app.models.refund import Refund
from backend.app.models.reservation import Reservation
from backend.app.models.shipment import Shipment
from backend.app.models.stripe_webhook_event import StripeWebhookEvent
from backend.app.schemas.orders import PaymentResponse
from backend.app.services.audit import record_audit_event
from backend.app.services.pos import (
    complete_pos_card_sale,
    get_safe_pos_reservations,
)

PAID_ORDER_STATUSES = {
    "PAID",
    "READY_TO_SHIP",
    "READY_FOR_PICKUP",
    "SHIPPED",
    "DELIVERED",
    "PICKED_UP",
    "COMPLETED",
}
SETTLED_PAYMENT_STATUSES = {
    "PAID",
    "PARTIALLY_REFUNDED",
    "REFUNDED",
}


def verify_stripe_webhook_event(
    payload: bytes,
    signature: str,
) -> stripe.Event:
    webhook_secret = settings.stripe_webhook_secret

    if not webhook_secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not configured")

    try:
        return Webhook.construct_event(
            payload=payload,
            sig_header=signature,
            secret=webhook_secret,
        )
    except (ValueError, SignatureVerificationError) as error:
        raise ValueError("Invalid Stripe webhook payload or signature") from error


def record_stripe_webhook_event(
    db: Session,
    stripe_event_id: str,
    event_type: str,
) -> bool:
    statement = (
        pg_insert(StripeWebhookEvent)
        .values(
            stripe_event_id=stripe_event_id,
            event_type=event_type,
        )
        .on_conflict_do_nothing(constraint="uq_stripe_webhook_events_stripe_event_id")
        .returning(StripeWebhookEvent.id)
    )

    inserted_id = db.execute(statement).scalar_one_or_none()
    return inserted_id is None


def validate_stripe_checkout_event(
    db: Session,
    event: stripe.Event,
) -> tuple[Payment, Order]:
    if event.type not in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    }:
        raise ValueError("Unsupported Stripe Checkout event")

    checkout_session = event.data.object

    if checkout_session.object != "checkout.session":
        raise ValueError("Stripe event is not a Checkout Session")

    if checkout_session.payment_status != "paid":
        raise ValueError("Checkout Session is not paid")

    payment_reference = (
        db.query(Payment.id, Payment.order_id)
        .filter(
            Payment.provider == "STRIPE",
            Payment.provider_checkout_session_id == checkout_session.id,
        )
        .one_or_none()
    )

    if payment_reference is None:
        raise ValueError("Checkout Session has no matching Stripe payment")

    payment_id, order_id = payment_reference
    order = db.query(Order).filter(Order.id == order_id).with_for_update().one_or_none()

    if order is None:
        raise ValueError("Stripe payment has no matching order")

    payment = (
        db.query(Payment)
        .filter(
            Payment.id == payment_id,
            Payment.order_id == order.id,
            Payment.provider == "STRIPE",
            Payment.provider_checkout_session_id == checkout_session.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if payment is None:
        raise ValueError("Checkout Session has no matching Stripe payment")

    session_metadata = (
        checkout_session.metadata.to_dict()
        if checkout_session.metadata is not None
        else {}
    )
    session_order_id = session_metadata.get("order_id")

    if session_order_id != str(order.id) or checkout_session.client_reference_id != str(
        order.id
    ):
        raise ValueError("Checkout Session order reference does not match")

    if (
        payment.amount != order.total_gross
        or payment.currency != order.currency
        or checkout_session.amount_total != int(payment.amount * 100)
        or checkout_session.currency != payment.currency.lower()
    ):
        raise ValueError("Checkout Session amount or currency does not match")

    return payment, order


def validate_pos_payment_intent_event(
    db: Session,
    event: stripe.Event,
) -> tuple[Payment, Order]:
    expected_status_by_event = {
        "payment_intent.succeeded": "succeeded",
        "payment_intent.payment_failed": "requires_payment_method",
        "payment_intent.canceled": "canceled",
    }
    expected_status = expected_status_by_event.get(event.type)
    if expected_status is None:
        raise ValueError("Unsupported Stripe PaymentIntent event")

    payment_intent = event.data.object
    if payment_intent.object != "payment_intent":
        raise ValueError("Stripe event is not a PaymentIntent")

    if payment_intent.status != expected_status:
        raise ValueError("Stripe PaymentIntent status does not match the event")

    payment_order_id = (
        db.query(Payment.order_id)
        .filter(
            Payment.provider == "STRIPE",
            Payment.provider_payment_id == payment_intent.id,
        )
        .scalar()
    )
    if payment_order_id is None:
        raise ValueError("PaymentIntent has no matching POS payment")

    order = (
        db.query(Order)
        .filter(Order.id == payment_order_id)
        .with_for_update()
        .one_or_none()
    )
    if order is None:
        raise ValueError("POS payment has no matching order")

    payment = (
        db.query(Payment)
        .filter(
            Payment.provider == "STRIPE",
            Payment.provider_payment_id == payment_intent.id,
            Payment.order_id == order.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if payment is None:
        raise ValueError("PaymentIntent has no matching POS payment")

    metadata = (
        payment_intent.metadata.to_dict() if payment_intent.metadata is not None else {}
    )
    if (
        order.channel != "POS"
        or payment.payment_method != "CARD"
        or payment.provider != "STRIPE"
        or payment.provider_checkout_session_id is not None
        or metadata.get("channel") != "POS"
        or metadata.get("order_id") != str(order.id)
        or metadata.get("order_number") != order.order_number
    ):
        raise ValueError("PaymentIntent does not match the POS order")

    if (
        payment.amount != order.total_gross
        or payment.currency != order.currency
        or payment_intent.amount != int(payment.amount * 100)
        or payment_intent.currency != payment.currency.lower()
    ):
        raise ValueError("PaymentIntent amount or currency does not match")

    return payment, order


def process_pos_payment_intent_event(
    db: Session,
    event: stripe.Event,
) -> None:
    if event.type not in {
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "payment_intent.canceled",
    }:
        raise ValueError("Unsupported Stripe PaymentIntent event")

    if record_stripe_webhook_event(db, event.id, event.type):
        db.rollback()
        return

    payment, order = validate_pos_payment_intent_event(db, event)

    if event.type == "payment_intent.payment_failed":
        commit_or_rollback(db)
        return

    if event.type == "payment_intent.canceled":
        if payment.status == "FAILED" and order.order_status == "CANCELLED":
            commit_or_rollback(db)
            return

        if (
            payment.status != "PENDING"
            or order.payment_status != "UNPAID"
            or order.order_status not in {"PENDING_PAYMENT", "EXPIRED"}
        ):
            raise ValueError("POS order cannot be canceled in its current state")

        reservations = (
            db.query(Reservation)
            .filter(
                Reservation.order_id == order.id,
                Reservation.status == "ACTIVE",
            )
            .order_by(Reservation.inventory_unit_id)
            .with_for_update()
            .all()
        )
        unit_ids = [reservation.inventory_unit_id for reservation in reservations]
        inventory_units = (
            db.query(InventoryUnit)
            .filter(InventoryUnit.id.in_(unit_ids))
            .order_by(InventoryUnit.id)
            .with_for_update()
            .all()
            if unit_ids
            else []
        )
        units_by_id = {unit.id: unit for unit in inventory_units}

        if len(units_by_id) != len(reservations):
            raise ValueError("POS cancellation has a missing inventory unit")

        if unit_ids and (
            db.query(Reservation.id)
            .filter(
                Reservation.inventory_unit_id.in_(unit_ids),
                Reservation.order_id != order.id,
                Reservation.status == "ACTIVE",
            )
            .first()
            is not None
        ):
            raise ValueError("POS cancellation conflicts with another reservation")

        if any(
            units_by_id[reservation.inventory_unit_id].status != "RESERVED"
            for reservation in reservations
        ):
            raise ValueError("POS cancellation found an invalid reservation")

        now = datetime.now(timezone.utc)
        for reservation in reservations:
            unit = units_by_id[reservation.inventory_unit_id]
            previous_status = unit.status
            unit.status = "IN_STOCK"
            reservation.status = "RELEASED"
            reservation.released_at = now

            db.add(
                InventoryMovement(
                    inventory_unit_id=unit.id,
                    movement_type="RELEASE",
                    from_location_id=unit.location_id,
                    to_location_id=unit.location_id,
                    from_status=previous_status,
                    to_status="IN_STOCK",
                    order_id=order.id,
                    actor_type="SYSTEM",
                    note="POS card payment canceled",
                )
            )

        payment.status = "FAILED"
        order.order_status = "CANCELLED"
        order.payment_status = "FAILED"
        commit_or_rollback(db)
        return

    if (
        payment.status in SETTLED_PAYMENT_STATUSES
        and order.order_status == "COMPLETED"
        and order.payment_status in SETTLED_PAYMENT_STATUSES
    ):
        commit_or_rollback(db)
        return

    if payment.status == "REFUNDED":
        commit_or_rollback(db)
        return

    if payment.status != "PENDING":
        raise ValueError("POS payment cannot be completed in its current state")

    payment_intent = event.data.object
    reservations = get_safe_pos_reservations(db, order.id)

    if reservations is not None:
        complete_pos_card_sale(
            db,
            order.id,
            payment_intent.id,
        )
        return

    stripe_refund = create_late_stripe_payment_refund(
        payment,
        payment_intent.id,
    )
    save_late_stripe_refund_result(
        db,
        payment,
        order,
        payment_intent.id,
        stripe_refund,
    )
    commit_or_rollback(db)


def get_safe_stripe_reservations(
    db: Session,
    order: Order,
) -> list[Reservation] | None:
    if order.order_status not in {"PENDING_PAYMENT", "EXPIRED"}:
        return None

    order_lines = db.query(OrderLine).filter(OrderLine.order_id == order.id).all()
    expected_unit_count = sum(line.quantity for line in order_lines)

    reservations = (
        db.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .order_by(Reservation.inventory_unit_id)
        .with_for_update()
        .all()
    )

    if not expected_unit_count or len(reservations) != expected_unit_count:
        return None

    inventory_units = (
        db.query(InventoryUnit)
        .filter(
            InventoryUnit.id.in_(
                [reservation.inventory_unit_id for reservation in reservations]
            )
        )
        .order_by(InventoryUnit.id)
        .with_for_update()
        .all()
    )
    units_by_id = {unit.id: unit for unit in inventory_units}

    if len(units_by_id) != len(reservations):
        return None

    for reservation in reservations:
        unit = units_by_id[reservation.inventory_unit_id]
        if not (reservation.status == "ACTIVE" and unit.status == "RESERVED") and not (
            reservation.status == "EXPIRED" and unit.status == "IN_STOCK"
        ):
            return None

    return reservations


class StripeLatePaymentRefundRequired(Exception):
    pass


class StripeRefundIntegrityError(ValueError):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


def process_paid_stripe_checkout_event(
    db: Session,
    event: stripe.Event,
) -> None:
    if record_stripe_webhook_event(db, event.id, event.type):
        db.rollback()
        return

    payment, order = validate_stripe_checkout_event(db, event)

    if (
        payment.status in SETTLED_PAYMENT_STATUSES
        and order.order_status in PAID_ORDER_STATUSES
        and order.payment_status in SETTLED_PAYMENT_STATUSES
    ):
        commit_or_rollback(db)
        return
    if (
        payment.status == "REFUNDED"
        and order.order_status == "CANCELLED"
        and order.payment_status == "REFUNDED"
    ):
        commit_or_rollback(db)
        return

    if payment.status != "PENDING":
        raise ValueError("Stripe payment is not pending")

    reservations = get_safe_stripe_reservations(db, order)
    if reservations is None:
        raise StripeLatePaymentRefundRequired

    if order.fulfillment_method == "SHIPPING":
        shipping_address = (
            db.query(OrderAddress)
            .filter(
                OrderAddress.order_id == order.id,
                OrderAddress.address_type == "SHIPPING",
            )
            .one_or_none()
        )
        if shipping_address is None:
            raise ValueError("Order shipping address not found")
    elif order.fulfillment_method == "STORE_PICKUP":
        shipping_address = None
    else:
        raise ValueError("Unsupported ECOM fulfillment method")

    checkout_session = event.data.object
    payment_intent = checkout_session.payment_intent

    if isinstance(payment_intent, str):
        payment_intent_id = payment_intent
    elif payment_intent is not None:
        payment_intent_id = payment_intent.id
    else:
        raise ValueError("Checkout Session has no PaymentIntent")

    payment.provider_payment_id = payment_intent_id

    _complete_paid_ecom_order(
        db,
        order,
        payment,
        reservations,
        shipping_address,
    )


def create_late_stripe_payment_refund(
    payment: Payment,
    payment_intent_id: str,
) -> stripe.Refund:
    return get_stripe_client().v1.refunds.create(
        {
            "payment_intent": payment_intent_id,
            "amount": int(payment.amount * 100),
            "metadata": {
                "half_moon_payment_id": str(payment.id),
            },
        },
        {"idempotency_key": (f"half-moon-late-payment-refund-v1-{payment.id}")},
    )


def save_late_stripe_refund_result(
    db: Session,
    payment: Payment,
    order: Order,
    payment_intent_id: str,
    stripe_refund: stripe.Refund,
) -> None:
    if stripe_refund.object != "refund":
        raise StripeRefundIntegrityError(
            "STRIPE_REFUND_OBJECT_INVALID",
            "Stripe did not return a Refund",
        )

    refund_payment_intent = stripe_refund.payment_intent
    if isinstance(refund_payment_intent, str):
        refund_payment_intent_id = refund_payment_intent
    elif refund_payment_intent is not None:
        refund_payment_intent_id = refund_payment_intent.id
    else:
        raise StripeRefundIntegrityError(
            "STRIPE_REFUND_PAYMENT_INTENT_MISSING",
            "Stripe Refund has no PaymentIntent",
        )

    if (
        refund_payment_intent_id != payment_intent_id
        or stripe_refund.amount != int(payment.amount * 100)
        or stripe_refund.currency != payment.currency.lower()
    ):
        raise StripeRefundIntegrityError(
            "STRIPE_REFUND_PAYMENT_MISMATCH",
            "Stripe Refund does not match the payment",
        )

    status_map = {
        "succeeded": "REFUNDED",
        "pending": "PENDING",
        "requires_action": "PENDING",
        "failed": "FAILED",
        "canceled": "FAILED",
    }
    stripe_refund_status = stripe_refund.status
    if stripe_refund_status is None:
        raise StripeRefundIntegrityError(
            "STRIPE_REFUND_STATUS_MISSING",
            "Stripe Refund has no status",
        )

    refund_status = status_map.get(stripe_refund_status)
    if refund_status is None:
        raise StripeRefundIntegrityError(
            "STRIPE_REFUND_STATUS_UNKNOWN",
            "Stripe returned an unknown Refund status",
        )

    refund_record = (
        db.query(Refund)
        .filter(Refund.provider_ref == stripe_refund.id)
        .with_for_update()
        .one_or_none()
    )

    if refund_record is None:
        refund_record = Refund(
            return_id=None,
            payment_id=payment.id,
            amount=payment.amount,
            method="ORIGINAL_PAYMENT",
            provider_ref=stripe_refund.id,
            status=refund_status,
        )
        db.add(refund_record)
    elif (
        refund_record.payment_id != payment.id or refund_record.amount != payment.amount
    ):
        raise StripeRefundIntegrityError(
            "STRIPE_REFUND_ALREADY_LINKED",
            "Stripe Refund is already linked to a different payment",
        )
    elif refund_record.status not in {"REFUNDED", "FAILED"}:
        refund_record.status = refund_status

    payment.provider_payment_id = payment_intent_id
    order.order_status = "EXPIRED"

    if refund_record.status == "REFUNDED":
        refund_record.completed_at = datetime.now(timezone.utc)
        payment.status = "REFUNDED"
        order.payment_status = "REFUNDED"
    else:
        payment.status = "PAID"
        order.payment_status = "PAID"


def process_late_paid_stripe_checkout_event(
    db: Session,
    event: stripe.Event,
) -> None:
    if record_stripe_webhook_event(db, event.id, event.type):
        db.rollback()
        return

    payment, order = validate_stripe_checkout_event(db, event)

    if (
        payment.status in SETTLED_PAYMENT_STATUSES
        and order.order_status in PAID_ORDER_STATUSES
        and order.payment_status in SETTLED_PAYMENT_STATUSES
    ):
        commit_or_rollback(db)
        return

    if payment.status == "REFUNDED":
        commit_or_rollback(db)
        return

    if payment.status not in {"PENDING", "PAID"}:
        raise ValueError("Stripe payment cannot be refunded in its current state")

    checkout_session = event.data.object
    payment_intent = checkout_session.payment_intent

    if isinstance(payment_intent, str):
        payment_intent_id = payment_intent
    elif payment_intent is not None:
        payment_intent_id = payment_intent.id
    else:
        raise ValueError("Checkout Session has no PaymentIntent")

    stripe_refund = create_late_stripe_payment_refund(
        payment,
        payment_intent_id,
    )
    try:
        save_late_stripe_refund_result(
            db,
            payment,
            order,
            payment_intent_id,
            stripe_refund,
        )
    except StripeRefundIntegrityError as error:
        db.rollback()
        record_audit_event(
            db,
            actor_type="SYSTEM",
            action_code="SYSTEM_LATE_PAYMENT_REFUND",
            outcome="FAILED",
            target_type="ORDER",
            target_id=order.id,
            error_code=error.error_code,
        )
        commit_or_rollback(db)
        raise

    if payment.status == "REFUNDED":
        record_audit_event(
            db,
            actor_type="SYSTEM",
            action_code="SYSTEM_LATE_PAYMENT_REFUND",
            outcome="SUCCEEDED",
            target_type="ORDER",
            target_id=order.id,
            details={
                "amount": str(payment.amount),
                "currency": payment.currency,
            },
        )
    commit_or_rollback(db)


def process_stripe_refund_updated_event(
    db: Session,
    event: stripe.Event,
) -> None:
    if event.type != "refund.updated":
        raise ValueError("Unsupported Stripe refund event")

    if record_stripe_webhook_event(db, event.id, event.type):
        db.rollback()
        return

    stripe_refund = event.data.object
    if stripe_refund.object != "refund":
        raise ValueError("Stripe event is not a Refund")

    status_map = {
        "succeeded": "REFUNDED",
        "pending": "PENDING",
        "requires_action": "PENDING",
        "failed": "FAILED",
        "canceled": "FAILED",
    }
    stripe_status = stripe_refund.status
    if stripe_status is None:
        raise ValueError("Stripe Refund has no status")

    new_status = status_map.get(stripe_status)
    if new_status is None:
        raise ValueError("Stripe returned an unknown Refund status")

    refund_reference = (
        db.query(Refund.id, Refund.payment_id)
        .filter(Refund.provider_ref == stripe_refund.id)
        .one_or_none()
    )

    if refund_reference is None:
        metadata = stripe_refund.metadata or {}
        if metadata.get("half_moon_payment_id"):
            raise ValueError("Stripe Refund record is not committed yet")

        commit_or_rollback(db)
        return

    refund_id, payment_id = refund_reference
    order_id = db.query(Payment.order_id).filter(Payment.id == payment_id).scalar()
    if order_id is None:
        raise ValueError("Stripe Refund has no matching Stripe payment")

    order = db.query(Order).filter(Order.id == order_id).with_for_update().one_or_none()
    if order is None:
        raise ValueError("Stripe payment has no matching order")

    payment = (
        db.query(Payment)
        .filter(Payment.id == payment_id, Payment.order_id == order.id)
        .with_for_update()
        .one_or_none()
    )
    if payment is None or payment.provider != "STRIPE":
        raise ValueError("Stripe Refund has no matching Stripe payment")

    refund_record = (
        db.query(Refund)
        .filter(
            Refund.id == refund_id,
            Refund.provider_ref == stripe_refund.id,
            Refund.payment_id == payment.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if refund_record is None:
        raise ValueError("Stripe Refund has no matching Stripe payment")

    refund_payment_intent = stripe_refund.payment_intent
    if isinstance(refund_payment_intent, str):
        payment_intent_id = refund_payment_intent
    elif refund_payment_intent is not None:
        payment_intent_id = refund_payment_intent.id
    else:
        raise ValueError("Stripe Refund has no PaymentIntent")

    if (
        payment_intent_id != payment.provider_payment_id
        or stripe_refund.amount != int(refund_record.amount * 100)
        or stripe_refund.currency != payment.currency.lower()
    ):
        raise ValueError("Stripe Refund does not match the saved refund")

    if refund_record.status not in {"REFUNDED", "FAILED"}:
        refund_record.status = new_status
        if new_status == "REFUNDED" and refund_record.completed_at is None:
            refund_record.completed_at = datetime.now(timezone.utc)

    flush_or_rollback(db)
    completed_refunds = (
        db.query(Refund)
        .filter(
            Refund.payment_id == payment.id,
            Refund.status == "REFUNDED",
        )
        .all()
    )
    refunded_amount = sum(
        (refund.amount for refund in completed_refunds),
        payment.amount - payment.amount,
    )

    if refunded_amount >= payment.amount:
        payment.status = "REFUNDED"
    elif refunded_amount > 0:
        payment.status = "PARTIALLY_REFUNDED"
    else:
        payment.status = "PAID"

    order.payment_status = payment.status
    commit_or_rollback(db)


def _complete_paid_ecom_order(
    db: Session,
    order: Order,
    payment: Payment,
    reservations: list[Reservation],
    shipping_address: OrderAddress | None,
) -> PaymentResponse:
    inventory_unit_ids = [reservation.inventory_unit_id for reservation in reservations]
    inventory_units = (
        db.query(InventoryUnit)
        .filter(InventoryUnit.id.in_(inventory_unit_ids))
        .order_by(InventoryUnit.id)
        .with_for_update()
        .all()
    )

    if len(inventory_units) != len(reservations):
        raise ValueError("Reserved inventory unit not found")

    units_by_id = {unit.id: unit for unit in inventory_units}
    if len(units_by_id) != len(reservations):
        raise ValueError("Order contains duplicate inventory reservations")

    if any(unit.status != "RESERVED" for unit in inventory_units):
        raise ValueError("Reserved inventory unit is no longer available")

    source_location_id = inventory_units[0].location_id if inventory_units else None
    if source_location_id is None:
        raise ValueError("Shipment source location not found")
    if any(unit.location_id != source_location_id for unit in inventory_units):
        raise ValueError("Order reservations use multiple source locations")

    for reservation in reservations:
        unit = units_by_id[reservation.inventory_unit_id]
        if not (
            reservation.status == "ACTIVE"
            and unit.status == "RESERVED"
            or reservation.status == "EXPIRED"
            and unit.status == "IN_STOCK"
        ):
            raise ValueError("Reserved inventory unit is no longer available")
    fulfillment_method = order.fulfillment_method
    if fulfillment_method == "STORE_PICKUP":
        source_location = (
            db.query(Location).filter(Location.id == source_location_id).one_or_none()
        )
        if source_location is None or source_location.code != "PHYSICAL_STORE":
            raise ValueError("Store pickup inventory is not at the physical store")

        shipment_status = "READY_FOR_PICKUP"
        shipping_address_snapshot = None
    elif fulfillment_method == "SHIPPING":
        if shipping_address is None:
            raise ValueError("Order shipping address not found")
        shipment_status = "READY_TO_SHIP"
        shipment_status = "READY_TO_SHIP"
        shipping_address_snapshot = {
            "recipient_name": shipping_address.recipient_name,
            "address_line_1": shipping_address.address_line_1,
            "address_line_2": shipping_address.address_line_2,
            "city": shipping_address.city,
            "region": shipping_address.region,
            "postal_code": shipping_address.postal_code,
            "country_code": shipping_address.country_code,
            "phone": shipping_address.phone,
        }
    else:
        raise ValueError("Unsupported ECOM fulfillment method")
    if fulfillment_method == "STORE_PICKUP":
        customer_id = order.customer_id
        if customer_id is None:
            raise ValueError("Paid ECOM shipment has no customer")

        customer = db.get(Customer, customer_id)
        if customer is None:
            raise ValueError("Shipment customer not found")

        recipient_email = decrypt_pii(customer.email_ciphertext)
        if not recipient_email.strip():
            raise ValueError("Shipment customer email is missing")
    now = datetime.now(timezone.utc)

    for reservation in reservations:
        if reservation.status == "EXPIRED":
            inventory_unit = units_by_id[reservation.inventory_unit_id]
            previous_status = inventory_unit.status
            inventory_unit.status = "RESERVED"
            reservation.status = "ACTIVE"
            reservation.expires_at = now + timedelta(
                minutes=settings.reservation_duration_minutes
            )

            db.add(
                InventoryMovement(
                    inventory_unit_id=inventory_unit.id,
                    movement_type="RESERVATION",
                    from_location_id=inventory_unit.location_id,
                    to_location_id=inventory_unit.location_id,
                    from_status=previous_status,
                    to_status="RESERVED",
                    order_id=order.id,
                    actor_type="SYSTEM",
                    note="Reservation restored after Stripe payment inventory check",
                )
            )

    payment.status = "PAID"
    db.add(payment)

    for reservation in reservations:
        inventory_unit = units_by_id[reservation.inventory_unit_id]
        previous_status = inventory_unit.status
        previous_location_id = inventory_unit.location_id

        db.add(
            OrderLineUnit(
                order_line_id=reservation.order_line_id,
                inventory_unit_id=inventory_unit.id,
            )
        )

        inventory_unit.location_id = None
        inventory_unit.status = "SOLD"
        reservation.status = "COMMITTED"

        db.add(
            InventoryMovement(
                inventory_unit_id=inventory_unit.id,
                movement_type="SALE",
                from_location_id=previous_location_id,
                to_location_id=None,
                from_status=previous_status,
                to_status="SOLD",
                order_id=order.id,
                actor_type="SYSTEM",
                note="Payment confirmed",
            )
        )

    db.add(
        Shipment(
            order_id=order.id,
            source_location_id=source_location_id,
            fulfillment_method=fulfillment_method,
            status=shipment_status,
            shipping_address_snapshot=shipping_address_snapshot,
        )
    )

    order.order_status = "PAID"
    order.payment_status = "PAID"

    commit_or_rollback(db)
    db.refresh(payment)

    return PaymentResponse(
        payment_id=payment.id,
        order_id=order.id,
        order_number=order.order_number,
        order_status=order.order_status,
        payment_status=payment.status,
        amount=payment.amount,
        currency=payment.currency,
        failure_reason=None,
    )
