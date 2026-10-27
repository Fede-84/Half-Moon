import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, TypedDict
from uuid import UUID

from cryptography.fernet import InvalidToken
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from stripe import StripeError

from backend.app.core.config import settings
from backend.app.core.data_protection import (
    create_pos_claim_code_digest,
    decrypt_pii,
    encrypt_pii,
)
from backend.app.db.database import commit_or_rollback, flush_or_rollback
from backend.app.integrations.stripe.client import get_stripe_client
from backend.app.models.customer import Customer
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.inventory_movement import InventoryMovement
from backend.app.models.inventory_unit import InventoryUnit
from backend.app.models.location import Location
from backend.app.models.order import Order
from backend.app.models.order_line import OrderLine
from backend.app.models.order_line_unit import OrderLineUnit
from backend.app.models.payment import Payment
from backend.app.models.pos_order_claim import PosOrderClaim
from backend.app.models.pos_receipt_delivery import PosReceiptDelivery
from backend.app.models.product import Product
from backend.app.models.product_variant import ProductVariant
from backend.app.models.reservation import Reservation
from backend.app.services.audit import record_audit_event
from backend.app.services.deadlines import utc_deadline_after_14_days
from backend.app.schemas.pos import (
    CreatePOSCheckoutRequest,
    OrderStatus,
    PaymentStatus,
    POSCardCheckoutResponse,
    POSClaimResetResponse,
    POSOrderClaimRequest,
    POSOrderClaimResponse,
    POSOrderLineRequest,
    POSOrderStatusResponse,
    POSReceiptDeliveryAcknowledgementResponse,
    POSReceiptDeliveryResponse,
)
from backend.app.services.reservations import release_expired_reservations

MONEY_QUANTUM = Decimal("0.01")


class POSLineCalculation(TypedDict):
    request: POSOrderLineRequest
    variant: ProductVariant
    product: Product
    total_gross: Decimal
    net_amount: Decimal
    vat_amount: Decimal


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _create_pos_key_hash(idempotency_key: str) -> str:
    namespaced_key = f"POS:{idempotency_key}"
    return hashlib.sha256(namespaced_key.encode("utf-8")).hexdigest()


def _create_pos_request_fingerprint(
    request: CreatePOSCheckoutRequest,
    staff_user_id: UUID | None,
) -> str:
    canonical_request = json.dumps(
        {
            "identity_scope": str(staff_user_id),
            "request": request.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()


def _lock_pos_idempotency_key(db: Session, key_hash: str) -> None:
    lock_id = int.from_bytes(
        hashlib.sha256(key_hash.encode("utf-8")).digest()[:8],
        byteorder="big",
        signed=True,
    )
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": lock_id},
    )


def _get_existing_pos_order(
    db: Session,
    key_hash: str,
    request_fingerprint: str,
) -> Order | None:
    order = (
        db.query(Order)
        .filter(Order.idempotency_key_hash == key_hash)
        .with_for_update()
        .one_or_none()
    )

    if order is None:
        return None

    if order.channel != "POS" or order.request_fingerprint != request_fingerprint:
        raise ValueError("Idempotency-Key was already used with a different request")

    return order


def _get_next_order_number(db: Session, now: datetime) -> str:
    sequence_value = db.execute(
        text("SELECT nextval('half_moon_order_number_seq')")
    ).scalar_one()
    return f"HM-{now.year}-{int(sequence_value):06d}"


def _select_units_for_source(
    db: Session,
    lines: list[POSOrderLineRequest],
    source: Location,
) -> dict[UUID, list[InventoryUnit]] | None:
    selected_units: dict[UUID, list[InventoryUnit]] = {}

    for line in sorted(lines, key=lambda line: str(line.variant_id)):
        units = (
            db.query(InventoryUnit)
            .filter(
                InventoryUnit.variant_id == line.variant_id,
                InventoryUnit.location_id == source.id,
                InventoryUnit.status == "IN_STOCK",
            )
            .order_by(InventoryUnit.serial_number)
            .with_for_update(skip_locked=True)
            .limit(line.quantity)
            .all()
        )

        if len(units) < line.quantity:
            return None

        selected_units[line.variant_id] = units

    return selected_units


def create_pending_pos_order(
    db: Session,
    request: CreatePOSCheckoutRequest,
    staff_user_id: UUID | None,
    idempotency_key: str,
) -> Order:
    release_expired_reservations(db)
    flush_or_rollback(db)

    key_hash = _create_pos_key_hash(idempotency_key)
    request_fingerprint = _create_pos_request_fingerprint(
        request,
        staff_user_id,
    )
    _lock_pos_idempotency_key(db, key_hash)

    existing_order = _get_existing_pos_order(
        db,
        key_hash,
        request_fingerprint,
    )
    if existing_order is not None:
        return existing_order

    variant_ids = [line.variant_id for line in request.lines]
    if len(set(variant_ids)) != len(variant_ids):
        raise ValueError("Each variant may appear only once per order")

    if request.customer_id is not None:
        customer = (
            db.query(Customer)
            .filter(Customer.id == request.customer_id)
            .with_for_update()
            .one_or_none()
        )
        if customer is None:
            raise ValueError("Customer was not found")

    variant_rows = (
        db.query(ProductVariant, Product)
        .join(Product, ProductVariant.product_id == Product.id)
        .filter(ProductVariant.id.in_(variant_ids))
        .all()
    )
    variants = {variant.id: (variant, product) for variant, product in variant_rows}

    if len(variants) != len(variant_ids):
        raise ValueError("One or more product variants were not found")

    now = datetime.now(timezone.utc)
    vat_rate = settings.standard_vat_rate
    line_calculations: list[POSLineCalculation] = []

    for line in request.lines:
        variant, product = variants[line.variant_id]
        total_gross = _round_money(variant.retail_price * line.quantity)
        net_amount = _round_money(total_gross / (Decimal("1.00") + vat_rate))
        vat_amount = _round_money(total_gross - net_amount)

        line_calculations.append(
            {
                "request": line,
                "variant": variant,
                "product": product,
                "total_gross": total_gross,
                "net_amount": net_amount,
                "vat_amount": vat_amount,
            }
        )

    locations = (
        db.query(Location)
        .filter(Location.code.in_(("PHYSICAL_STORE", "MAIN_WAREHOUSE")))
        .all()
    )
    locations_by_code = {location.code: location for location in locations}

    source: Location | None = None
    selected_units: dict[UUID, list[InventoryUnit]] | None = None

    for location_code in ("PHYSICAL_STORE", "MAIN_WAREHOUSE"):
        candidate_source = locations_by_code.get(location_code)
        if candidate_source is None:
            continue

        candidate_units = _select_units_for_source(
            db,
            request.lines,
            candidate_source,
        )
        if candidate_units is not None:
            source = candidate_source
            selected_units = candidate_units
            break

    if source is None or selected_units is None:
        raise ValueError("No single location can fulfill the complete order")

    subtotal_gross = _round_money(
        sum(
            (item["total_gross"] for item in line_calculations),
            Decimal("0.00"),
        )
    )
    net_total = _round_money(
        sum(
            (item["net_amount"] for item in line_calculations),
            Decimal("0.00"),
        )
    )
    vat_total = _round_money(subtotal_gross - net_total)
    expires_at = now + timedelta(minutes=settings.reservation_duration_minutes)

    order = Order(
        order_number=_get_next_order_number(db, now),
        idempotency_key_hash=key_hash,
        request_fingerprint=request_fingerprint,
        customer_id=request.customer_id,
        channel="POS",
        fulfillment_method=None,
        order_status="PENDING_PAYMENT",
        payment_status="UNPAID",
        currency="EUR",
        subtotal_gross=subtotal_gross,
        discount_total_gross=Decimal("0.00"),
        taxable_gross=subtotal_gross,
        net_total=net_total,
        vat_total=vat_total,
        total_gross=subtotal_gross,
        standard_vat_rate=vat_rate * 100,
        applied_vat_rate=vat_rate * 100,
        tax_free=False,
    )
    db.add(order)
    flush_or_rollback(db)

    for calculation in line_calculations:
        line = calculation["request"]
        variant = calculation["variant"]
        product = calculation["product"]

        order_line = OrderLine(
            order_id=order.id,
            variant_id=variant.id,
            quantity=line.quantity,
            product_name_snapshot=product.name,
            variant_snapshot={
                "material": variant.material,
                "color": variant.color,
                "size_system": variant.size_system,
                "size_label": variant.size_label,
            },
            sku_snapshot=variant.sku,
            ean_snapshot=variant.ean,
            retail_price_gross=variant.retail_price,
            discount_gross=Decimal("0.00"),
            taxable_gross=calculation["total_gross"],
            net_amount=calculation["net_amount"],
            vat_rate=vat_rate * 100,
            vat_amount=calculation["vat_amount"],
            total_gross=calculation["total_gross"],
        )
        db.add(order_line)
        flush_or_rollback(db)

        for inventory_unit in selected_units[variant.id]:
            previous_status = inventory_unit.status
            inventory_unit.status = "RESERVED"

            db.add(
                Reservation(
                    order_id=order.id,
                    order_line_id=order_line.id,
                    inventory_unit_id=inventory_unit.id,
                    status="ACTIVE",
                    expires_at=expires_at,
                )
            )
            db.add(
                InventoryMovement(
                    inventory_unit_id=inventory_unit.id,
                    movement_type="RESERVATION",
                    from_location_id=source.id,
                    to_location_id=source.id,
                    from_status=previous_status,
                    to_status="RESERVED",
                    order_id=order.id,
                    actor_type="STAFF" if staff_user_id is not None else "SYSTEM",
                    staff_user_id=staff_user_id,
                    note="POS checkout reservation",
                )
            )

    try:
        commit_or_rollback(db)
    except IntegrityError:
        existing_order = _get_existing_pos_order(
            db,
            key_hash,
            request_fingerprint,
        )
        if existing_order is None:
            raise
        return existing_order

    db.refresh(order)
    return order


def get_safe_pos_reservations(
    db: Session,
    order_id: UUID,
) -> list[Reservation] | None:
    order = db.query(Order).filter(Order.id == order_id).with_for_update().one_or_none()
    if order is None or order.channel != "POS":
        return None
    if order.order_status not in {"PENDING_PAYMENT", "EXPIRED"}:
        return None

    order_lines = (
        db.query(OrderLine)
        .filter(OrderLine.order_id == order.id)
        .order_by(OrderLine.id)
        .with_for_update()
        .all()
    )
    expected_unit_count = sum(line.quantity for line in order_lines)
    if not expected_unit_count:
        return None

    line_quantities = {line.id: line.quantity for line in order_lines}
    reservations = (
        db.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .order_by(Reservation.inventory_unit_id)
        .with_for_update()
        .all()
    )
    if len(reservations) != expected_unit_count:
        return None

    reserved_count_by_line: dict[UUID, int] = {}
    for reservation in reservations:
        if reservation.order_line_id not in line_quantities:
            return None
        reserved_count_by_line[reservation.order_line_id] = (
            reserved_count_by_line.get(reservation.order_line_id, 0) + 1
        )

    if any(
        reserved_count_by_line.get(line.id, 0) != line.quantity for line in order_lines
    ):
        return None

    unit_ids = [reservation.inventory_unit_id for reservation in reservations]
    inventory_units = (
        db.query(InventoryUnit)
        .filter(InventoryUnit.id.in_(unit_ids))
        .order_by(InventoryUnit.id)
        .with_for_update()
        .all()
    )
    if len(inventory_units) != expected_unit_count:
        return None

    units_by_id = {unit.id: unit for unit in inventory_units}
    if len(units_by_id) != expected_unit_count:
        return None

    competing_reservation = (
        db.query(Reservation.id)
        .filter(
            Reservation.inventory_unit_id.in_(unit_ids),
            Reservation.order_id != order.id,
            Reservation.status == "ACTIVE",
        )
        .first()
    )
    if competing_reservation is not None:
        return None

    source_location_id = inventory_units[0].location_id
    if source_location_id is None:
        return None

    for unit in inventory_units:
        if unit.location_id != source_location_id:
            return None

    for reservation in reservations:
        unit = units_by_id[reservation.inventory_unit_id]
        if reservation.status == "ACTIVE":
            if unit.status != "RESERVED":
                return None
        elif reservation.status == "EXPIRED":
            if unit.status != "IN_STOCK":
                return None
        else:
            return None

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.reservation_duration_minutes)

    for reservation in reservations:
        unit = units_by_id[reservation.inventory_unit_id]

        if reservation.status == "EXPIRED":
            previous_status = unit.status
            unit.status = "RESERVED"
            reservation.status = "ACTIVE"

            db.add(
                InventoryMovement(
                    inventory_unit_id=unit.id,
                    movement_type="RESERVATION",
                    from_location_id=unit.location_id,
                    to_location_id=unit.location_id,
                    from_status=previous_status,
                    to_status="RESERVED",
                    order_id=order.id,
                    actor_type="SYSTEM",
                    note="POS reservation restored after successful card payment",
                )
            )

        if reservation.expires_at <= now:
            reservation.expires_at = expires_at

    if order.order_status == "EXPIRED":
        order.order_status = "PENDING_PAYMENT"

    return reservations


def _complete_pos_sale(
    db: Session,
    order_id: UUID,
    staff_user_id: UUID | None,
    payment_method: str,
    provider: str,
    provider_payment_id: str | None,
) -> Order:

    order = db.query(Order).filter(Order.id == order_id).with_for_update().one_or_none()
    if order is None:
        raise ValueError("POS order was not found")
    if order.channel != "POS":
        raise ValueError("Order is not a POS order")

    payments = (
        db.query(Payment).filter(Payment.order_id == order.id).with_for_update().all()
    )
    if len(payments) > 1:
        raise ValueError("POS order has multiple payment records")

    payment = payments[0] if payments else None

    if order.order_status == "COMPLETED":
        claim = (
            db.query(PosOrderClaim)
            .filter(PosOrderClaim.order_id == order.id)
            .one_or_none()
        )
        if (
            payment is not None
            and payment.payment_method == payment_method
            and payment.provider == provider
            and payment.provider_payment_id == provider_payment_id
            and payment.status == "PAID"
            and order.payment_status == "PAID"
            and order.paid_at is not None
            and claim is not None
        ):
            return order
        raise ValueError("POS order is already completed with a different payment")

    if (
        order.order_status != "PENDING_PAYMENT"
        or order.payment_status != "UNPAID"
        or order.paid_at is not None
    ):
        raise ValueError("POS order is not awaiting payment")

    if payment_method == "CASH" and payment is None:
        payment = Payment(
            order_id=order.id,
            payment_method="CASH",
            provider="NONE",
            amount=order.total_gross,
            currency=order.currency,
            status="PAID",
        )
        db.add(payment)
    elif payment is None:
        raise ValueError("POS card payment was not found")

    if (
        payment.payment_method != payment_method
        or payment.provider != provider
        or payment.status not in {"PENDING", "PAID"}
        or payment.amount != order.total_gross
        or payment.currency != order.currency
        or payment.provider_payment_id != provider_payment_id
        or payment.provider_checkout_session_id is not None
    ):
        raise ValueError("Existing payment does not match the completed POS payment")

    order_lines = (
        db.query(OrderLine)
        .filter(OrderLine.order_id == order.id)
        .with_for_update()
        .all()
    )
    expected_unit_count = sum(line.quantity for line in order_lines)
    if not expected_unit_count:
        raise ValueError("POS order has no order lines")

    line_quantities = {line.id: line.quantity for line in order_lines}
    reservations = (
        db.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .order_by(Reservation.inventory_unit_id)
        .with_for_update()
        .all()
    )
    if len(reservations) != expected_unit_count:
        raise ValueError("POS order does not have a complete inventory reservation")

    now = datetime.now(timezone.utc)
    reserved_count_by_line: dict[UUID, int] = {}

    for reservation in reservations:
        if (
            reservation.order_line_id not in line_quantities
            or reservation.status != "ACTIVE"
            or reservation.expires_at <= now
        ):
            raise ValueError("POS order has an invalid or expired reservation")

        reserved_count_by_line[reservation.order_line_id] = (
            reserved_count_by_line.get(reservation.order_line_id, 0) + 1
        )

    if any(
        reserved_count_by_line.get(line.id, 0) != line.quantity for line in order_lines
    ):
        raise ValueError("POS order reservations do not match its order lines")

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
    if len(inventory_units) != len(reservations):
        raise ValueError("One or more reserved inventory units were not found")

    units_by_id = {unit.id: unit for unit in inventory_units}
    if len(units_by_id) != len(reservations):
        raise ValueError("POS order contains duplicate inventory reservations")

    source_location_id = inventory_units[0].location_id
    if source_location_id is None or any(
        unit.location_id != source_location_id or unit.status != "RESERVED"
        for unit in inventory_units
    ):
        raise ValueError("POS order reservations are no longer available")

    if (
        db.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .first()
        is not None
    ):
        raise ValueError("POS order already has sold inventory allocations")

    payment.status = "PAID"
    paid_at = datetime.now(timezone.utc)
    claim_code = f"HM-{secrets.randbelow(100_000_000):08d}"
    claim_expires_at = utc_deadline_after_14_days(paid_at)

    for reservation in reservations:
        inventory_unit = units_by_id[reservation.inventory_unit_id]
        previous_location_id = inventory_unit.location_id
        previous_status = inventory_unit.status

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
                actor_type="STAFF" if staff_user_id is not None else "SYSTEM",
                staff_user_id=staff_user_id,
                note=f"POS {payment_method.lower()} sale completed",
            )
        )

    order.order_status = "COMPLETED"
    order.payment_status = "PAID"
    order.paid_at = paid_at

    db.add(
        PosOrderClaim(
            order_id=order.id,
            claim_code_hash=create_pos_claim_code_digest(claim_code),
        )
    )
    flush_or_rollback(db)

    db.add(
        PosReceiptDelivery(
            order_id=order.id,
            encrypted_claim_code=encrypt_pii(claim_code),
            expires_at=claim_expires_at,
        )
    )

    commit_or_rollback(db)

    db.refresh(order)
    return order


def complete_pos_cash_sale(
    db: Session,
    order_id: UUID,
    staff_user_id: UUID | None,
) -> Order:
    return _complete_pos_sale(
        db,
        order_id,
        staff_user_id,
        payment_method="CASH",
        provider="NONE",
        provider_payment_id=None,
    )


def complete_pos_card_sale(
    db: Session,
    order_id: UUID,
    provider_payment_id: str,
) -> Order:
    return _complete_pos_sale(
        db,
        order_id,
        staff_user_id=None,
        payment_method="CARD",
        provider="STRIPE",
        provider_payment_id=provider_payment_id,
    )


class POSPaymentUnavailableError(Exception):
    pass


class POSDataIntegrityError(RuntimeError):
    pass


class POSReceiptNotReadyError(RuntimeError):
    pass


class POSReceiptGoneError(RuntimeError):
    pass


class POSClaimVerificationRequiredError(RuntimeError):
    pass


class POSClaimConflictError(RuntimeError):
    pass


class POSClaimInvalidCodeError(RuntimeError):
    pass


class POSClaimLockedError(RuntimeError):
    pass


class POSClaimExpiredError(RuntimeError):
    pass


def create_pos_card_payment_intent(
    db: Session,
    order_id: UUID,
) -> POSCardCheckoutResponse:
    release_expired_reservations(db)
    flush_or_rollback(db)

    order = db.query(Order).filter(Order.id == order_id).with_for_update().one_or_none()
    if order is None:
        raise ValueError("POS order was not found")
    if order.channel != "POS":
        raise ValueError("Order is not a POS order")
    if order.order_status != "PENDING_PAYMENT" or order.payment_status != "UNPAID":
        raise ValueError("POS order is not awaiting card payment")

    order_lines = (
        db.query(OrderLine)
        .filter(OrderLine.order_id == order.id)
        .with_for_update()
        .all()
    )
    expected_unit_count = sum(line.quantity for line in order_lines)
    if not expected_unit_count:
        raise ValueError("POS order has no order lines")

    line_quantities = {line.id: line.quantity for line in order_lines}
    reservations = (
        db.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .order_by(Reservation.inventory_unit_id)
        .with_for_update()
        .all()
    )
    if len(reservations) != expected_unit_count:
        raise ValueError("POS order does not have a complete inventory reservation")

    now = datetime.now(timezone.utc)
    reserved_count_by_line: dict[UUID, int] = {}

    for reservation in reservations:
        if (
            reservation.order_line_id not in line_quantities
            or reservation.status != "ACTIVE"
            or reservation.expires_at <= now
        ):
            raise ValueError("POS order has an invalid or expired reservation")

        reserved_count_by_line[reservation.order_line_id] = (
            reserved_count_by_line.get(reservation.order_line_id, 0) + 1
        )

    if any(
        reserved_count_by_line.get(line.id, 0) != line.quantity for line in order_lines
    ):
        raise ValueError("POS order reservations do not match its order lines")

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
    if len(inventory_units) != expected_unit_count:
        raise ValueError("One or more reserved inventory units were not found")

    source_location_id = inventory_units[0].location_id
    if source_location_id is None or any(
        unit.status != "RESERVED" or unit.location_id != source_location_id
        for unit in inventory_units
    ):
        raise ValueError("POS order reservations are no longer available")

    reservation_expires_at = min(reservation.expires_at for reservation in reservations)

    payments = (
        db.query(Payment).filter(Payment.order_id == order.id).with_for_update().all()
    )
    if len(payments) > 1:
        raise ValueError("POS order has multiple payment records")

    payment = payments[0] if payments else None
    if payment is None:
        payment = Payment(
            order_id=order.id,
            payment_method="CARD",
            provider="STRIPE",
            amount=order.total_gross,
            currency=order.currency,
            status="PENDING",
        )
        db.add(payment)
        flush_or_rollback(db)
    elif (
        payment.payment_method != "CARD"
        or payment.provider != "STRIPE"
        or payment.status != "PENDING"
        or payment.amount != order.total_gross
        or payment.currency != order.currency
        or payment.provider_checkout_session_id is not None
    ):
        raise ValueError("Existing payment does not match a pending POS card payment")

    existing_payment_intent_id = payment.provider_payment_id
    payment_id = payment.id
    order_id_value = order.id
    order_number = order.order_number
    amount_in_cents = int(order.total_gross * 100)
    currency = order.currency.lower()

    commit_or_rollback(db)

    try:
        stripe_client = get_stripe_client()
        if existing_payment_intent_id is not None:
            payment_intent = stripe_client.v1.payment_intents.retrieve(
                existing_payment_intent_id
            )
        else:
            payment_intent = stripe_client.v1.payment_intents.create(
                {
                    "amount": amount_in_cents,
                    "currency": currency,
                    "automatic_payment_methods": {
                        "enabled": True,
                        "allow_redirects": "never",
                    },
                    "metadata": {
                        "order_id": str(order_id_value),
                        "order_number": order_number,
                        "channel": "POS",
                    },
                },
                {
                    "idempotency_key": (
                        f"half-moon-pos-payment-intent-v1-{order_id_value}"
                    )
                },
            )
    except (StripeError, RuntimeError) as error:
        raise POSPaymentUnavailableError(
            "Stripe card payment could not be started; retry the same POS order."
        ) from error

    intent_metadata = (
        payment_intent.metadata.to_dict() if payment_intent.metadata is not None else {}
    )
    if (
        payment_intent.amount != amount_in_cents
        or payment_intent.currency != currency
        or intent_metadata.get("order_id") != str(order_id_value)
        or intent_metadata.get("channel") != "POS"
    ):
        raise ValueError("Stripe PaymentIntent does not match the POS order")
    if payment_intent.status == "canceled":
        raise ValueError(
            "Stripe PaymentIntent was canceled; start a new checkout "
            "with a new Idempotency-Key"
        )
    if not payment_intent.client_secret:
        raise POSPaymentUnavailableError(
            "Stripe did not return a client secret for the POS PaymentIntent."
        )

    payment = db.query(Payment).filter(Payment.id == payment_id).with_for_update().one()
    if payment.provider_payment_id not in {None, payment_intent.id}:
        raise ValueError("POS payment is linked to a different PaymentIntent")

    payment.provider_payment_id = payment_intent.id
    commit_or_rollback(db)

    return POSCardCheckoutResponse(
        order_id=order_id_value,
        order_number=order_number,
        payment_method="CARD",
        order_status="PENDING_PAYMENT",
        payment_status="UNPAID",
        total_gross=order.total_gross,
        currency=currency.upper(),
        reservation_expires_at=reservation_expires_at,
        payment_intent_client_secret=payment_intent.client_secret,
    )


def _as_pos_payment_method(value: str) -> Literal["CASH", "CARD"]:
    match value:
        case "CASH":
            return "CASH"
        case "CARD":
            return "CARD"
        case _:
            raise POSDataIntegrityError(f"Unexpected POS payment method: {value}")


def _as_order_status(value: str) -> OrderStatus:
    match value:
        case "PENDING_PAYMENT":
            return "PENDING_PAYMENT"
        case "CANCELLED":
            return "CANCELLED"
        case "EXPIRED":
            return "EXPIRED"
        case "PAID":
            return "PAID"
        case "READY_TO_SHIP":
            return "READY_TO_SHIP"
        case "READY_FOR_PICKUP":
            return "READY_FOR_PICKUP"
        case "SHIPPED":
            return "SHIPPED"
        case "DELIVERED":
            return "DELIVERED"
        case "PICKED_UP":
            return "PICKED_UP"
        case "COMPLETED":
            return "COMPLETED"
        case _:
            raise POSDataIntegrityError(f"Unexpected POS order status: {value}")


def _as_payment_status(value: str) -> PaymentStatus:
    match value:
        case "UNPAID":
            return "UNPAID"
        case "PAID":
            return "PAID"
        case "FAILED":
            return "FAILED"
        case "PARTIALLY_REFUNDED":
            return "PARTIALLY_REFUNDED"
        case "REFUNDED":
            return "REFUNDED"
        case _:
            raise POSDataIntegrityError(f"Unexpected POS payment status: {value}")


def _get_pos_order_and_payment(
    db: Session,
    order_id: UUID,
    *,
    lock: bool,
) -> tuple[Order, Payment]:
    order_query = db.query(Order).filter(
        Order.id == order_id,
        Order.channel == "POS",
    )
    if lock:
        order_query = order_query.with_for_update()

    order = order_query.one_or_none()
    if order is None:
        raise ValueError("POS order not found")

    payment_query = db.query(Payment).filter(Payment.order_id == order.id)
    if lock:
        payment_query = payment_query.with_for_update()

    payments = payment_query.all()
    if len(payments) != 1:
        raise POSDataIntegrityError("POS order must have exactly one payment record")

    return order, payments[0]


def get_pos_order_status(
    db: Session,
    order_id: UUID,
) -> POSOrderStatusResponse:
    expired_reservations = release_expired_reservations(db)
    flush_or_rollback(db)

    if expired_reservations["expired_reservations"]:
        commit_or_rollback(db)

    order, payment = _get_pos_order_and_payment(
        db,
        order_id,
        lock=False,
    )

    active_reservations = (
        db.query(Reservation)
        .filter(
            Reservation.order_id == order.id,
            Reservation.status == "ACTIVE",
        )
        .all()
    )
    reservation_expires_at = (
        min(reservation.expires_at for reservation in active_reservations)
        if active_reservations
        else None
    )

    return POSOrderStatusResponse(
        order_id=order.id,
        order_number=order.order_number,
        payment_method=_as_pos_payment_method(payment.payment_method),
        order_status=_as_order_status(order.order_status),
        payment_status=_as_payment_status(order.payment_status),
        total_gross=order.total_gross,
        currency=order.currency,
        reservation_expires_at=reservation_expires_at,
    )


def _get_completed_pos_receipt_claim(
    db: Session,
    order_id: UUID,
) -> tuple[Order, PosOrderClaim, datetime]:
    order, payment = _get_pos_order_and_payment(
        db,
        order_id,
        lock=True,
    )

    paid_at = order.paid_at
    if order.order_status != "COMPLETED" or paid_at is None:
        raise POSReceiptNotReadyError(
            "POS receipt is not available until the sale is completed"
        )

    completed_payment_statuses = {
        "PAID",
        "PARTIALLY_REFUNDED",
        "REFUNDED",
    }
    if (
        order.payment_status not in completed_payment_statuses
        or payment.status not in completed_payment_statuses
    ):
        raise POSDataIntegrityError(
            "Completed POS order has an inconsistent payment state"
        )

    claim = (
        db.query(PosOrderClaim)
        .filter(PosOrderClaim.order_id == order.id)
        .with_for_update()
        .one_or_none()
    )
    if claim is None:
        raise POSDataIntegrityError("Completed POS order has no receipt claim record")

    return order, claim, paid_at


def get_pos_receipt_delivery(
    db: Session,
    order_id: UUID,
) -> POSReceiptDeliveryResponse:
    order, claim, paid_at = _get_completed_pos_receipt_claim(db, order_id)

    if claim.receipt_delivery_acknowledged_at is not None:
        raise POSReceiptGoneError("Receipt delivery has already been acknowledged")

    now = datetime.now(timezone.utc)
    claim_deadline = utc_deadline_after_14_days(paid_at)

    if now > claim_deadline:
        raise POSReceiptGoneError("Receipt delivery has expired")

    delivery = (
        db.query(PosReceiptDelivery)
        .filter(PosReceiptDelivery.order_id == order.id)
        .with_for_update()
        .one_or_none()
    )
    if delivery is None:
        raise POSDataIntegrityError("Unexpired POS receipt delivery copy is missing")

    if delivery.expires_at != claim_deadline:
        raise POSDataIntegrityError(
            "POS receipt delivery deadline does not match the sale"
        )

    try:
        claim_code = decrypt_pii(delivery.encrypted_claim_code)
    except (InvalidToken, UnicodeDecodeError) as error:
        raise POSDataIntegrityError(
            "POS receipt delivery copy cannot be decrypted"
        ) from error

    return POSReceiptDeliveryResponse(
        order_id=order.id,
        order_number=order.order_number,
        claim_code=claim_code,
        claim_expires_at=delivery.expires_at,
    )


def acknowledge_pos_receipt_delivery(
    db: Session,
    order_id: UUID,
) -> POSReceiptDeliveryAcknowledgementResponse:
    order, claim, paid_at = _get_completed_pos_receipt_claim(db, order_id)

    delivery_query = db.query(PosReceiptDelivery).filter(
        PosReceiptDelivery.order_id == order.id
    )
    delivery = delivery_query.with_for_update().one_or_none()

    if claim.receipt_delivery_acknowledged_at is not None:
        if delivery is not None:
            raise POSDataIntegrityError(
                "Acknowledged POS receipt still has an encrypted delivery copy"
            )

        return POSReceiptDeliveryAcknowledgementResponse(
            acknowledged=True,
            acknowledged_at=claim.receipt_delivery_acknowledged_at,
        )

    now = datetime.now(timezone.utc)
    claim_deadline = utc_deadline_after_14_days(paid_at)

    if now > claim_deadline:
        raise POSReceiptGoneError("Receipt delivery has expired")

    if delivery is None:
        raise POSDataIntegrityError("Unexpired POS receipt delivery copy is missing")

    if delivery.expires_at != claim_deadline:
        raise POSDataIntegrityError(
            "POS receipt delivery deadline does not match the sale"
        )

    claim.receipt_delivery_acknowledged_at = now
    db.delete(delivery)
    commit_or_rollback(db)

    return POSReceiptDeliveryAcknowledgementResponse(
        acknowledged=True,
        acknowledged_at=now,
    )


def delete_expired_pos_receipt_deliveries(
    db: Session,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(timezone.utc)

    candidate_order_ids = (
        db.query(PosReceiptDelivery.order_id)
        .filter(PosReceiptDelivery.expires_at < current_time)
        .distinct()
        .order_by(PosReceiptDelivery.order_id)
        .all()
    )

    deleted_count = 0

    try:
        for (order_id,) in candidate_order_ids:
            order = (
                db.query(Order)
                .filter(Order.id == order_id, Order.channel == "POS")
                .with_for_update()
                .one_or_none()
            )
            if order is None:
                raise POSDataIntegrityError(
                    "Expired POS receipt delivery has no POS order"
                )

            claim = (
                db.query(PosOrderClaim)
                .filter(PosOrderClaim.order_id == order.id)
                .with_for_update()
                .one_or_none()
            )
            if claim is None:
                raise POSDataIntegrityError(
                    "Expired POS receipt delivery has no claim record"
                )

            delivery = (
                db.query(PosReceiptDelivery)
                .filter(PosReceiptDelivery.order_id == order.id)
                .with_for_update()
                .one_or_none()
            )
            if delivery is None:
                continue

            if claim.receipt_delivery_acknowledged_at is not None:
                raise POSDataIntegrityError(
                    "Acknowledged POS receipt still has an encrypted delivery copy"
                )

            if delivery.expires_at < current_time:
                db.delete(delivery)
                deleted_count += 1

    except SQLAlchemyError:
        db.rollback()
        raise
    except POSDataIntegrityError:
        db.rollback()
        raise

    commit_or_rollback(db)
    return deleted_count


def claim_anonymous_pos_order(
    db: Session,
    customer_id: UUID,
    request: POSOrderClaimRequest,
) -> POSOrderClaimResponse:
    customer_account = (
        db.query(CustomerAccount)
        .filter(
            CustomerAccount.customer_id == customer_id,
            CustomerAccount.account_status == "ACTIVE",
            CustomerAccount.email_verified_at.is_not(None),
        )
        .one_or_none()
    )
    if customer_account is None:
        raise POSClaimVerificationRequiredError(
            "An active, email-verified customer account is required"
        )

    order = (
        db.query(Order)
        .filter(
            Order.order_number == request.order_number,
            Order.channel == "POS",
        )
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    if order is None:
        raise ValueError("POS order not found")

    if (
        order.customer_id is not None
        or order.order_status != "COMPLETED"
        or order.payment_status != "PAID"
    ):
        raise POSClaimConflictError("POS order is not eligible to be claimed")

    paid_at = order.paid_at
    if paid_at is None:
        raise POSDataIntegrityError("Paid POS order has no paid_at timestamp")

    claim = (
        db.query(PosOrderClaim)
        .filter(PosOrderClaim.order_id == order.id)
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    if claim is None:
        raise POSDataIntegrityError("Paid POS order has no claim record")

    if claim.claimed_at is not None:
        raise POSClaimConflictError("POS claim code has already been used")

    now = datetime.now(timezone.utc)
    claim_deadline = utc_deadline_after_14_days(paid_at)
    if now > claim_deadline:
        raise POSClaimExpiredError("POS claim code has expired")

    if claim.failed_attempt_count >= 5:
        if claim.locked_until is None:
            raise POSClaimLockedError(
                "POS claim is locked; a store manager must reset it"
            )
        if claim.locked_until > now:
            raise POSClaimLockedError(
                "POS claim is temporarily locked; wait for the cooldown to "
                "expire or ask a store manager to reset it"
            )

        claim.failed_attempt_count = 0
        claim.locked_until = None

    supplied_digest = create_pos_claim_code_digest(request.claim_code)
    if not hmac.compare_digest(supplied_digest, claim.claim_code_hash):
        claim.failed_attempt_count += 1
        reached_attempt_limit = claim.failed_attempt_count >= 5
        if reached_attempt_limit:
            claim.locked_until = now + timedelta(seconds=30)
            record_audit_event(
                db,
                actor_type="CUSTOMER",
                customer_id=customer_id,
                action_code="POS_CLAIM_LOCKOUT",
                outcome="FAILED",
                target_type="ORDER",
                target_id=order.id,
                error_code="POS_CLAIM_ATTEMPT_LIMIT",
                details={"failed_attempt_count": claim.failed_attempt_count},
            )
        commit_or_rollback(db)

        if reached_attempt_limit:
            raise POSClaimLockedError(
                "Incorrect claim code; the five-attempt limit has been reached. "
                "Try again after 30 seconds or ask a store manager to reset it."
            )

        raise POSClaimInvalidCodeError("POS claim code is incorrect")

    order.customer_id = customer_id
    claim.claimed_at = now
    record_audit_event(
        db,
        actor_type="CUSTOMER",
        customer_id=customer_id,
        action_code="CUSTOMER_POS_ORDER_CLAIM",
        outcome="SUCCEEDED",
        target_type="ORDER",
        target_id=order.id,
    )
    commit_or_rollback(db)

    return POSOrderClaimResponse(
        claimed=True,
        order_id=order.id,
        order_number=order.order_number,
        claimed_at=now,
    )


def _reset_pos_order_claim_lock(
    db: Session,
    order: Order,
    staff_user_id: UUID,
) -> POSClaimResetResponse:
    claim = (
        db.query(PosOrderClaim)
        .filter(PosOrderClaim.order_id == order.id)
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    if claim is None:
        raise POSDataIntegrityError("POS order has no claim record")

    if claim.claimed_at is not None or order.customer_id is not None:
        raise POSClaimConflictError("A claimed POS order cannot be reset")

    paid_at = order.paid_at
    if paid_at is None:
        raise POSDataIntegrityError("Paid POS order has no paid_at timestamp")

    now = datetime.now(timezone.utc)
    claim_deadline = utc_deadline_after_14_days(paid_at)
    if now > claim_deadline:
        raise POSClaimExpiredError("POS claim code has expired")

    if claim.failed_attempt_count != 5:
        raise POSClaimConflictError("POS claim is not locked")

    failed_attempt_count = claim.failed_attempt_count
    claim.failed_attempt_count = 0
    claim.locked_until = None
    claim.last_reset_at = now
    claim.last_reset_by_staff_id = staff_user_id
    record_audit_event(
        db,
        actor_type="STAFF",
        staff_user_id=staff_user_id,
        action_code="STAFF_POS_CLAIM_RESET",
        outcome="SUCCEEDED",
        target_type="ORDER",
        target_id=order.id,
        details={"failed_attempt_count": failed_attempt_count},
    )
    commit_or_rollback(db)

    return POSClaimResetResponse(
        reset=True,
        failed_attempt_count=0,
        reset_at=now,
    )


def reset_pos_order_claim_lock(
    db: Session,
    order_id: UUID,
    staff_user_id: UUID,
) -> POSClaimResetResponse:
    order = (
        db.query(Order)
        .filter(
            Order.id == order_id,
            Order.channel == "POS",
        )
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    if order is None:
        raise ValueError("POS order not found")

    return _reset_pos_order_claim_lock(db, order, staff_user_id)


def reset_pos_order_claim_lock_by_order_number(
    db: Session,
    order_number: str,
    staff_user_id: UUID,
) -> POSClaimResetResponse:
    order = (
        db.query(Order)
        .filter(
            Order.order_number == order_number.strip(),
            Order.channel == "POS",
        )
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    if order is None:
        raise ValueError("POS order not found")

    return _reset_pos_order_claim_lock(db, order, staff_user_id)
