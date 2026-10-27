from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.app.core.data_protection import (
    create_email_lookup_hmac,
    encrypt_pii,
)
from backend.app.models.customer import Customer
from backend.app.models.inventory_unit import InventoryUnit
from backend.app.models.location import Location
from backend.app.models.order import Order
from backend.app.models.order_address import OrderAddress
from backend.app.models.product import Product
from backend.app.models.product_variant import ProductVariant
from backend.app.models.reservation import Reservation
from backend.app.schemas.orders import (
    CreateOrderRequest,
    GuestCreateOrderRequest,
    OrderLineRequest,
)
from backend.app.services.orders import (
    PendingOrderData,
    create_guest_pending_order,
    create_pending_order,
)


def _shipping_address() -> dict[str, str]:
    return {
        "recipient_name": "Test Buyer",
        "address_line_1": "Via Roma 1",
        "city": "Roma",
        "postal_code": "00100",
        "country_code": "IT",
    }


def _create_inventory(
    db: Session,
    *,
    store_quantity: int,
    warehouse_quantity: int,
) -> tuple[ProductVariant, dict[str, Location]]:
    locations_by_code: dict[str, Location] = {}

    for code, name, location_type in (
        ("PHYSICAL_STORE", "Physical Store", "STORE"),
        ("MAIN_WAREHOUSE", "Main Warehouse", "WAREHOUSE"),
    ):
        location = db.query(Location).filter(Location.code == code).one_or_none()
        if location is None:
            location = Location(
                code=code,
                name=name,
                location_type=location_type,
            )
            db.add(location)
            db.flush()

        locations_by_code[code] = location

    suffix = uuid4().hex
    product = Product(
        name=f"ECOM Order Test {suffix}",
        slug=f"ecom-order-test-{suffix}",
        description="ECOM order creation test product",
        category="NECKLACE",
        image_url="https://example.invalid/ecom-order-test.png",
    )
    db.add(product)
    db.flush()

    variant = ProductVariant(
        product_id=product.id,
        material="Silver",
        color="Silver",
        size_label="Standard",
        sku=f"ECOM-ORDER-{suffix}",
        ean=f"{uuid4().int % 10**13:013d}",
        retail_price=Decimal("10.00"),
    )
    db.add(variant)
    db.flush()

    for location_code, quantity in (
        ("PHYSICAL_STORE", store_quantity),
        ("MAIN_WAREHOUSE", warehouse_quantity),
    ):
        db.add_all(
            [
                InventoryUnit(
                    variant_id=variant.id,
                    serial_number=f"ECOM-{suffix}-{location_code}-{index}",
                    location_id=locations_by_code[location_code].id,
                    status="IN_STOCK",
                )
                for index in range(quantity)
            ]
        )

    db.flush()
    return variant, locations_by_code


def _create_customer(db: Session) -> Customer:
    email = f"ecom-order-{uuid4().hex}@example.com"
    customer = Customer(
        email_ciphertext=encrypt_pii(email),
        email_lookup_hmac=create_email_lookup_hmac(email),
    )
    db.add(customer)
    db.flush()
    return customer


def _create_pending_order(
    db: Session,
    customer: Customer,
    request: CreateOrderRequest,
) -> PendingOrderData:
    return create_pending_order(
        db,
        customer.id,
        request,
        idempotency_key_hash=uuid4().hex,
        request_fingerprint=uuid4().hex,
    )


def test_shipping_remains_default_and_requires_an_address() -> None:
    lines = [{"variant_id": str(uuid4()), "quantity": 1}]

    with pytest.raises(ValidationError):
        CreateOrderRequest.model_validate({"lines": lines})

    request = CreateOrderRequest.model_validate(
        {
            "lines": lines,
            "shipping_address": _shipping_address(),
        }
    )

    assert request.fulfillment_method == "SHIPPING"
    assert request.shipping_address is not None


def test_pickup_discards_even_a_malformed_shipping_address() -> None:
    request = CreateOrderRequest.model_validate(
        {
            "lines": [{"variant_id": str(uuid4()), "quantity": 1}],
            "fulfillment_method": "STORE_PICKUP",
            "shipping_address": {"not": "a valid address"},
        }
    )

    assert request.fulfillment_method == "STORE_PICKUP"
    assert request.shipping_address is None


@pytest.mark.parametrize(
    ("store_quantity", "warehouse_quantity", "expected_location"),
    [
        (1, 1, "MAIN_WAREHOUSE"),
        (1, 0, "PHYSICAL_STORE"),
    ],
    ids=["warehouse-first", "store-fallback"],
)
def test_shipping_keeps_warehouse_priority_and_store_fallback(
    test_db_session: Session,
    store_quantity: int,
    warehouse_quantity: int,
    expected_location: str,
) -> None:
    variant, locations = _create_inventory(
        test_db_session,
        store_quantity=store_quantity,
        warehouse_quantity=warehouse_quantity,
    )
    customer = _create_customer(test_db_session)
    request = CreateOrderRequest.model_validate(
        {
            "lines": [{"variant_id": str(variant.id), "quantity": 1}],
            "shipping_address": _shipping_address(),
        }
    )

    pending_order = _create_pending_order(test_db_session, customer, request)
    order = test_db_session.get(Order, pending_order["id"])
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == pending_order["id"])
        .one()
    )
    reserved_unit = test_db_session.get(
        InventoryUnit,
        reservation.inventory_unit_id,
    )

    assert order is not None
    assert order.fulfillment_method == "SHIPPING"
    assert reserved_unit is not None
    assert reserved_unit.location_id == locations[expected_location].id
    assert (
        test_db_session.query(OrderAddress)
        .filter(OrderAddress.order_id == order.id)
        .count()
        == 1
    )


def test_pickup_reserves_from_store_and_does_not_save_an_address(
    test_db_session: Session,
) -> None:
    variant, locations = _create_inventory(
        test_db_session,
        store_quantity=1,
        warehouse_quantity=1,
    )
    customer = _create_customer(test_db_session)
    request = CreateOrderRequest.model_validate(
        {
            "lines": [{"variant_id": str(variant.id), "quantity": 1}],
            "fulfillment_method": "STORE_PICKUP",
            "shipping_address": {"malformed": True},
        }
    )

    pending_order = _create_pending_order(test_db_session, customer, request)
    order = test_db_session.get(Order, pending_order["id"])
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == pending_order["id"])
        .one()
    )
    reserved_unit = test_db_session.get(
        InventoryUnit,
        reservation.inventory_unit_id,
    )

    assert order is not None
    assert order.fulfillment_method == "STORE_PICKUP"
    assert reserved_unit is not None
    assert reserved_unit.location_id == locations["PHYSICAL_STORE"].id
    assert (
        test_db_session.query(OrderAddress)
        .filter(OrderAddress.order_id == order.id)
        .count()
        == 0
    )


def test_pickup_fails_if_store_cannot_fulfill_the_whole_order(
    test_db_session: Session,
) -> None:
    variant, _ = _create_inventory(
        test_db_session,
        store_quantity=1,
        warehouse_quantity=1,
    )
    customer = _create_customer(test_db_session)
    request = CreateOrderRequest(
        lines=[OrderLineRequest(variant_id=variant.id, quantity=2)],
        fulfillment_method="STORE_PICKUP",
    )
    idempotency_key_hash = uuid4().hex

    with pytest.raises(
        ValueError,
        match="No single location can fulfill the complete order",
    ):
        create_pending_order(
            test_db_session,
            customer.id,
            request,
            idempotency_key_hash=idempotency_key_hash,
            request_fingerprint=uuid4().hex,
        )

    assert (
        test_db_session.query(Order)
        .filter(Order.idempotency_key_hash == idempotency_key_hash)
        .one_or_none()
        is None
    )
    units = (
        test_db_session.query(InventoryUnit)
        .filter(InventoryUnit.variant_id == variant.id)
        .all()
    )
    assert len(units) == 2
    assert {unit.status for unit in units} == {"IN_STOCK"}


def test_guest_pickup_ignores_address_and_does_not_set_phone(
    test_db_session: Session,
) -> None:
    variant, locations = _create_inventory(
        test_db_session,
        store_quantity=1,
        warehouse_quantity=1,
    )
    request = GuestCreateOrderRequest.model_validate(
        {
            "email": f"guest-{uuid4().hex}@example.com",
            "recipient_is_buyer": True,
            "lines": [{"variant_id": str(variant.id), "quantity": 1}],
            "fulfillment_method": "STORE_PICKUP",
            "shipping_address": {"malformed": True},
        }
    )

    pending_order = create_guest_pending_order(
        test_db_session,
        request,
        idempotency_key_hash=uuid4().hex,
        request_fingerprint=uuid4().hex,
    )
    order = test_db_session.get(Order, pending_order["id"])
    assert order is not None

    customer = test_db_session.get(Customer, order.customer_id)
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    reserved_unit = test_db_session.get(
        InventoryUnit,
        reservation.inventory_unit_id,
    )

    assert customer is not None
    assert customer.phone_ciphertext is None
    assert order.fulfillment_method == "STORE_PICKUP"
    assert reserved_unit is not None
    assert reserved_unit.location_id == locations["PHYSICAL_STORE"].id
    assert (
        test_db_session.query(OrderAddress)
        .filter(OrderAddress.order_id == order.id)
        .count()
        == 0
    )
