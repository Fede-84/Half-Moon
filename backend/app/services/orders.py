from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, TypedDict
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.database import commit_or_rollback, flush_or_rollback
from backend.app.models.customer import Customer
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.inventory_movement import InventoryMovement
from backend.app.models.inventory_unit import InventoryUnit
from backend.app.models.location import Location
from backend.app.models.order import Order
from backend.app.models.order_address import OrderAddress
from backend.app.models.order_line import OrderLine
from backend.app.models.product import Product
from backend.app.models.product_variant import ProductVariant
from backend.app.models.reservation import Reservation
from backend.app.schemas.orders import (
    CreateOrderRequest,
    GuestCreateOrderRequest,
    ShippingAddressRequest,
)
from backend.app.services.customers import (
    get_or_create_customer,
    update_customer_profile,
)
from backend.app.services.reservations import release_expired_reservations

MONEY_QUANTUM = Decimal("0.01")


class PendingOrderData(TypedDict):
    id: UUID
    order_number: str
    order_status: str
    payment_status: str
    currency: str
    total_gross: str
    reservation_expires_at: datetime
    reserved_quantity: int


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _shipping_address_for_request(
    request: CreateOrderRequest,
) -> ShippingAddressRequest | None:
    if request.fulfillment_method == "STORE_PICKUP":
        return None

    if request.shipping_address is None:
        raise ValueError("Shipping address is required for SHIPPING orders")

    return request.shipping_address


def _get_next_order_number(
    db: Session,
    now: datetime,
) -> str:
    sequence_value = db.execute(
        text("SELECT nextval('half_moon_order_number_seq')")
    ).scalar_one()

    return f"HM-{now.year}-{int(sequence_value):06d}"


def _select_units_for_source(
    db: Session,
    lines: list[Any],
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


def create_pending_order(
    db: Session,
    customer_id: UUID,
    request: CreateOrderRequest,
    idempotency_key_hash: str,
    request_fingerprint: str,
) -> PendingOrderData:
    shipping_address = _shipping_address_for_request(request)
    release_expired_reservations(db)
    flush_or_rollback(db)

    customer = (
        db.query(Customer)
        .filter(Customer.id == customer_id)
        .with_for_update()
        .one_or_none()
    )

    if customer is None:
        raise ValueError("Customer was not found")

    existing_order = (
        db.query(Order)
        .filter(Order.idempotency_key_hash == idempotency_key_hash)
        .one_or_none()
    )

    if existing_order is not None:
        if (
            existing_order.customer_id != customer_id
            or existing_order.request_fingerprint != request_fingerprint
        ):
            raise ValueError(
                "Idempotency-Key was already used with a different request"
            )

        return _serialize_pending_order(db, existing_order)

    return _create_pending_order_after_cleanup(
        db,
        customer_id,
        request,
        shipping_address,
        idempotency_key_hash,
        request_fingerprint,
    )


def _serialize_pending_order(
    db: Session,
    order: Order,
) -> PendingOrderData:
    first_reservation = (
        db.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .order_by(Reservation.expires_at)
        .first()
    )
    order_lines = db.query(OrderLine).filter(OrderLine.order_id == order.id).all()

    return {
        "id": order.id,
        "order_number": order.order_number,
        "order_status": order.order_status,
        "payment_status": order.payment_status,
        "currency": order.currency,
        "total_gross": str(order.total_gross),
        "reservation_expires_at": (
            first_reservation.expires_at
            if first_reservation is not None
            else order.created_at
            + timedelta(minutes=settings.reservation_duration_minutes)
        ),
        "reserved_quantity": sum(line.quantity for line in order_lines),
    }


def _create_pending_order_after_cleanup(
    db: Session,
    customer_id: UUID,
    request: CreateOrderRequest,
    shipping_address: ShippingAddressRequest | None,
    idempotency_key_hash: str,
    request_fingerprint: str,
) -> PendingOrderData:
    variant_ids = [line.variant_id for line in request.lines]

    if len(set(variant_ids)) != len(variant_ids):
        raise ValueError("Each variant may appear only once per order")

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
    line_calculations: list[dict[str, Any]] = []

    for line in request.lines:
        variant, product = variants[line.variant_id]
        total_gross = _round_money(variant.retail_price * line.quantity)
        net_amount = _round_money(total_gross / (Decimal(1) + vat_rate))
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
    location_codes = (
        ("PHYSICAL_STORE",)
        if request.fulfillment_method == "STORE_PICKUP"
        else ("MAIN_WAREHOUSE", "PHYSICAL_STORE")
    )
    source_locations = (
        db.query(Location).filter(Location.code.in_(location_codes)).all()
    )
    locations_by_code = {location.code: location for location in source_locations}

    selected_units = None
    source = None

    for location_code in location_codes:
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
            (calculation["total_gross"] for calculation in line_calculations),
            Decimal(0),
        )
    )
    net_total = _round_money(
        sum(
            (calculation["net_amount"] for calculation in line_calculations),
            Decimal(0),
        )
    )
    vat_total = _round_money(subtotal_gross - net_total)
    order_number = _get_next_order_number(db, now)
    expires_at = now + timedelta(minutes=settings.reservation_duration_minutes)

    order = Order(
        order_number=order_number,
        idempotency_key_hash=idempotency_key_hash,
        request_fingerprint=request_fingerprint,
        customer_id=customer_id,
        channel="ECOM",
        fulfillment_method=request.fulfillment_method,
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

    if shipping_address is not None:
        db.add(
            OrderAddress(
                order_id=order.id,
                address_type="SHIPPING",
                recipient_name=shipping_address.recipient_name,
                address_line_1=shipping_address.address_line_1,
                address_line_2=shipping_address.address_line_2,
                city=shipping_address.city,
                region=shipping_address.region,
                postal_code=shipping_address.postal_code,
                country_code=shipping_address.country_code,
                phone=shipping_address.phone,
            )
        )

    for calculation in line_calculations:
        request_line = calculation["request"]
        variant = calculation["variant"]
        product = calculation["product"]

        order_line = OrderLine(
            order_id=order.id,
            variant_id=variant.id,
            quantity=request_line.quantity,
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
                    actor_type="SYSTEM",
                    note="ECOM checkout reservation",
                )
            )

    try:
        commit_or_rollback(db)
    except IntegrityError:
        existing_order = (
            db.query(Order)
            .filter(Order.idempotency_key_hash == idempotency_key_hash)
            .one_or_none()
        )

        if existing_order is None:
            raise

        if (
            existing_order.customer_id != customer_id
            or existing_order.request_fingerprint != request_fingerprint
        ):
            raise ValueError(
                "Idempotency-Key was already used with a different request"
            )

        return _serialize_pending_order(db, existing_order)

    db.refresh(order)

    return _serialize_pending_order(db, order)


def create_guest_pending_order(
    db: Session,
    request: GuestCreateOrderRequest,
    idempotency_key_hash: str,
    request_fingerprint: str,
) -> PendingOrderData:
    shipping_address = _shipping_address_for_request(request)
    release_expired_reservations(db)
    flush_or_rollback(db)

    customer = get_or_create_customer(db, str(request.email))

    existing_order = (
        db.query(Order)
        .filter(Order.idempotency_key_hash == idempotency_key_hash)
        .one_or_none()
    )

    if existing_order is not None:
        if (
            existing_order.customer_id != customer.id
            or existing_order.request_fingerprint != request_fingerprint
        ):
            raise ValueError(
                "Idempotency-Key was already used with a different request"
            )

        return _serialize_pending_order(db, existing_order)

    existing_account = (
        db.query(CustomerAccount)
        .filter(CustomerAccount.customer_id == customer.id)
        .first()
    )

    if existing_account is not None:
        raise ValueError("Please sign in to place an order with this email")

    update_customer_profile(
        customer,
        first_name=request.first_name,
        last_name=request.last_name,
        phone=(
            shipping_address.phone
            if request.recipient_is_buyer and shipping_address is not None
            else None
        ),
    )

    return _create_pending_order_after_cleanup(
        db,
        customer.id,
        request,
        shipping_address,
        idempotency_key_hash,
        request_fingerprint,
    )
