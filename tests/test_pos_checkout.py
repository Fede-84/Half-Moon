import hashlib
import hmac
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Event
from types import SimpleNamespace
from typing import Literal
from unittest.mock import Mock, call
from uuid import UUID, uuid4

import pytest
import stripe
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app import main as main_module
from backend.app.core.data_protection import (
    create_email_lookup_hmac,
    create_pos_claim_code_digest,
    decrypt_pii,
    encrypt_pii,
)
from backend.app.core.security import create_access_token
from backend.app.models.audit_event import AuditEvent
from backend.app.models.customer import Customer
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.inventory_movement import InventoryMovement
from backend.app.models.inventory_unit import InventoryUnit
from backend.app.models.location import Location
from backend.app.models.order import Order
from backend.app.models.order_address import OrderAddress
from backend.app.models.order_line import OrderLine
from backend.app.models.order_line_unit import OrderLineUnit
from backend.app.models.payment import Payment
from backend.app.models.pos_order_claim import PosOrderClaim
from backend.app.models.pos_receipt_delivery import PosReceiptDelivery
from backend.app.models.product import Product
from backend.app.models.product_variant import ProductVariant
from backend.app.models.refund import Refund
from backend.app.models.reservation import Reservation
from backend.app.models.return_record import ReturnRecord
from backend.app.models.shipment import Shipment
from backend.app.models.staff_user import StaffUser
from backend.app.models.stripe_webhook_event import StripeWebhookEvent
from backend.app.schemas.orders import (
    CreateOrderRequest,
    OrderLineRequest,
    ShippingAddressRequest,
)
from backend.app.schemas.pos import (
    CreatePOSCheckoutRequest,
    POSOrderLineRequest,
)
from backend.app.services import orders as orders_service
from backend.app.services import payments as payments_service
from backend.app.services import pos as pos_service
from backend.app.services import returns as returns_service
from backend.app.services.deadlines import utc_deadline_after_14_days
from backend.app.services.orders import create_pending_order
from backend.app.services.payments import (
    process_pos_payment_intent_event,
    validate_pos_payment_intent_event,
)
from backend.app.services.pos import (
    POSPaymentUnavailableError,
    complete_pos_cash_sale,
    create_pending_pos_order,
    create_pos_card_payment_intent,
    delete_expired_pos_receipt_deliveries,
    get_safe_pos_reservations,
)


def _create_pos_test_data(
    db: Session,
    store_quantity: int = 0,
    warehouse_quantity: int = 0,
):
    suffix = uuid4().hex

    staff = StaffUser(
        email_ciphertext=f"test-email-{suffix}",
        email_lookup_hmac=hashlib.sha256(suffix.encode()).hexdigest(),
        first_name_ciphertext="test-first-name",
        last_name_ciphertext="test-last-name",
        password_hash="test-password-hash",
        role="SALES_ASSOCIATE",
    )

    store = db.query(Location).filter(Location.code == "PHYSICAL_STORE").one_or_none()
    if store is None:
        store = Location(
            code="PHYSICAL_STORE",
            name="Physical Store",
            location_type="STORE",
        )

    warehouse = (
        db.query(Location).filter(Location.code == "MAIN_WAREHOUSE").one_or_none()
    )
    if warehouse is None:
        warehouse = Location(
            code="MAIN_WAREHOUSE",
            name="Main Warehouse",
            location_type="WAREHOUSE",
        )

    product = Product(
        name=f"POS Test Necklace {suffix}",
        slug=f"pos-test-necklace-{suffix}",
        description="POS checkout test product",
        category="NECKLACE",
        image_url="https://example.invalid/pos-test-necklace.png",
    )
    db.add_all([staff, store, warehouse, product])
    db.flush()

    variant = ProductVariant(
        product_id=product.id,
        material="Silver",
        color="Silver",
        size_label="Standard",
        sku=f"POS-TEST-{suffix}",
        ean=f"{uuid4().int % 10**13:013d}",
        retail_price=Decimal("189.00"),
    )
    db.add(variant)
    db.flush()

    store_units = [
        InventoryUnit(
            variant_id=variant.id,
            serial_number=f"POS-STORE-{suffix}-{index}",
            location_id=store.id,
            status="IN_STOCK",
        )
        for index in range(store_quantity)
    ]
    warehouse_units = [
        InventoryUnit(
            variant_id=variant.id,
            serial_number=f"POS-WAREHOUSE-{suffix}-{index}",
            location_id=warehouse.id,
            status="IN_STOCK",
        )
        for index in range(warehouse_quantity)
    ]
    db.add_all([*store_units, *warehouse_units])
    db.flush()

    return staff, variant, store_units, warehouse_units


def _create_pos_claim_customer(
    db: Session,
    *,
    verified: bool = True,
) -> Customer:
    suffix = uuid4().hex
    email = f"pos-claim-{suffix}@example.invalid"
    customer = Customer(
        email_ciphertext=encrypt_pii(email),
        email_lookup_hmac=create_email_lookup_hmac(email),
    )
    db.add(customer)
    db.flush()

    db.add(
        CustomerAccount(
            customer_id=customer.id,
            password_hash="test-password-hash",
            account_status="ACTIVE",
            email_verified_at=(datetime.now(timezone.utc) if verified else None),
        )
    )
    db.flush()
    return customer


def _customer_authorization_headers(customer: Customer) -> dict[str, str]:
    token = create_access_token(subject=str(customer.id))
    return {"Authorization": f"Bearer {token}"}


def _create_claimable_pos_sale(
    api_client,
    db: Session,
    *,
    quantity: int = 1,
    source: Literal["PHYSICAL_STORE", "MAIN_WAREHOUSE"] = "PHYSICAL_STORE",
) -> tuple[Order, str, StaffUser, dict[str, str]]:
    staff, variant, _, _ = _create_pos_test_data(
        db,
        store_quantity=quantity if source == "PHYSICAL_STORE" else 0,
        warehouse_quantity=quantity if source == "MAIN_WAREHOUSE" else 0,
    )
    staff_headers = _staff_authorization_headers(
        staff,
        f"pos-claim-test-{uuid4().hex}",
    )

    checkout = api_client.post(
        "/api/v1/staff/pos/checkout",
        headers=staff_headers,
        json={
            "lines": [{"variant_id": str(variant.id), "quantity": quantity}],
            "payment_method": "CASH",
        },
    )
    assert checkout.status_code == 201

    order = db.query(Order).filter(Order.id == checkout.json()["order_id"]).one()
    receipt = api_client.get(
        f"/api/v1/staff/pos/orders/{order.id}/receipt",
        headers=staff_headers,
    )
    assert receipt.status_code == 200

    return order, receipt.json()["claim_code"], staff, staff_headers


def _staff_authorization_headers(
    staff: StaffUser,
    idempotency_key: str,
) -> dict[str, str]:
    token = create_access_token(
        subject=str(staff.id),
        role=staff.role,
    )
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idempotency_key,
    }


def _checkout_request(
    variant_id,
    quantity: int = 1,
    payment_method: Literal["CASH", "CARD"] = "CASH",
) -> CreatePOSCheckoutRequest:
    return CreatePOSCheckoutRequest(
        lines=[
            POSOrderLineRequest(
                variant_id=variant_id,
                quantity=quantity,
            )
        ],
        payment_method=payment_method,
    )


def _create_pos_webhook_test_payment(
    db: Session,
) -> tuple[Order, Payment]:
    staff, variant, _, _ = _create_pos_test_data(
        db,
        store_quantity=1,
    )
    order = create_pending_pos_order(
        db,
        _checkout_request(variant.id, payment_method="CARD"),
        staff.id,
        f"pos-webhook-validator-{uuid4().hex}",
    )
    payment = Payment(
        order_id=order.id,
        payment_method="CARD",
        provider="STRIPE",
        provider_payment_id=f"pi_pos_webhook_{uuid4().hex}",
        amount=order.total_gross,
        currency=order.currency,
        status="PENDING",
    )
    db.add(payment)
    db.flush()
    return order, payment


def _make_pos_payment_intent_event(
    order: Order,
    payment: Payment,
    event_type: str,
    status: str,
    *,
    amount: int | None = None,
    currency: str | None = None,
    metadata_overrides: dict[str, str] | None = None,
) -> stripe.Event:
    metadata = {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "channel": "POS",
    }
    if metadata_overrides is not None:
        metadata.update(metadata_overrides)

    return stripe.Event.construct_from(
        {
            "id": f"evt_pos_test_{uuid4().hex}",
            "object": "event",
            "api_version": "2024-06-20",
            "created": int(datetime.now(timezone.utc).timestamp()),
            "data": {
                "object": {
                    "id": payment.provider_payment_id,
                    "object": "payment_intent",
                    "status": status,
                    "amount": (int(payment.amount * 100) if amount is None else amount),
                    "currency": (
                        payment.currency.lower() if currency is None else currency
                    ),
                    "metadata": metadata,
                }
            },
            "livemode": False,
            "pending_webhooks": 1,
            "request": None,
            "type": event_type,
        },
        None,
    )


def _signed_payment_intent_webhook(
    event_type: str,
    status: str,
    secret: str,
    channel: str,
    *,
    payment_intent_id: str | None = None,
    order_id: UUID | None = None,
    order_number: str | None = None,
    amount: int = 18900,
    currency: str = "eur",
) -> tuple[bytes, dict[str, str]]:
    timestamp = int(datetime.now(timezone.utc).timestamp())
    event_id = f"evt_pos_route_test_{uuid4().hex}"
    payload = json.dumps(
        {
            "id": event_id,
            "object": "event",
            "api_version": "2024-06-20",
            "created": timestamp,
            "data": {
                "object": {
                    "id": payment_intent_id or f"pi_pos_route_test_{uuid4().hex}",
                    "object": "payment_intent",
                    "status": status,
                    "amount": amount,
                    "currency": currency,
                    "metadata": {
                        "channel": channel,
                        "order_id": str(order_id or uuid4()),
                        "order_number": order_number or "HM-ROUTE-TEST",
                    },
                }
            },
            "livemode": False,
            "pending_webhooks": 1,
            "request": None,
            "type": event_type,
        },
        separators=(",", ":"),
    ).encode("utf-8")

    signed_payload = str(timestamp).encode("utf-8") + b"." + payload
    digest = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    return payload, {
        "Stripe-Signature": f"t={timestamp},v1={digest}",
    }


def test_pos_checkout_request_schemas_expose_only_approved_fields():
    assert set(CreatePOSCheckoutRequest.model_fields) == {
        "customer_id",
        "lines",
        "payment_method",
    }
    assert set(POSOrderLineRequest.model_fields) == {"quantity", "variant_id"}


def test_pending_pos_order_prefers_store_and_snapshots_catalog_price(
    test_db_session: Session,
):
    staff, variant, store_units, warehouse_units = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
        warehouse_quantity=1,
    )

    order = create_pending_pos_order(
        test_db_session,
        _checkout_request(variant.id),
        staff.id,
        "pos-idempotency-key-0001",
    )

    order_line = (
        test_db_session.query(OrderLine).filter(OrderLine.order_id == order.id).one()
    )
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    reserved_unit = test_db_session.get(
        InventoryUnit,
        reservation.inventory_unit_id,
    )

    assert reserved_unit is not None
    assert order.channel == "POS"
    assert order.order_status == "PENDING_PAYMENT"
    assert order.payment_status == "UNPAID"
    assert order.customer_id is None
    assert order.total_gross == Decimal("189.00")
    assert order.discount_total_gross == Decimal("0.00")
    assert order.net_total == Decimal("154.92")
    assert order.vat_total == Decimal("34.08")

    assert order_line.variant_id == variant.id
    assert order_line.product_name_snapshot.startswith("POS Test Necklace ")
    assert order_line.retail_price_gross == Decimal("189.00")
    assert order_line.discount_gross == Decimal("0.00")
    assert order_line.total_gross == Decimal("189.00")

    assert reservation.status == "ACTIVE"
    assert reserved_unit.status == "RESERVED"
    assert reserved_unit.id == store_units[0].id
    warehouse_unit = test_db_session.get(
        InventoryUnit,
        warehouse_units[0].id,
    )
    assert warehouse_unit is not None
    assert warehouse_unit.status == "IN_STOCK"

    movement = (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "RESERVATION",
        )
        .one()
    )
    assert movement.actor_type == "STAFF"
    assert movement.staff_user_id == staff.id
    assert movement.from_location_id == store_units[0].location_id
    assert movement.to_status == "RESERVED"


@pytest.mark.parametrize(
    ("catalog_price", "expected_net", "expected_vat"),
    [
        (Decimal("189.00"), Decimal("154.92"), Decimal("34.08")),
        (Decimal("0.05"), Decimal("0.04"), Decimal("0.01")),
    ],
    ids=["standard-price", "rounding-edge"],
)
def test_ecom_and_pos_financial_snapshots_match_and_survive_catalog_price_change(
    test_db_session: Session,
    catalog_price: Decimal,
    expected_net: Decimal,
    expected_vat: Decimal,
):
    staff, variant, _, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
        warehouse_quantity=1,
    )
    variant.retail_price = catalog_price
    test_db_session.flush()
    customer = _create_pos_claim_customer(test_db_session)

    ecom_request = CreateOrderRequest.model_validate(
        {
            "lines": [{"variant_id": str(variant.id), "quantity": 1}],
            "shipping_address": {
                "recipient_name": "VAT Test Buyer",
                "address_line_1": "Via Roma 1",
                "city": "Roma",
                "postal_code": "00100",
                "country_code": "IT",
            },
        }
    )
    ecom_pending_order = create_pending_order(
        test_db_session,
        customer.id,
        ecom_request,
        idempotency_key_hash=uuid4().hex,
        request_fingerprint=uuid4().hex,
    )
    ecom_order = test_db_session.get(Order, ecom_pending_order["id"])
    assert ecom_order is not None

    pos_order = create_pending_pos_order(
        test_db_session,
        _checkout_request(variant.id),
        staff.id,
        f"pos-vat-parity-{uuid4().hex}",
    )
    ecom_order_line = (
        test_db_session.query(OrderLine)
        .filter(OrderLine.order_id == ecom_order.id)
        .one()
    )
    pos_order_line = (
        test_db_session.query(OrderLine)
        .filter(OrderLine.order_id == pos_order.id)
        .one()
    )

    expected_order_financials = (
        catalog_price,
        Decimal("0.00"),
        catalog_price,
        expected_net,
        expected_vat,
        catalog_price,
        Decimal("22.00"),
        Decimal("22.00"),
    )
    for financial_order in (ecom_order, pos_order):
        assert (
            financial_order.subtotal_gross,
            financial_order.discount_total_gross,
            financial_order.taxable_gross,
            financial_order.net_total,
            financial_order.vat_total,
            financial_order.total_gross,
            financial_order.standard_vat_rate,
            financial_order.applied_vat_rate,
        ) == expected_order_financials

    expected_line_financials = (
        catalog_price,
        Decimal("0.00"),
        catalog_price,
        expected_net,
        Decimal("22.00"),
        expected_vat,
        catalog_price,
    )
    for financial_order_line in (ecom_order_line, pos_order_line):
        assert (
            financial_order_line.retail_price_gross,
            financial_order_line.discount_gross,
            financial_order_line.taxable_gross,
            financial_order_line.net_amount,
            financial_order_line.vat_rate,
            financial_order_line.vat_amount,
            financial_order_line.total_gross,
        ) == expected_line_financials

    variant.retail_price = Decimal("250.00")
    test_db_session.flush()
    for financial_order in (ecom_order, pos_order):
        test_db_session.refresh(financial_order)
        assert financial_order.total_gross == catalog_price
        assert financial_order.net_total == expected_net
        assert financial_order.vat_total == expected_vat

    for financial_order_line in (ecom_order_line, pos_order_line):
        test_db_session.refresh(financial_order_line)
        assert financial_order_line.retail_price_gross == catalog_price
        assert financial_order_line.net_amount == expected_net
        assert financial_order_line.vat_amount == expected_vat
        assert financial_order_line.total_gross == catalog_price


def test_pending_pos_order_falls_back_to_warehouse_for_whole_order(
    test_db_session: Session,
):
    staff, variant, store_units, warehouse_units = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
        warehouse_quantity=2,
    )

    order = create_pending_pos_order(
        test_db_session,
        _checkout_request(variant.id, quantity=2),
        staff.id,
        "pos-idempotency-key-0002",
    )

    reservations = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .all()
    )
    reserved_unit_ids = {reservation.inventory_unit_id for reservation in reservations}

    assert len(reservations) == 2
    assert reserved_unit_ids == {unit.id for unit in warehouse_units}
    store_unit = test_db_session.get(
        InventoryUnit,
        store_units[0].id,
    )
    assert store_unit is not None
    assert store_unit.status == "IN_STOCK"


def test_pending_pos_order_rejects_split_fulfillment(
    test_db_session: Session,
):
    staff, variant, store_units, warehouse_units = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
        warehouse_quantity=1,
    )

    pos_order_count_before = (
        test_db_session.query(Order).filter(Order.channel == "POS").count()
    )
    reservation_count_before = test_db_session.query(Reservation).count()

    with pytest.raises(
        ValueError,
        match="No single location can fulfill the complete order",
    ):
        create_pending_pos_order(
            test_db_session,
            _checkout_request(variant.id, quantity=2),
            staff.id,
            "pos-idempotency-key-0003",
        )

    assert (
        test_db_session.query(Order).filter(Order.channel == "POS").count()
        == pos_order_count_before
    )
    assert test_db_session.query(Reservation).count() == reservation_count_before
    store_unit = test_db_session.get(
        InventoryUnit,
        store_units[0].id,
    )
    warehouse_unit = test_db_session.get(
        InventoryUnit,
        warehouse_units[0].id,
    )
    assert store_unit is not None
    assert warehouse_unit is not None
    assert store_unit.status == "IN_STOCK"
    assert warehouse_unit.status == "IN_STOCK"


def test_pending_pos_order_idempotency_replays_and_rejects_changed_request(
    test_db_session: Session,
):
    staff, variant, _, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    key = "pos-idempotency-key-0004"
    request = _checkout_request(variant.id)

    pos_order_count_before = (
        test_db_session.query(Order).filter(Order.channel == "POS").count()
    )
    first_order = create_pending_pos_order(
        test_db_session,
        request,
        staff.id,
        key,
    )
    replayed_order = create_pending_pos_order(
        test_db_session,
        request,
        staff.id,
        key,
    )

    assert replayed_order.id == first_order.id
    assert (
        test_db_session.query(Order).filter(Order.channel == "POS").count()
        == pos_order_count_before + 1
    )
    assert (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == first_order.id)
        .count()
        == 1
    )

    changed_request = _checkout_request(
        variant.id,
        payment_method="CARD",
    )
    with pytest.raises(
        ValueError,
        match="Idempotency-Key was already used with a different request",
    ):
        create_pending_pos_order(
            test_db_session,
            changed_request,
            staff.id,
            key,
        )


def test_pos_unit_allocation_locks_variants_in_uuid_order():
    variant_ids = sorted((uuid4(), uuid4()), key=str, reverse=True)
    lines = [
        POSOrderLineRequest(variant_id=variant_id, quantity=1)
        for variant_id in variant_ids
    ]
    query = Mock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.with_for_update.return_value = query
    query.limit.return_value = query
    query.all.return_value = [object()]
    db = Mock()
    db.query.return_value = query

    selected_units = pos_service._select_units_for_source(
        db,
        lines,
        Location(
            id=uuid4(),
            code="POS_LOCK_ORDER_TEST",
            name="POS Lock Order Test",
            location_type="STORE",
        ),
    )

    locked_variant_ids = [
        call.args[0].right.value for call in query.filter.call_args_list
    ]
    assert selected_units is not None
    assert locked_variant_ids == sorted(variant_ids, key=str)
    assert query.with_for_update.call_args_list == [
        call(skip_locked=True),
        call(skip_locked=True),
    ]


def test_ecom_unit_allocation_locks_variants_in_uuid_order():
    variant_ids = sorted((uuid4(), uuid4()), key=str, reverse=True)
    lines = [
        POSOrderLineRequest(variant_id=variant_id, quantity=1)
        for variant_id in variant_ids
    ]
    query = Mock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.with_for_update.return_value = query
    query.limit.return_value = query
    query.all.return_value = [object()]
    db = Mock()
    db.query.return_value = query

    selected_units = orders_service._select_units_for_source(
        db,
        lines,
        Location(
            id=uuid4(),
            code="ECOM_LOCK_ORDER_TEST",
            name="ECOM Lock Order Test",
            location_type="STORE",
        ),
    )

    locked_variant_ids = [
        call.args[0].right.value for call in query.filter.call_args_list
    ]
    assert selected_units is not None
    assert locked_variant_ids == sorted(variant_ids, key=str)
    assert query.with_for_update.call_args_list == [
        call(skip_locked=True),
        call(skip_locked=True),
    ]


@pytest.mark.parametrize(
    ("holder_channel", "contender_channel"),
    [("ECOM", "POS"), ("POS", "ECOM")],
)
def test_ecom_and_pos_concurrent_checkouts_cannot_reserve_same_unit(
    half_moon_test_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    holder_channel: Literal["ECOM", "POS"],
    contender_channel: Literal["ECOM", "POS"],
):
    session_factory = sessionmaker(
        bind=half_moon_test_engine,
        expire_on_commit=False,
    )
    canonical_location_codes = ("PHYSICAL_STORE", "MAIN_WAREHOUSE")
    staff_id: UUID | None = None
    customer_id: UUID | None = None
    variant_id: UUID | None = None
    store_unit_ids: list[UUID] = []
    created_location_ids: list[UUID] = []

    try:
        with session_factory() as setup_db:
            existing_location_ids = {
                location.id
                for location in (
                    setup_db.query(Location)
                    .filter(Location.code.in_(canonical_location_codes))
                    .all()
                )
            }
            staff, variant, store_units, _ = _create_pos_test_data(
                setup_db,
                store_quantity=1,
                warehouse_quantity=0,
            )
            customer_email = f"checkout-race-{uuid4().hex}@example.invalid"
            customer = Customer(
                email_ciphertext=encrypt_pii(customer_email),
                email_lookup_hmac=create_email_lookup_hmac(customer_email),
            )
            setup_db.add(customer)
            setup_db.flush()

            staff_id = staff.id
            customer_id = customer.id
            variant_id = variant.id
            store_unit_ids = [unit.id for unit in store_units]
            created_location_ids = [
                location.id
                for location in (
                    setup_db.query(Location)
                    .filter(Location.code.in_(canonical_location_codes))
                    .all()
                )
                if location.id not in existing_location_ids
            ]
            setup_db.commit()

        assert staff_id is not None
        assert customer_id is not None
        assert variant_id is not None

        ecom_request = CreateOrderRequest(
            lines=[OrderLineRequest(variant_id=variant_id, quantity=1)],
            fulfillment_method="SHIPPING",
            shipping_address=ShippingAddressRequest(
                recipient_name="Concurrency Test",
                address_line_1="1 Test Street",
                city="Rome",
                postal_code="00100",
                country_code="IT",
            ),
        )
        pos_request = CreatePOSCheckoutRequest(
            lines=[POSOrderLineRequest(variant_id=variant_id, quantity=1)],
            payment_method="CARD",
        )

        def create_checkout(
            channel: Literal["ECOM", "POS"],
            checkout_db: Session,
        ) -> UUID:
            if channel == "ECOM":
                pending_order = orders_service.create_pending_order(
                    checkout_db,
                    customer_id,
                    ecom_request,
                    idempotency_key_hash=uuid4().hex,
                    request_fingerprint=uuid4().hex,
                )
                return pending_order["id"]

            pending_pos_order = pos_service.create_pending_pos_order(
                checkout_db,
                pos_request,
                staff_id,
                uuid4().hex,
            )
            return pending_pos_order.id

        holder_locked_units = Event()
        release_holder_commit = Event()
        contender_finished = Event()
        original_holder_commit = (
            orders_service.commit_or_rollback
            if holder_channel == "ECOM"
            else pos_service.commit_or_rollback
        )

        def pause_holder_commit(checkout_db: Session) -> None:
            holder_locked_units.set()
            if not release_holder_commit.wait(timeout=30):
                raise TimeoutError("Timed out waiting to release checkout lock")
            original_holder_commit(checkout_db)

        if holder_channel == "ECOM":
            monkeypatch.setattr(
                orders_service,
                "commit_or_rollback",
                pause_holder_commit,
            )
        else:
            monkeypatch.setattr(
                pos_service,
                "commit_or_rollback",
                pause_holder_commit,
            )

        def create_holder_checkout() -> UUID:
            with session_factory() as checkout_db:
                return create_checkout(holder_channel, checkout_db)

        def create_contending_checkout() -> tuple[str, UUID | str]:
            try:
                with session_factory() as checkout_db:
                    return "created", create_checkout(contender_channel, checkout_db)
            except ValueError as error:
                return "rejected", str(error)
            finally:
                contender_finished.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            holder_future = executor.submit(create_holder_checkout)
            contender_future = None
            try:
                assert holder_locked_units.wait(timeout=10), (
                    "The first checkout did not reach its locked-unit commit point"
                )
                contender_future = executor.submit(create_contending_checkout)
                contender_finished_before_release = contender_finished.wait(timeout=10)
            finally:
                release_holder_commit.set()

            holder_order_id = holder_future.result(timeout=10)
            assert contender_future is not None
            contender_result = contender_future.result(timeout=10)

        assert contender_finished_before_release
        assert contender_result[0] == "rejected"
        assert "No single location can fulfill" in str(contender_result[1])

        with session_factory() as verification_db:
            orders = (
                verification_db.query(Order)
                .join(OrderLine, OrderLine.order_id == Order.id)
                .filter(OrderLine.variant_id == variant_id)
                .all()
            )
            active_reservations = (
                verification_db.query(Reservation)
                .filter(
                    Reservation.inventory_unit_id.in_(store_unit_ids),
                    Reservation.status == "ACTIVE",
                )
                .all()
            )
            movements = (
                verification_db.query(InventoryMovement)
                .filter(InventoryMovement.inventory_unit_id.in_(store_unit_ids))
                .all()
            )
            reserved_unit = verification_db.get(InventoryUnit, store_unit_ids[0])

            assert [order.id for order in orders] == [holder_order_id]
            assert len(active_reservations) == 1
            assert active_reservations[0].order_id == holder_order_id
            assert len(movements) == 1
            assert movements[0].order_id == holder_order_id
            assert movements[0].movement_type == "RESERVATION"
            assert reserved_unit is not None
            assert reserved_unit.status == "RESERVED"
    finally:
        if variant_id is not None:
            with session_factory() as cleanup_db:
                product_ids = [
                    product_id
                    for (product_id,) in (
                        cleanup_db.query(ProductVariant.product_id)
                        .filter(ProductVariant.id == variant_id)
                        .all()
                    )
                ]
                order_ids = [
                    order_id
                    for (order_id,) in (
                        cleanup_db.query(Order.id)
                        .join(OrderLine, OrderLine.order_id == Order.id)
                        .filter(OrderLine.variant_id == variant_id)
                        .all()
                    )
                ]

                if store_unit_ids:
                    cleanup_db.query(Reservation).filter(
                        Reservation.inventory_unit_id.in_(store_unit_ids)
                    ).delete(synchronize_session=False)
                    cleanup_db.query(OrderLineUnit).filter(
                        OrderLineUnit.inventory_unit_id.in_(store_unit_ids)
                    ).delete(synchronize_session=False)
                    cleanup_db.query(InventoryMovement).filter(
                        InventoryMovement.inventory_unit_id.in_(store_unit_ids)
                    ).delete(synchronize_session=False)

                if order_ids:
                    cleanup_db.query(Payment).filter(
                        Payment.order_id.in_(order_ids)
                    ).delete(synchronize_session=False)
                    cleanup_db.query(OrderAddress).filter(
                        OrderAddress.order_id.in_(order_ids)
                    ).delete(synchronize_session=False)
                    cleanup_db.query(OrderLine).filter(
                        OrderLine.order_id.in_(order_ids)
                    ).delete(synchronize_session=False)
                    cleanup_db.query(Order).filter(Order.id.in_(order_ids)).delete(
                        synchronize_session=False
                    )

                if store_unit_ids:
                    cleanup_db.query(InventoryUnit).filter(
                        InventoryUnit.id.in_(store_unit_ids)
                    ).delete(synchronize_session=False)

                cleanup_db.query(ProductVariant).filter(
                    ProductVariant.id == variant_id
                ).delete(synchronize_session=False)
                if product_ids:
                    cleanup_db.query(Product).filter(
                        Product.id.in_(product_ids)
                    ).delete(synchronize_session=False)
                if customer_id is not None:
                    cleanup_db.query(Customer).filter(
                        Customer.id == customer_id
                    ).delete(synchronize_session=False)
                if staff_id is not None:
                    cleanup_db.query(StaffUser).filter(StaffUser.id == staff_id).delete(
                        synchronize_session=False
                    )
                if created_location_ids:
                    cleanup_db.query(Location).filter(
                        Location.id.in_(created_location_ids)
                    ).delete(synchronize_session=False)
                cleanup_db.commit()


def test_cash_pos_sale_commits_once_and_stores_receipt_claim(
    test_db_session: Session,
):
    staff, variant, store_units, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    request = _checkout_request(variant.id)
    order = create_pending_pos_order(
        test_db_session,
        request,
        staff.id,
        "pos-cash-sale-idempotency-key-0001",
    )
    assert order.paid_at is None

    completed_order = complete_pos_cash_sale(
        test_db_session,
        order.id,
        staff.id,
    )

    assert completed_order.order_status == "COMPLETED"
    assert completed_order.payment_status == "PAID"
    assert completed_order.paid_at is not None
    assert completed_order.paid_at.utcoffset() is not None
    original_paid_at = completed_order.paid_at

    payment = test_db_session.query(Payment).filter(Payment.order_id == order.id).one()
    assert payment.payment_method == "CASH"
    assert payment.provider == "NONE"
    assert payment.status == "PAID"
    assert payment.amount == Decimal("189.00")
    assert payment.currency == "EUR"
    assert payment.provider_payment_id is None

    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    assert reservation.status == "COMMITTED"

    sold_unit = test_db_session.get(InventoryUnit, store_units[0].id)
    assert sold_unit is not None
    assert sold_unit.status == "SOLD"
    assert sold_unit.location_id is None

    allocation = (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .one()
    )
    assert allocation.inventory_unit_id == store_units[0].id

    sale_movement = (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "SALE",
        )
        .one()
    )
    assert sale_movement.actor_type == "STAFF"
    assert sale_movement.staff_user_id == staff.id
    assert sale_movement.from_status == "RESERVED"
    assert sale_movement.to_status == "SOLD"
    assert sale_movement.to_location_id is None

    claim = (
        test_db_session.query(PosOrderClaim)
        .filter(PosOrderClaim.order_id == order.id)
        .one()
    )
    delivery = (
        test_db_session.query(PosReceiptDelivery)
        .filter(PosReceiptDelivery.order_id == order.id)
        .one()
    )
    claim_code = decrypt_pii(delivery.encrypted_claim_code)
    expected_claim_code_hash = hmac.new(
        pos_service.settings.pos_claim_code_hmac_secret.encode("utf-8"),
        f"half-moon-pos-claim-code:v1:{claim_code.strip().upper()}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert re.fullmatch(r"HM-\d{8}", claim_code) is not None
    assert "claim_code" not in PosOrderClaim.__table__.columns
    assert claim.claim_code_hash == expected_claim_code_hash
    assert claim.claim_code_hash == create_pos_claim_code_digest(claim_code)
    assert delivery.encrypted_claim_code != claim_code
    assert completed_order.paid_at is not None
    assert delivery.expires_at == utc_deadline_after_14_days(completed_order.paid_at)

    assert (
        test_db_session.query(Shipment).filter(Shipment.order_id == order.id).count()
        == 0
    )

    replayed_order = complete_pos_cash_sale(
        test_db_session,
        order.id,
        staff.id,
    )
    test_db_session.refresh(replayed_order)
    assert replayed_order.id == completed_order.id
    assert replayed_order.paid_at == original_paid_at
    assert (
        test_db_session.query(Payment).filter(Payment.order_id == order.id).count() == 1
    )
    assert (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "SALE",
        )
        .count()
        == 1
    )
    assert (
        test_db_session.query(PosOrderClaim)
        .filter(PosOrderClaim.order_id == order.id)
        .count()
        == 1
    )


def test_pos_card_payment_intent_uses_server_amount_and_reuses_intent(
    test_db_session: Session,
    monkeypatch,
):
    staff, variant, _, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    order = create_pending_pos_order(
        test_db_session,
        _checkout_request(variant.id, payment_method="CARD"),
        staff.id,
        "pos-card-payment-intent-idempotency-key-0001",
    )

    stripe_client = Mock()
    payment_intent = SimpleNamespace(
        id=f"pi_pos_test_{uuid4().hex}",
        client_secret="pi_pos_test_client_secret",
        amount=18900,
        currency="eur",
        status="requires_payment_method",
        metadata=SimpleNamespace(
            to_dict=lambda: {
                "order_id": str(order.id),
                "order_number": order.order_number,
                "channel": "POS",
            }
        ),
    )
    stripe_client.v1.payment_intents.create.return_value = payment_intent
    stripe_client.v1.payment_intents.retrieve.return_value = payment_intent
    monkeypatch.setattr(
        pos_service,
        "get_stripe_client",
        lambda: stripe_client,
    )

    response = create_pos_card_payment_intent(test_db_session, order.id)

    assert response.order_id == order.id
    assert response.order_number == order.order_number
    assert response.payment_method == "CARD"
    assert response.order_status == "PENDING_PAYMENT"
    assert response.payment_status == "UNPAID"
    assert response.total_gross == Decimal("189.00")
    assert response.currency == "EUR"
    assert response.payment_intent_client_secret == payment_intent.client_secret

    persisted_order = test_db_session.get(Order, order.id)
    assert persisted_order is not None
    assert persisted_order.order_status == "PENDING_PAYMENT"
    assert persisted_order.payment_status == "UNPAID"
    assert persisted_order.paid_at is None

    payment = test_db_session.query(Payment).filter(Payment.order_id == order.id).one()
    assert payment.payment_method == "CARD"
    assert payment.provider == "STRIPE"
    assert payment.status == "PENDING"
    assert payment.amount == Decimal("189.00")
    assert payment.currency == "EUR"
    assert payment.provider_payment_id == payment_intent.id

    create_args = stripe_client.v1.payment_intents.create.call_args.args
    create_params = create_args[0]
    create_options = create_args[1]

    assert create_params["amount"] == 18900
    assert create_params["currency"] == "eur"
    assert create_params["automatic_payment_methods"] == {
        "enabled": True,
        "allow_redirects": "never",
    }
    assert create_params["metadata"]["order_id"] == str(order.id)
    assert create_params["metadata"]["channel"] == "POS"
    assert create_options["idempotency_key"] == (
        f"half-moon-pos-payment-intent-v1-{order.id}"
    )

    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    assert reservation.status == "ACTIVE"

    retried_response = create_pos_card_payment_intent(test_db_session, order.id)

    assert retried_response.payment_intent_client_secret == payment_intent.client_secret
    stripe_client.v1.payment_intents.create.assert_called_once()
    stripe_client.v1.payment_intents.retrieve.assert_called_once_with(payment_intent.id)


def test_pos_card_decline_retry_reuses_payment_and_completes_order(
    test_db_session: Session,
    monkeypatch,
):
    staff, variant, _, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    order = create_pending_pos_order(
        test_db_session,
        _checkout_request(variant.id, payment_method="CARD"),
        staff.id,
        f"pos-card-decline-retry-{uuid4().hex}",
    )

    stripe_client = Mock()
    payment_intent = SimpleNamespace(
        id=f"pi_pos_retry_{uuid4().hex}",
        client_secret="pi_pos_retry_client_secret",
        amount=18900,
        currency="eur",
        status="requires_payment_method",
        metadata=SimpleNamespace(
            to_dict=lambda: {
                "order_id": str(order.id),
                "order_number": order.order_number,
                "channel": "POS",
            }
        ),
    )
    stripe_client.v1.payment_intents.create.return_value = payment_intent
    stripe_client.v1.payment_intents.retrieve.return_value = payment_intent
    monkeypatch.setattr(
        pos_service,
        "get_stripe_client",
        lambda: stripe_client,
    )

    first_response = create_pos_card_payment_intent(test_db_session, order.id)
    payment = test_db_session.query(Payment).filter(Payment.order_id == order.id).one()
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    payment_failed_event = _make_pos_payment_intent_event(
        order,
        payment,
        "payment_intent.payment_failed",
        "requires_payment_method",
    )
    process_pos_payment_intent_event(test_db_session, payment_failed_event)

    assert order.order_status == "PENDING_PAYMENT"
    assert order.payment_status == "UNPAID"
    assert payment.status == "PENDING"
    assert reservation.status == "ACTIVE"
    assert (
        test_db_session.query(Payment).filter(Payment.order_id == order.id).count() == 1
    )

    retry_response = create_pos_card_payment_intent(test_db_session, order.id)

    assert retry_response.order_id == first_response.order_id == order.id
    assert retry_response.payment_intent_client_secret == (
        first_response.payment_intent_client_secret
    )
    stripe_client.v1.payment_intents.create.assert_called_once()
    stripe_client.v1.payment_intents.retrieve.assert_called_once_with(payment_intent.id)
    assert (
        test_db_session.query(Payment).filter(Payment.order_id == order.id).count() == 1
    )
    assert reservation.expires_at > datetime.now(timezone.utc)

    payment_succeeded_event = _make_pos_payment_intent_event(
        order,
        payment,
        "payment_intent.succeeded",
        "succeeded",
    )
    process_pos_payment_intent_event(test_db_session, payment_succeeded_event)
    test_db_session.refresh(order)
    test_db_session.refresh(payment)
    test_db_session.refresh(reservation)

    assert order.order_status == "COMPLETED"
    assert order.payment_status == "PAID"
    assert payment.status == "PAID"
    assert reservation.status == "COMMITTED"
    assert (
        test_db_session.query(Payment).filter(Payment.order_id == order.id).count() == 1
    )
    assert (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "SALE",
        )
        .count()
        == 1
    )


def test_pos_card_payment_intent_wraps_stripe_configuration_error(
    test_db_session: Session,
    monkeypatch,
):
    staff, variant, _, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    order = create_pending_pos_order(
        test_db_session,
        _checkout_request(variant.id, payment_method="CARD"),
        staff.id,
        "pos-card-payment-intent-config-error-0001",
    )

    def missing_stripe_configuration():
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")

    monkeypatch.setattr(
        pos_service,
        "get_stripe_client",
        missing_stripe_configuration,
    )

    with pytest.raises(
        POSPaymentUnavailableError,
        match="Stripe card payment could not be started",
    ):
        create_pos_card_payment_intent(test_db_session, order.id)

    payment = test_db_session.query(Payment).filter(Payment.order_id == order.id).one()
    assert payment.payment_method == "CARD"
    assert payment.provider == "STRIPE"
    assert payment.status == "PENDING"
    assert payment.provider_payment_id is None

    persisted_order = test_db_session.get(Order, order.id)
    assert persisted_order is not None
    assert persisted_order.order_status == "PENDING_PAYMENT"
    assert persisted_order.payment_status == "UNPAID"
    assert persisted_order.paid_at is None

    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    unit = test_db_session.get(InventoryUnit, reservation.inventory_unit_id)
    assert reservation.status == "ACTIVE"
    assert unit is not None
    assert unit.status == "RESERVED"
    assert (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .count()
        == 0
    )

    movements = (
        test_db_session.query(InventoryMovement)
        .filter(InventoryMovement.order_id == order.id)
        .all()
    )
    assert len(movements) == 1
    assert movements[0].movement_type == "RESERVATION"
    assert movements[0].inventory_unit_id == unit.id
    assert movements[0].from_status == "IN_STOCK"
    assert movements[0].to_status == "RESERVED"


def test_safe_pos_reservations_restores_expired_reservation_when_unit_is_in_stock(
    test_db_session: Session,
):
    staff, variant, store_units, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    order = create_pending_pos_order(
        test_db_session,
        _checkout_request(variant.id, payment_method="CARD"),
        staff.id,
        "pos-safe-restore-idempotency-key-0001",
    )
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    unit = test_db_session.get(InventoryUnit, store_units[0].id)
    assert unit is not None

    order.order_status = "EXPIRED"
    reservation.status = "EXPIRED"
    reservation.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    unit.status = "IN_STOCK"
    test_db_session.flush()

    restored_reservations = get_safe_pos_reservations(test_db_session, order.id)
    test_db_session.flush()

    assert restored_reservations == [reservation]
    assert order.order_status == "PENDING_PAYMENT"
    assert reservation.status == "ACTIVE"
    assert reservation.expires_at > datetime.now(timezone.utc)
    assert unit.status == "RESERVED"

    restoration_movement = (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "RESERVATION",
            InventoryMovement.actor_type == "SYSTEM",
        )
        .one()
    )
    assert restoration_movement.inventory_unit_id == unit.id
    assert restoration_movement.from_status == "IN_STOCK"
    assert restoration_movement.to_status == "RESERVED"


def test_safe_pos_reservations_extends_expired_active_reservation(
    test_db_session: Session,
):
    staff, variant, store_units, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    order = create_pending_pos_order(
        test_db_session,
        _checkout_request(variant.id, payment_method="CARD"),
        staff.id,
        "pos-extend-reservation-idempotency-key-0001",
    )
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    unit = test_db_session.get(InventoryUnit, store_units[0].id)
    assert unit is not None

    reservation.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    test_db_session.flush()

    safe_reservations = get_safe_pos_reservations(test_db_session, order.id)

    assert safe_reservations == [reservation]
    assert order.order_status == "PENDING_PAYMENT"
    assert reservation.status == "ACTIVE"
    assert reservation.expires_at > datetime.now(timezone.utc)
    assert unit.status == "RESERVED"
    assert (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "RESERVATION",
            InventoryMovement.actor_type == "SYSTEM",
        )
        .count()
        == 0
    )


def test_safe_pos_reservations_rejects_unavailable_unit(
    test_db_session: Session,
):
    staff, variant, store_units, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    order = create_pending_pos_order(
        test_db_session,
        _checkout_request(variant.id, payment_method="CARD"),
        staff.id,
        "pos-unavailable-unit-idempotency-key-0001",
    )
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    unit = test_db_session.get(InventoryUnit, store_units[0].id)
    assert unit is not None

    order.order_status = "EXPIRED"
    reservation.status = "EXPIRED"
    reservation.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    unit.status = "REPAIR"
    test_db_session.flush()

    safe_reservations = get_safe_pos_reservations(test_db_session, order.id)

    assert safe_reservations is None
    assert order.order_status == "EXPIRED"
    assert reservation.status == "EXPIRED"
    assert unit.status == "REPAIR"
    assert (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "RESERVATION",
            InventoryMovement.actor_type == "SYSTEM",
        )
        .count()
        == 0
    )


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("payment_intent.succeeded", "succeeded"),
        ("payment_intent.payment_failed", "requires_payment_method"),
        ("payment_intent.canceled", "canceled"),
    ],
)
def test_pos_payment_intent_validator_accepts_supported_events(
    test_db_session: Session,
    event_type: str,
    status: str,
):
    order, payment = _create_pos_webhook_test_payment(test_db_session)
    event = _make_pos_payment_intent_event(
        order,
        payment,
        event_type,
        status,
    )

    validated_payment, validated_order = validate_pos_payment_intent_event(
        test_db_session,
        event,
    )

    assert validated_payment is payment
    assert validated_order is order


@pytest.mark.parametrize(
    ("event_type", "status", "amount", "currency", "metadata_overrides", "error"),
    [
        (
            "payment_intent.succeeded",
            "requires_payment_method",
            18900,
            "eur",
            None,
            "status does not match",
        ),
        (
            "payment_intent.succeeded",
            "succeeded",
            18899,
            "eur",
            None,
            "amount or currency",
        ),
        (
            "payment_intent.succeeded",
            "succeeded",
            18900,
            "usd",
            None,
            "amount or currency",
        ),
        (
            "payment_intent.succeeded",
            "succeeded",
            18900,
            "eur",
            {"order_id": "not-the-matching-order"},
            "does not match the POS order",
        ),
        (
            "payment_intent.processing",
            "processing",
            18900,
            "eur",
            None,
            "Unsupported Stripe PaymentIntent event",
        ),
    ],
)
def test_pos_payment_intent_validator_rejects_mismatches(
    test_db_session: Session,
    event_type: str,
    status: str,
    amount: int,
    currency: str,
    metadata_overrides: dict[str, str] | None,
    error: str,
):
    order, payment = _create_pos_webhook_test_payment(test_db_session)
    event = _make_pos_payment_intent_event(
        order,
        payment,
        event_type,
        status,
        amount=amount,
        currency=currency,
        metadata_overrides=metadata_overrides,
    )

    with pytest.raises(ValueError, match=error):
        validate_pos_payment_intent_event(test_db_session, event)


def test_pos_payment_failed_event_keeps_checkout_retryable(
    test_db_session: Session,
):
    checkout_started_at = datetime.now(timezone.utc)
    order, payment = _create_pos_webhook_test_payment(test_db_session)
    checkout_finished_at = datetime.now(timezone.utc)
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    initial_expiration = reservation.expires_at
    assert (
        checkout_started_at + timedelta(minutes=3)
        <= initial_expiration
        <= checkout_finished_at + timedelta(minutes=3)
    )
    event = _make_pos_payment_intent_event(
        order,
        payment,
        "payment_intent.payment_failed",
        "requires_payment_method",
    )

    process_pos_payment_intent_event(test_db_session, event)

    assert order.order_status == "PENDING_PAYMENT"
    assert order.payment_status == "UNPAID"
    assert payment.status == "PENDING"
    assert reservation.status == "ACTIVE"
    assert reservation.expires_at == initial_expiration
    unit = test_db_session.get(InventoryUnit, reservation.inventory_unit_id)
    assert unit is not None
    assert unit.status == "RESERVED"
    assert (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .count()
        == 0
    )

    movements = (
        test_db_session.query(InventoryMovement)
        .filter(InventoryMovement.order_id == order.id)
        .all()
    )
    assert len(movements) == 1
    assert movements[0].movement_type == "RESERVATION"
    assert movements[0].inventory_unit_id == unit.id
    assert movements[0].from_status == "IN_STOCK"
    assert movements[0].to_status == "RESERVED"
    assert (
        test_db_session.query(StripeWebhookEvent)
        .filter(StripeWebhookEvent.stripe_event_id == event.id)
        .count()
        == 1
    )


def test_pos_payment_canceled_event_releases_reservation_and_cancels_order(
    test_db_session: Session,
):
    order, payment = _create_pos_webhook_test_payment(test_db_session)
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    unit = test_db_session.get(InventoryUnit, reservation.inventory_unit_id)
    assert unit is not None

    event = _make_pos_payment_intent_event(
        order,
        payment,
        "payment_intent.canceled",
        "canceled",
    )

    process_pos_payment_intent_event(test_db_session, event)

    assert order.order_status == "CANCELLED"
    assert order.payment_status == "FAILED"
    assert payment.status == "FAILED"
    assert reservation.status == "RELEASED"
    assert reservation.released_at is not None
    assert unit.status == "IN_STOCK"

    release_movement = (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "RELEASE",
        )
        .one()
    )
    assert release_movement.inventory_unit_id == unit.id
    assert release_movement.from_status == "RESERVED"
    assert release_movement.to_status == "IN_STOCK"
    assert (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .count()
        == 0
    )

    movements = (
        test_db_session.query(InventoryMovement)
        .filter(InventoryMovement.order_id == order.id)
        .all()
    )
    assert {
        (movement.movement_type, movement.from_status, movement.to_status)
        for movement in movements
    } == {
        ("RESERVATION", "IN_STOCK", "RESERVED"),
        ("RELEASE", "RESERVED", "IN_STOCK"),
    }


def test_pos_payment_succeeded_completes_sale_once(
    test_db_session: Session,
):
    order, payment = _create_pos_webhook_test_payment(test_db_session)
    assert order.paid_at is None
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    unit = test_db_session.get(InventoryUnit, reservation.inventory_unit_id)
    assert unit is not None
    event = _make_pos_payment_intent_event(
        order,
        payment,
        "payment_intent.succeeded",
        "succeeded",
    )

    process_pos_payment_intent_event(test_db_session, event)
    assert order.paid_at is not None
    assert order.paid_at.utcoffset() is not None
    original_paid_at = order.paid_at

    process_pos_payment_intent_event(test_db_session, event)
    test_db_session.refresh(order)

    assert order.order_status == "COMPLETED"
    assert order.paid_at == original_paid_at
    assert order.payment_status == "PAID"
    assert payment.status == "PAID"
    assert reservation.status == "COMMITTED"
    assert unit.status == "SOLD"
    assert (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .count()
        == 1
    )
    assert (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "SALE",
        )
        .count()
        == 1
    )
    assert (
        test_db_session.query(StripeWebhookEvent)
        .filter(StripeWebhookEvent.stripe_event_id == event.id)
        .count()
        == 1
    )


def test_late_pos_payment_is_refunded_when_reserved_unit_is_unavailable(
    api_client,
    test_db_session: Session,
    monkeypatch,
):
    secret = "whsec_pos_route_test"
    monkeypatch.setattr(
        payments_service.settings,
        "stripe_webhook_secret",
        secret,
    )
    order, payment = _create_pos_webhook_test_payment(test_db_session)
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    unit = test_db_session.get(InventoryUnit, reservation.inventory_unit_id)
    assert unit is not None

    order.order_status = "EXPIRED"
    reservation.status = "EXPIRED"
    unit.status = "REPAIR"
    test_db_session.flush()

    stripe_refund = stripe.Refund.construct_from(
        {
            "id": f"re_pos_test_{uuid4().hex}",
            "object": "refund",
            "amount": int(payment.amount * 100),
            "currency": payment.currency.lower(),
            "payment_intent": payment.provider_payment_id,
            "status": "succeeded",
            "metadata": {"half_moon_payment_id": str(payment.id)},
        },
        None,
    )
    refund_creator = Mock(return_value=stripe_refund)
    monkeypatch.setattr(
        payments_service,
        "create_late_stripe_payment_refund",
        refund_creator,
    )
    payload, headers = _signed_payment_intent_webhook(
        "payment_intent.succeeded",
        "succeeded",
        secret,
        "POS",
        payment_intent_id=payment.provider_payment_id,
        order_id=order.id,
        order_number=order.order_number,
        amount=int(payment.amount * 100),
        currency=payment.currency.lower(),
    )
    stripe_event_id = json.loads(payload)["id"]
    test_db_session.commit()

    first_response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )
    replay_response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )

    assert first_response.status_code == 200
    assert replay_response.status_code == 200
    assert first_response.json() == {"received": True}
    assert replay_response.json() == {"received": True}
    refund_creator.assert_called_once_with(payment, payment.provider_payment_id)
    test_db_session.refresh(order)
    test_db_session.refresh(payment)
    test_db_session.refresh(reservation)
    test_db_session.refresh(unit)
    assert order.order_status == "EXPIRED"
    assert order.payment_status == "REFUNDED"
    assert payment.status == "REFUNDED"
    assert reservation.status == "EXPIRED"
    assert unit.status == "REPAIR"

    refund_record = (
        test_db_session.query(Refund).filter(Refund.payment_id == payment.id).one()
    )
    assert refund_record.status == "REFUNDED"
    assert refund_record.provider_ref == stripe_refund.id
    assert (
        test_db_session.query(Refund).filter(Refund.payment_id == payment.id).count()
        == 1
    )
    assert (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .count()
        == 0
    )
    assert (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "SALE",
        )
        .count()
        == 0
    )
    assert (
        test_db_session.query(StripeWebhookEvent)
        .filter(StripeWebhookEvent.stripe_event_id == stripe_event_id)
        .count()
        == 1
    )


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("payment_intent.succeeded", "succeeded"),
        ("payment_intent.payment_failed", "requires_payment_method"),
        ("payment_intent.canceled", "canceled"),
    ],
)
def test_signed_pos_payment_intent_webhook_dispatches_to_pos_processor(
    api_client,
    monkeypatch,
    event_type: str,
    status: str,
):
    secret = "whsec_pos_route_test"
    monkeypatch.setattr(
        payments_service.settings,
        "stripe_webhook_secret",
        secret,
    )
    processor = Mock()
    monkeypatch.setattr(
        main_module,
        "process_pos_payment_intent_event",
        processor,
    )
    payload, headers = _signed_payment_intent_webhook(
        event_type,
        status,
        secret,
        "POS",
    )

    response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}
    processor.assert_called_once()
    assert processor.call_args.args[1].type == event_type


def test_signed_pos_payment_succeeded_webhook_commits_sale_once(
    api_client,
    test_db_session: Session,
    monkeypatch,
):
    secret = "whsec_pos_route_test"
    monkeypatch.setattr(
        payments_service.settings,
        "stripe_webhook_secret",
        secret,
    )
    order, payment = _create_pos_webhook_test_payment(test_db_session)
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    unit = test_db_session.get(InventoryUnit, reservation.inventory_unit_id)
    assert unit is not None
    payload, headers = _signed_payment_intent_webhook(
        "payment_intent.succeeded",
        "succeeded",
        secret,
        "POS",
        payment_intent_id=payment.provider_payment_id,
        order_id=order.id,
        order_number=order.order_number,
        amount=int(payment.amount * 100),
        currency=payment.currency.lower(),
    )
    stripe_event_id = json.loads(payload)["id"]

    first_response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )
    replay_response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )

    assert first_response.status_code == 200
    assert replay_response.status_code == 200
    assert first_response.json() == {"received": True}
    assert replay_response.json() == {"received": True}
    test_db_session.refresh(order)
    test_db_session.refresh(payment)
    test_db_session.refresh(reservation)
    test_db_session.refresh(unit)
    assert order.order_status == "COMPLETED"
    assert order.payment_status == "PAID"
    assert order.paid_at is not None
    assert payment.status == "PAID"
    assert reservation.status == "COMMITTED"
    assert unit.status == "SOLD"
    assert (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .count()
        == 1
    )
    assert (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "SALE",
        )
        .count()
        == 1
    )
    assert (
        test_db_session.query(StripeWebhookEvent)
        .filter(StripeWebhookEvent.stripe_event_id == stripe_event_id)
        .count()
        == 1
    )


def test_signed_pos_payment_failed_webhook_is_idempotent_and_retryable(
    api_client,
    test_db_session: Session,
    monkeypatch,
):
    secret = "whsec_pos_route_test"
    monkeypatch.setattr(
        payments_service.settings,
        "stripe_webhook_secret",
        secret,
    )
    order, payment = _create_pos_webhook_test_payment(test_db_session)
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    original_reservation_expires_at = reservation.expires_at
    unit = test_db_session.get(InventoryUnit, reservation.inventory_unit_id)
    assert unit is not None
    payload, headers = _signed_payment_intent_webhook(
        "payment_intent.payment_failed",
        "requires_payment_method",
        secret,
        "POS",
        payment_intent_id=payment.provider_payment_id,
        order_id=order.id,
        order_number=order.order_number,
        amount=int(payment.amount * 100),
        currency=payment.currency.lower(),
    )
    stripe_event_id = json.loads(payload)["id"]

    first_response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )
    replay_response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )

    assert first_response.status_code == 200
    assert replay_response.status_code == 200
    assert first_response.json() == {"received": True}
    assert replay_response.json() == {"received": True}
    test_db_session.refresh(order)
    test_db_session.refresh(payment)
    test_db_session.refresh(reservation)
    test_db_session.refresh(unit)
    assert order.order_status == "PENDING_PAYMENT"
    assert order.payment_status == "UNPAID"
    assert payment.status == "PENDING"
    assert reservation.status == "ACTIVE"
    assert reservation.expires_at == original_reservation_expires_at
    assert reservation.expires_at > datetime.now(timezone.utc)
    assert unit.status == "RESERVED"
    assert (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .count()
        == 0
    )
    assert (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "SALE",
        )
        .count()
        == 0
    )
    assert (
        test_db_session.query(StripeWebhookEvent)
        .filter(StripeWebhookEvent.stripe_event_id == stripe_event_id)
        .count()
        == 1
    )


def test_signed_pos_payment_canceled_webhook_is_idempotent_and_terminal(
    api_client,
    test_db_session: Session,
    monkeypatch,
):
    secret = "whsec_pos_route_test"
    monkeypatch.setattr(
        payments_service.settings,
        "stripe_webhook_secret",
        secret,
    )
    order, payment = _create_pos_webhook_test_payment(test_db_session)
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    unit = test_db_session.get(InventoryUnit, reservation.inventory_unit_id)
    assert unit is not None
    payload, headers = _signed_payment_intent_webhook(
        "payment_intent.canceled",
        "canceled",
        secret,
        "POS",
        payment_intent_id=payment.provider_payment_id,
        order_id=order.id,
        order_number=order.order_number,
        amount=int(payment.amount * 100),
        currency=payment.currency.lower(),
    )
    stripe_event_id = json.loads(payload)["id"]

    first_response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )
    replay_response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )

    assert first_response.status_code == 200
    assert replay_response.status_code == 200
    assert first_response.json() == {"received": True}
    assert replay_response.json() == {"received": True}
    test_db_session.refresh(order)
    test_db_session.refresh(payment)
    test_db_session.refresh(reservation)
    test_db_session.refresh(unit)
    assert order.order_status == "CANCELLED"
    assert order.payment_status == "FAILED"
    assert payment.status == "FAILED"
    assert reservation.status == "RELEASED"
    assert reservation.released_at is not None
    assert unit.status == "IN_STOCK"
    assert (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "RELEASE",
        )
        .count()
        == 1
    )
    release_movement = (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "RELEASE",
        )
        .one()
    )
    assert release_movement.from_status == "RESERVED"
    assert release_movement.to_status == "IN_STOCK"
    assert (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .count()
        == 0
    )
    assert (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "SALE",
        )
        .count()
        == 0
    )
    assert (
        test_db_session.query(StripeWebhookEvent)
        .filter(StripeWebhookEvent.stripe_event_id == stripe_event_id)
        .count()
        == 1
    )


@pytest.mark.parametrize(
    ("failure_case", "expected_status"),
    [
        ("invalid_signature", 400),
        ("mismatched_order", 500),
        ("mismatched_amount", 500),
    ],
)
def test_invalid_or_mismatched_signed_pos_webhook_does_not_commit_sale(
    api_client,
    test_db_session: Session,
    monkeypatch,
    failure_case: str,
    expected_status: int,
):
    secret = "whsec_pos_route_test"
    monkeypatch.setattr(
        payments_service.settings,
        "stripe_webhook_secret",
        secret,
    )
    order, payment = _create_pos_webhook_test_payment(test_db_session)
    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order.id)
        .one()
    )
    unit = test_db_session.get(InventoryUnit, reservation.inventory_unit_id)
    assert unit is not None
    webhook_order_id = uuid4() if failure_case == "mismatched_order" else order.id
    webhook_amount = (
        int(payment.amount * 100) - 1
        if failure_case == "mismatched_amount"
        else int(payment.amount * 100)
    )
    payload, headers = _signed_payment_intent_webhook(
        "payment_intent.succeeded",
        "succeeded",
        secret,
        "POS",
        payment_intent_id=payment.provider_payment_id,
        order_id=webhook_order_id,
        order_number=order.order_number,
        amount=webhook_amount,
        currency=payment.currency.lower(),
    )
    if failure_case == "invalid_signature":
        headers["Stripe-Signature"] = "t=1,v1=invalid"

    test_db_session.commit()
    response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )

    assert response.status_code == expected_status
    test_db_session.refresh(order)
    test_db_session.refresh(payment)
    test_db_session.refresh(reservation)
    test_db_session.refresh(unit)
    assert order.order_status == "PENDING_PAYMENT"
    assert order.payment_status == "UNPAID"
    assert payment.status == "PENDING"
    assert reservation.status == "ACTIVE"
    assert unit.status == "RESERVED"
    assert (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .count()
        == 0
    )
    assert (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "SALE",
        )
        .count()
        == 0
    )
    assert (
        test_db_session.query(StripeWebhookEvent)
        .filter(StripeWebhookEvent.stripe_event_id == json.loads(payload)["id"])
        .count()
        == 0
    )


def test_signed_non_pos_payment_intent_webhook_is_not_sent_to_pos_processor(
    api_client,
    monkeypatch,
):
    secret = "whsec_pos_route_test"
    monkeypatch.setattr(
        payments_service.settings,
        "stripe_webhook_secret",
        secret,
    )
    processor = Mock()
    monkeypatch.setattr(
        main_module,
        "process_pos_payment_intent_event",
        processor,
    )
    payload, headers = _signed_payment_intent_webhook(
        "payment_intent.succeeded",
        "succeeded",
        secret,
        "ECOM",
    )

    response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}
    processor.assert_not_called()


def test_staff_pos_checkout_route_requires_staff_authentication(
    api_client,
):
    response = api_client.post(
        "/api/v1/staff/pos/checkout",
        headers={"Idempotency-Key": "pos-route-auth-key-0001"},
        json={
            "lines": [{"variant_id": str(uuid4()), "quantity": 1}],
            "payment_method": "CASH",
        },
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "line_overrides",
    [
        {"variant_id": "not-a-uuid"},
        {"quantity": 0},
        {"quantity": -1},
    ],
    ids=["malformed-variant-uuid", "zero-quantity", "negative-quantity"],
)
def test_staff_pos_checkout_route_rejects_invalid_line_values_without_side_effects(
    api_client,
    test_db_session: Session,
    line_overrides: dict[str, str | int],
):
    staff, variant, store_units, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    pos_order_count_before = (
        test_db_session.query(Order).filter(Order.channel == "POS").count()
    )
    payment_count_before = test_db_session.query(Payment).count()
    reservation_count_before = test_db_session.query(Reservation).count()
    movement_count_before = test_db_session.query(InventoryMovement).count()

    line_payload: dict[str, str | int] = {
        "variant_id": str(variant.id),
        "quantity": 1,
    }
    line_payload.update(line_overrides)

    response = api_client.post(
        "/api/v1/staff/pos/checkout",
        headers=_staff_authorization_headers(
            staff,
            f"pos-invalid-line-{uuid4().hex}",
        ),
        json={
            "lines": [line_payload],
            "payment_method": "CASH",
        },
    )

    assert response.status_code == 422
    assert (
        test_db_session.query(Order).filter(Order.channel == "POS").count()
        == pos_order_count_before
    )
    assert test_db_session.query(Payment).count() == payment_count_before
    assert test_db_session.query(Reservation).count() == reservation_count_before
    assert test_db_session.query(InventoryMovement).count() == movement_count_before
    assert store_units[0].status == "IN_STOCK"


def test_staff_pos_checkout_route_rejects_client_supplied_catalog_price(
    api_client,
    test_db_session: Session,
):
    staff, variant, store_units, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    pos_order_count_before = (
        test_db_session.query(Order).filter(Order.channel == "POS").count()
    )
    reservation_count_before = test_db_session.query(Reservation).count()
    movement_count_before = test_db_session.query(InventoryMovement).count()

    response = api_client.post(
        "/api/v1/staff/pos/checkout",
        headers=_staff_authorization_headers(
            staff,
            "pos-no-client-price-key-0001",
        ),
        json={
            "lines": [
                {
                    "variant_id": str(variant.id),
                    "quantity": 1,
                    "retail_price": "0.01",
                }
            ],
            "payment_method": "CASH",
        },
    )

    assert response.status_code == 422
    assert (
        test_db_session.query(Order).filter(Order.channel == "POS").count()
        == pos_order_count_before
    )
    assert test_db_session.query(Reservation).count() == reservation_count_before
    assert test_db_session.query(InventoryMovement).count() == movement_count_before
    test_db_session.refresh(variant)
    assert variant.retail_price == Decimal("189.00")
    test_db_session.refresh(store_units[0])
    assert store_units[0].status == "IN_STOCK"


@pytest.mark.parametrize(
    "role",
    ["SALES_ASSOCIATE", "STORE_MANAGER"],
)
def test_staff_pos_checkout_route_completes_cash_sale(
    api_client,
    test_db_session: Session,
    role: Literal["SALES_ASSOCIATE", "STORE_MANAGER"],
):
    staff, variant, store_units, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    staff.role = role
    test_db_session.flush()

    response = api_client.post(
        "/api/v1/staff/pos/checkout",
        headers=_staff_authorization_headers(
            staff,
            f"pos-route-cash-{role.lower()}-key-0001",
        ),
        json={
            "lines": [{"variant_id": str(variant.id), "quantity": 1}],
            "payment_method": "CASH",
        },
    )

    assert response.status_code == 201
    response_body = response.json()
    assert response_body["payment_method"] == "CASH"
    assert response_body["order_status"] == "COMPLETED"
    assert response_body["payment_status"] == "PAID"

    created_order = (
        test_db_session.query(Order)
        .filter(Order.order_number == response_body["order_number"])
        .one()
    )
    payment = (
        test_db_session.query(Payment)
        .filter(Payment.order_id == created_order.id)
        .one()
    )
    assert payment.payment_method == "CASH"
    assert payment.provider == "NONE"
    assert payment.status == "PAID"

    sold_unit = test_db_session.get(InventoryUnit, store_units[0].id)
    assert sold_unit is not None
    assert sold_unit.status == "SOLD"


def test_customer_pos_demo_checkout_is_cash_only_idempotent_and_system_attributed(
    api_client,
    test_db_session: Session,
):
    _, variant, store_units, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    idempotency_key = f"pos-demo-checkout-{uuid4().hex}"
    request_body = {
        "lines": [{"variant_id": str(variant.id), "quantity": 1}],
    }

    response = api_client.post(
        "/api/v1/orders/pos-demo",
        headers={"Idempotency-Key": idempotency_key},
        json=request_body,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["payment_method"] == "CASH"
    assert body["order_status"] == "COMPLETED"
    assert body["payment_status"] == "PAID"
    assert "claim_code" not in body

    order = test_db_session.get(Order, UUID(body["order_id"]))
    assert order is not None
    assert order.customer_id is None

    movements = (
        test_db_session.query(InventoryMovement)
        .filter(InventoryMovement.order_id == order.id)
        .order_by(InventoryMovement.movement_type)
        .all()
    )
    assert [movement.movement_type for movement in movements] == ["RESERVATION", "SALE"]
    assert all(movement.actor_type == "SYSTEM" for movement in movements)
    assert all(movement.staff_user_id is None for movement in movements)

    receipt_path = f"/api/v1/orders/pos-demo/{order.id}/receipt"
    first_receipt = api_client.get(receipt_path)
    replay_receipt = api_client.get(receipt_path)
    assert first_receipt.status_code == 200
    assert replay_receipt.status_code == 200
    assert first_receipt.json()["claim_code"] == replay_receipt.json()["claim_code"]

    acknowledgement_path = (
        f"/api/v1/orders/pos-demo/{order.id}/receipt/acknowledgement"
    )
    first_acknowledgement = api_client.post(acknowledgement_path)
    replay_acknowledgement = api_client.post(acknowledgement_path)
    assert first_acknowledgement.status_code == 200
    assert replay_acknowledgement.status_code == 200
    assert replay_acknowledgement.json()["acknowledged"] is True
    assert api_client.get(receipt_path).status_code == 410

    replay_checkout = api_client.post(
        "/api/v1/orders/pos-demo",
        headers={"Idempotency-Key": idempotency_key},
        json=request_body,
    )
    assert replay_checkout.status_code == 201
    assert replay_checkout.json()["order_id"] == str(order.id)
    assert (
        test_db_session.query(Order)
        .filter(Order.idempotency_key_hash == order.idempotency_key_hash)
        .count()
        == 1
    )
    assert (
        test_db_session.query(InventoryMovement)
        .filter(InventoryMovement.order_id == order.id)
        .count()
        == 2
    )
    test_db_session.refresh(store_units[0])
    assert store_units[0].status == "SOLD"


def test_customer_pos_demo_checkout_rejects_staff_only_fields_and_shortage(
    api_client,
    test_db_session: Session,
):
    _, variant, _, _ = _create_pos_test_data(test_db_session)
    valid_headers = {"Idempotency-Key": f"pos-demo-shortage-{uuid4().hex}"}
    valid_lines = [{"variant_id": str(variant.id), "quantity": 1}]
    pos_order_count_before = (
        test_db_session.query(Order).filter(Order.channel == "POS").count()
    )

    shortage_response = api_client.post(
        "/api/v1/orders/pos-demo",
        headers=valid_headers,
        json={"lines": valid_lines},
    )
    assert shortage_response.status_code == 409
    assert shortage_response.json()["detail"] == (
        "No single location can fulfill the complete order"
    )
    assert (
        test_db_session.query(Order)
        .filter(Order.channel == "POS")
        .count()
        == pos_order_count_before
    )

    client_supplied_customer = api_client.post(
        "/api/v1/orders/pos-demo",
        headers={"Idempotency-Key": f"pos-demo-extra-{uuid4().hex}"},
        json={
            "lines": valid_lines,
            "customer_id": str(uuid4()),
            "payment_method": "CARD",
        },
    )
    assert client_supplied_customer.status_code == 422


def test_staff_pos_checkout_route_rejects_changed_request_for_same_idempotency_key(
    api_client,
    test_db_session: Session,
):
    staff, variant, store_units, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    idempotency_key = "pos-route-changed-request-key-0001"
    headers = _staff_authorization_headers(staff, idempotency_key)
    original_payload = {
        "lines": [{"variant_id": str(variant.id), "quantity": 1}],
        "payment_method": "CASH",
    }

    original_response = api_client.post(
        "/api/v1/staff/pos/checkout",
        headers=headers,
        json=original_payload,
    )

    assert original_response.status_code == 201
    order_id = UUID(original_response.json()["order_id"])
    original_order = test_db_session.get(Order, order_id)
    assert original_order is not None
    original_paid_at = original_order.paid_at
    assert original_paid_at is not None

    counts_before_retry = (
        test_db_session.query(Order).filter(Order.channel == "POS").count(),
        test_db_session.query(Payment).count(),
        test_db_session.query(Reservation).count(),
        test_db_session.query(InventoryMovement).count(),
        test_db_session.query(OrderLineUnit).count(),
        test_db_session.query(PosOrderClaim).count(),
        test_db_session.query(PosReceiptDelivery).count(),
    )

    changed_payload = {
        "lines": [{"variant_id": str(variant.id), "quantity": 1}],
        "payment_method": "CARD",
    }
    changed_request_response = api_client.post(
        "/api/v1/staff/pos/checkout",
        headers=headers,
        json=changed_payload,
    )

    assert changed_request_response.status_code == 409
    assert changed_request_response.json()["detail"] == (
        "Idempotency-Key was already used with a different request"
    )
    assert (
        test_db_session.query(Order).filter(Order.channel == "POS").count(),
        test_db_session.query(Payment).count(),
        test_db_session.query(Reservation).count(),
        test_db_session.query(InventoryMovement).count(),
        test_db_session.query(OrderLineUnit).count(),
        test_db_session.query(PosOrderClaim).count(),
        test_db_session.query(PosReceiptDelivery).count(),
    ) == counts_before_retry

    persisted_order = test_db_session.get(Order, order_id)
    assert persisted_order is not None
    assert persisted_order.order_status == "COMPLETED"
    assert persisted_order.payment_status == "PAID"
    assert persisted_order.paid_at == original_paid_at

    reservation = (
        test_db_session.query(Reservation)
        .filter(Reservation.order_id == order_id)
        .one()
    )
    assert reservation.status == "COMMITTED"

    sold_unit = test_db_session.get(InventoryUnit, store_units[0].id)
    assert sold_unit is not None
    assert sold_unit.status == "SOLD"
    assert sold_unit.location_id is None

    order_movements = (
        test_db_session.query(InventoryMovement)
        .filter(InventoryMovement.order_id == order_id)
        .all()
    )
    assert len(order_movements) == 2
    assert {movement.movement_type for movement in order_movements} == {
        "RESERVATION",
        "SALE",
    }


def test_staff_pos_checkout_route_starts_card_payment(
    api_client,
    test_db_session: Session,
    monkeypatch,
):
    staff, variant, _, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    stripe_client = Mock()

    def create_payment_intent(params, options):
        return SimpleNamespace(
            id=f"pi_pos_route_{uuid4().hex}",
            client_secret="pi_pos_route_client_secret",
            amount=params["amount"],
            currency=params["currency"],
            status="requires_payment_method",
            metadata=SimpleNamespace(to_dict=lambda: params["metadata"]),
        )

    stripe_client.v1.payment_intents.create.side_effect = create_payment_intent
    monkeypatch.setattr(
        pos_service,
        "get_stripe_client",
        lambda: stripe_client,
    )

    response = api_client.post(
        "/api/v1/staff/pos/checkout",
        headers=_staff_authorization_headers(
            staff,
            "pos-route-card-key-0001",
        ),
        json={
            "lines": [{"variant_id": str(variant.id), "quantity": 1}],
            "payment_method": "CARD",
        },
    )

    assert response.status_code == 201
    response_body = response.json()
    assert response_body["payment_method"] == "CARD"
    assert response_body["order_status"] == "PENDING_PAYMENT"
    assert response_body["payment_status"] == "UNPAID"
    assert response_body["payment_intent_client_secret"] == (
        "pi_pos_route_client_secret"
    )

    created_order = (
        test_db_session.query(Order)
        .filter(Order.order_number == response_body["order_number"])
        .one()
    )
    payment = (
        test_db_session.query(Payment)
        .filter(Payment.order_id == created_order.id)
        .one()
    )
    assert payment.payment_method == "CARD"
    assert payment.provider == "STRIPE"
    assert payment.status == "PENDING"
    stripe_client.v1.payment_intents.create.assert_called_once()


def test_expired_pos_receipt_cleanup_deletes_delivery_copy(
    test_db_session: Session,
):
    staff, variant, _, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    order = create_pending_pos_order(
        test_db_session,
        _checkout_request(variant.id),
        staff.id,
        "pos-receipt-cleanup-test-key-001",
    )
    complete_pos_cash_sale(test_db_session, order.id, staff.id)

    delivery = (
        test_db_session.query(PosReceiptDelivery)
        .filter(PosReceiptDelivery.order_id == order.id)
        .one()
    )
    delivery.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    test_db_session.flush()

    deleted_count = delete_expired_pos_receipt_deliveries(test_db_session)

    assert deleted_count == 1
    assert (
        test_db_session.query(PosReceiptDelivery)
        .filter(PosReceiptDelivery.order_id == order.id)
        .count()
        == 0
    )

    claim = (
        test_db_session.query(PosOrderClaim)
        .filter(PosOrderClaim.order_id == order.id)
        .one()
    )
    assert claim.receipt_delivery_acknowledged_at is None


def test_pos_receipt_delivery_respects_inclusive_deadline_and_expiry(
    api_client,
    test_db_session: Session,
    monkeypatch,
):
    staff, variant, _, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    order = create_pending_pos_order(
        test_db_session,
        _checkout_request(variant.id),
        staff.id,
        f"pos-receipt-deadline-{uuid4().hex}",
    )
    complete_pos_cash_sale(test_db_session, order.id, staff.id)

    claim = (
        test_db_session.query(PosOrderClaim)
        .filter(PosOrderClaim.order_id == order.id)
        .one()
    )
    delivery = (
        test_db_session.query(PosReceiptDelivery)
        .filter(PosReceiptDelivery.order_id == order.id)
        .one()
    )
    paid_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    claim_deadline = utc_deadline_after_14_days(paid_at)
    order.paid_at = paid_at
    delivery.expires_at = claim_deadline
    test_db_session.flush()

    current_time = claim_deadline

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return current_time.replace(tzinfo=None)
            return current_time

    monkeypatch.setattr(pos_service, "datetime", FrozenDateTime)
    headers = _staff_authorization_headers(
        staff,
        f"pos-receipt-deadline-{uuid4().hex}",
    )
    receipt_path = f"/api/v1/staff/pos/orders/{order.id}/receipt"

    inclusive_receipt = api_client.get(receipt_path, headers=headers)

    assert inclusive_receipt.status_code == 200
    assert (
        datetime.fromisoformat(inclusive_receipt.json()["claim_expires_at"])
        == claim_deadline
    )
    assert (
        delete_expired_pos_receipt_deliveries(
            test_db_session,
            now=claim_deadline,
        )
        == 0
    )

    current_time = claim_deadline + timedelta(microseconds=1)
    expired_receipt = api_client.get(receipt_path, headers=headers)
    assert expired_receipt.status_code == 410
    assert expired_receipt.json()["detail"] == "Receipt delivery has expired"
    assert (
        delete_expired_pos_receipt_deliveries(
            test_db_session,
            now=current_time,
        )
        == 1
    )
    assert (
        test_db_session.query(PosReceiptDelivery)
        .filter(PosReceiptDelivery.order_id == order.id)
        .count()
        == 0
    )
    assert (
        test_db_session.query(PosOrderClaim)
        .filter(
            PosOrderClaim.order_id == order.id,
            PosOrderClaim.receipt_delivery_acknowledged_at.is_(None),
        )
        .count()
        == 1
    )


def test_staff_pos_receipt_acknowledgement_is_idempotent(
    api_client,
    test_db_session: Session,
):
    staff, variant, _, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    headers = _staff_authorization_headers(
        staff,
        "pos-receipt-ack-test-key-001",
    )

    checkout = api_client.post(
        "/api/v1/staff/pos/checkout",
        headers=headers,
        json={
            "lines": [{"variant_id": str(variant.id), "quantity": 1}],
            "payment_method": "CASH",
        },
    )
    assert checkout.status_code == 201
    checkout_body = checkout.json()
    assert checkout_body["order_status"] == "COMPLETED"
    assert checkout_body["payment_status"] == "PAID"
    order_id = checkout_body["order_id"]

    receipt_path = f"/api/v1/staff/pos/orders/{order_id}/receipt"
    acknowledgement_path = (
        f"/api/v1/staff/pos/orders/{order_id}/receipt/acknowledgement"
    )

    receipt = api_client.get(receipt_path, headers=headers)
    assert receipt.status_code == 200
    receipt_body = receipt.json()
    assert receipt_body["order_number"] == checkout_body["order_number"]
    assert re.fullmatch(r"HM-\d{8}", receipt_body["claim_code"])

    acknowledgement = api_client.post(acknowledgement_path, headers=headers)
    assert acknowledgement.status_code == 200
    first_body = acknowledgement.json()
    assert first_body["acknowledged"] is True

    assert (
        test_db_session.query(PosReceiptDelivery)
        .filter(PosReceiptDelivery.order_id == order_id)
        .count()
        == 0
    )

    retry = api_client.post(acknowledgement_path, headers=headers)
    assert retry.status_code == 200
    retry_body = retry.json()
    assert retry_body["acknowledged"] is True
    assert datetime.fromisoformat(
        retry_body["acknowledged_at"]
    ) == datetime.fromisoformat(first_body["acknowledged_at"])

    unavailable_receipt = api_client.get(receipt_path, headers=headers)
    assert unavailable_receipt.status_code == 410
    assert unavailable_receipt.json()["detail"] == (
        "Receipt delivery has already been acknowledged"
    )


def test_verified_customer_claims_pos_order_once(
    api_client,
    test_db_session: Session,
):
    order, claim_code, staff, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )
    customer = _create_pos_claim_customer(test_db_session)
    customer_headers = _customer_authorization_headers(customer)

    response = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=customer_headers,
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["claimed"] is True
    assert body["order_id"] == str(order.id)
    assert body["order_number"] == order.order_number
    assert set(body) == {"claimed", "order_id", "order_number", "claimed_at"}
    assert order.customer_id == customer.id

    claim = (
        test_db_session.query(PosOrderClaim)
        .filter(PosOrderClaim.order_id == order.id)
        .one()
    )
    assert claim.claimed_at is not None
    claim_event = (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "CUSTOMER_POS_ORDER_CLAIM",
            AuditEvent.target_type == "ORDER",
            AuditEvent.target_id == order.id,
        )
        .one()
    )
    assert claim_event.actor_type == "CUSTOMER"
    assert claim_event.customer_id == customer.id
    assert claim_event.outcome == "SUCCEEDED"
    assert claim_event.details is None

    replay = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=customer_headers,
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )
    assert replay.status_code == 409
    assert order.customer_id == customer.id


def test_customer_order_details_and_staff_pos_lookup_return_serialized_items(
    api_client,
    test_db_session: Session,
):
    order, claim_code, staff, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
        quantity=2,
    )
    customer = _create_pos_claim_customer(test_db_session)
    customer_headers = _customer_authorization_headers(customer)

    claim_response = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=customer_headers,
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )
    assert claim_response.status_code == 200

    customer_details = api_client.get(
        f"/api/v1/orders/{order.id}",
        headers=customer_headers,
    )
    assert customer_details.status_code == 200
    customer_body = customer_details.json()
    assert customer_body["order_id"] == str(order.id)
    assert customer_body["order_number"] == order.order_number
    assert customer_body["channel"] == "POS"
    assert customer_body["total_gross"] == "378.00"
    assert len(customer_body["lines"]) == 1
    order_line = customer_body["lines"][0]
    assert order_line["product_name"]
    order_line_record = (
        test_db_session.query(OrderLine)
        .filter(OrderLine.order_id == order.id)
        .one()
    )
    variant_record = test_db_session.get(
        ProductVariant,
        order_line_record.variant_id,
    )
    product_record = test_db_session.get(Product, variant_record.product_id)
    assert order_line["image_url"] == (
        variant_record.image_url or product_record.image_url
    )
    assert order_line["quantity"] == 2
    assert order_line["unit_price_gross"] == "189.00"
    assert order_line["total_gross"] == "378.00"
    assert len(order_line["units"]) == 2
    assert all(not unit["already_returned"] for unit in order_line["units"])
    assert "claim_code" not in customer_body

    other_customer = _create_pos_claim_customer(test_db_session)
    unauthorized_details = api_client.get(
        f"/api/v1/orders/{order.id}",
        headers=_customer_authorization_headers(other_customer),
    )
    assert unauthorized_details.status_code == 404

    staff_headers = _staff_authorization_headers(
        staff,
        f"pos-order-lookup-{uuid4().hex}",
    )
    staff_details = api_client.get(
        "/api/v1/staff/pos/orders/lookup",
        headers=staff_headers,
        params={"order_number": order.order_number},
    )
    assert staff_details.status_code == 200
    assert staff_details.json() == customer_body


def test_customer_order_history_contains_only_owned_ecom_orders(
    api_client,
    test_db_session: Session,
):
    customer = _create_pos_claim_customer(test_db_session)
    other_customer = _create_pos_claim_customer(test_db_session)
    current_time = datetime.now(timezone.utc)

    def create_order(
        *,
        order_number: str,
        owner: Customer,
        channel: Literal["ECOM", "POS"],
        created_at: datetime,
    ) -> Order:
        total_gross = Decimal("100.00")
        order = Order(
            order_number=order_number,
            customer_id=owner.id,
            channel=channel,
            fulfillment_method="SHIPPING" if channel == "ECOM" else None,
            order_status="DELIVERED" if channel == "ECOM" else "COMPLETED",
            payment_status="PAID",
            currency="EUR",
            subtotal_gross=total_gross,
            discount_total_gross=Decimal("0.00"),
            taxable_gross=total_gross,
            net_total=Decimal("81.97"),
            vat_total=Decimal("18.03"),
            total_gross=total_gross,
            standard_vat_rate=Decimal("22.00"),
            applied_vat_rate=Decimal("22.00"),
            created_at=created_at,
        )
        test_db_session.add(order)
        test_db_session.flush()
        return order

    older_order = create_order(
        order_number=f"HM-HISTORY-{uuid4().hex[:12]}",
        owner=customer,
        channel="ECOM",
        created_at=current_time - timedelta(days=1),
    )
    newer_order = create_order(
        order_number=f"HM-HISTORY-{uuid4().hex[:12]}",
        owner=customer,
        channel="ECOM",
        created_at=current_time,
    )
    create_order(
        order_number=f"HM-HISTORY-{uuid4().hex[:12]}",
        owner=customer,
        channel="POS",
        created_at=current_time + timedelta(seconds=1),
    )
    create_order(
        order_number=f"HM-HISTORY-{uuid4().hex[:12]}",
        owner=other_customer,
        channel="ECOM",
        created_at=current_time + timedelta(seconds=2),
    )

    unauthorized_response = api_client.get("/api/v1/orders")
    assert unauthorized_response.status_code == 401

    response = api_client.get(
        "/api/v1/orders",
        headers=_customer_authorization_headers(customer),
    )

    assert response.status_code == 200
    summaries = response.json()
    assert [summary["order_id"] for summary in summaries] == [
        str(newer_order.id),
        str(older_order.id),
    ]
    assert set(summaries[0]) == {
        "order_id",
        "order_number",
        "order_status",
        "payment_status",
        "currency",
        "total_gross",
        "created_at",
    }
    assert {
        field_name: field_value
        for field_name, field_value in summaries[0].items()
        if field_name != "created_at"
    } == {
        "order_id": str(newer_order.id),
        "order_number": newer_order.order_number,
        "order_status": "DELIVERED",
        "payment_status": "PAID",
        "currency": "EUR",
        "total_gross": "100.00",
    }
    assert (
        datetime.fromisoformat(summaries[0]["created_at"].replace("Z", "+00:00"))
        == current_time
    )
    assert "lines" not in summaries[0]


def test_customer_pos_order_history_contains_only_owned_pos_orders(
    api_client,
    test_db_session: Session,
):
    customer_order, customer_claim_code, _, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )
    other_order, other_claim_code, _, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )
    customer = _create_pos_claim_customer(test_db_session)
    other_customer = _create_pos_claim_customer(test_db_session)
    customer_headers = _customer_authorization_headers(customer)
    other_customer_headers = _customer_authorization_headers(other_customer)

    assert api_client.get("/api/v1/orders/pos").status_code == 401

    for order, code, headers in (
        (customer_order, customer_claim_code, customer_headers),
        (other_order, other_claim_code, other_customer_headers),
    ):
        claim_response = api_client.post(
            "/api/v1/orders/pos-claim",
            headers=headers,
            json={"order_number": order.order_number, "claim_code": code},
        )
        assert claim_response.status_code == 200

    customer_response = api_client.get(
        "/api/v1/orders/pos",
        headers=customer_headers,
    )
    other_response = api_client.get(
        "/api/v1/orders/pos",
        headers=other_customer_headers,
    )
    assert customer_response.status_code == 200
    assert other_response.status_code == 200
    assert [item["order_id"] for item in customer_response.json()] == [
        str(customer_order.id)
    ]
    assert [item["order_id"] for item in other_response.json()] == [
        str(other_order.id)
    ]


@pytest.mark.parametrize("reset_by_order_number", [False, True])
def test_pos_claim_failed_attempts_lock_and_manager_reset(
    api_client,
    test_db_session: Session,
    reset_by_order_number: bool,
):
    order, claim_code, staff, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )
    customer = _create_pos_claim_customer(test_db_session)
    customer_headers = _customer_authorization_headers(customer)

    wrong_code = "HM-00000000" if claim_code != "HM-00000000" else "HM-00000001"
    claim = (
        test_db_session.query(PosOrderClaim)
        .filter(PosOrderClaim.order_id == order.id)
        .one()
    )
    original_claim_code_hash = claim.claim_code_hash

    for attempt in range(1, 6):
        response = api_client.post(
            "/api/v1/orders/pos-claim",
            headers=customer_headers,
            json={
                "order_number": order.order_number,
                "claim_code": wrong_code,
            },
        )
        assert response.status_code == 409
        test_db_session.refresh(claim)
        assert claim.failed_attempt_count == attempt
        lockout_event_count = (
            test_db_session.query(AuditEvent)
            .filter(
                AuditEvent.action_code == "POS_CLAIM_LOCKOUT",
                AuditEvent.target_type == "ORDER",
                AuditEvent.target_id == order.id,
            )
            .count()
        )
        assert lockout_event_count == (1 if attempt == 5 else 0)

    assert "five-attempt limit" in response.json()["detail"]
    assert claim.locked_until is not None

    correct_code_while_locked = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=customer_headers,
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )
    assert correct_code_while_locked.status_code == 409
    assert order.customer_id is None

    reset_path = (
        "/api/v1/staff/pos/orders/claim/reset"
        if reset_by_order_number
        else f"/api/v1/staff/pos/orders/{order.id}/claim/reset"
    )

    def reset_claim(headers: dict[str, str]):
        if reset_by_order_number:
            return api_client.post(
                reset_path,
                headers=headers,
                json={"order_number": order.order_number},
            )
        return api_client.post(reset_path, headers=headers)

    receipt_path = f"/api/v1/staff/pos/orders/{order.id}/receipt"
    associate_headers = _staff_authorization_headers(
        staff,
        f"pos-claim-associate-{uuid4().hex}",
    )
    acknowledgement = api_client.post(
        f"{receipt_path}/acknowledgement",
        headers=associate_headers,
    )
    assert acknowledgement.status_code == 200
    assert (
        test_db_session.query(PosReceiptDelivery)
        .filter(PosReceiptDelivery.order_id == order.id)
        .count()
        == 0
    )
    unavailable_receipt = api_client.get(receipt_path, headers=associate_headers)
    assert unavailable_receipt.status_code == 410

    associate_reset = reset_claim(associate_headers)
    assert associate_reset.status_code == 403

    staff.role = "STORE_MANAGER"
    test_db_session.flush()
    manager_headers = _staff_authorization_headers(
        staff,
        f"pos-claim-manager-{uuid4().hex}",
    )

    reset = reset_claim(manager_headers)
    assert reset.status_code == 200
    assert reset.json()["reset"] is True
    assert reset.json()["failed_attempt_count"] == 0
    assert set(reset.json()) == {"reset", "failed_attempt_count", "reset_at"}

    test_db_session.refresh(claim)
    assert claim.failed_attempt_count == 0
    assert claim.locked_until is None
    assert claim.last_reset_at is not None
    assert claim.last_reset_by_staff_id == staff.id
    assert claim.claimed_at is None
    assert claim.receipt_delivery_acknowledged_at is not None
    assert claim.claim_code_hash == original_claim_code_hash
    assert (
        test_db_session.query(PosReceiptDelivery)
        .filter(PosReceiptDelivery.order_id == order.id)
        .count()
        == 0
    )
    unavailable_receipt_after_reset = api_client.get(
        receipt_path,
        headers=manager_headers,
    )
    assert unavailable_receipt_after_reset.status_code == 410
    reset_event = (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "STAFF_POS_CLAIM_RESET",
            AuditEvent.target_type == "ORDER",
            AuditEvent.target_id == order.id,
        )
        .one()
    )
    assert reset_event.actor_type == "STAFF"
    assert reset_event.staff_user_id == staff.id
    assert reset_event.outcome == "SUCCEEDED"
    assert reset_event.details == {"failed_attempt_count": 5}

    successful_claim = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=customer_headers,
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )
    assert successful_claim.status_code == 200
    assert order.customer_id == customer.id
    assert (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "CUSTOMER_POS_ORDER_CLAIM",
            AuditEvent.target_type == "ORDER",
            AuditEvent.target_id == order.id,
        )
        .count()
        == 1
    )


def test_pos_claim_lock_expires_after_thirty_seconds(
    api_client,
    test_db_session: Session,
    monkeypatch,
):
    order, claim_code, _, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )
    customer = _create_pos_claim_customer(test_db_session)
    customer_headers = _customer_authorization_headers(customer)
    wrong_code = "HM-00000000" if claim_code != "HM-00000000" else "HM-00000001"
    now = datetime.now(timezone.utc)

    class ControlledDateTime(datetime):
        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return now

    monkeypatch.setattr(pos_service, "datetime", ControlledDateTime)

    for _ in range(5):
        response = api_client.post(
            "/api/v1/orders/pos-claim",
            headers=customer_headers,
            json={
                "order_number": order.order_number,
                "claim_code": wrong_code,
            },
        )
        assert response.status_code == 409

    claim = (
        test_db_session.query(PosOrderClaim)
        .filter(PosOrderClaim.order_id == order.id)
        .one()
    )
    assert claim.locked_until == now + timedelta(seconds=30)

    now = now + timedelta(seconds=29)
    still_locked = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=customer_headers,
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )
    assert still_locked.status_code == 409
    test_db_session.refresh(claim)
    assert claim.failed_attempt_count == 5

    now = now + timedelta(seconds=1)
    claim_after_cooldown = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=customer_headers,
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )
    assert claim_after_cooldown.status_code == 200
    test_db_session.refresh(claim)
    assert claim.failed_attempt_count == 0
    assert claim.locked_until is None
    assert order.customer_id == customer.id


def test_pos_claim_requires_verified_customer_and_respects_expiry(
    api_client,
    test_db_session: Session,
):
    order, claim_code, _, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )
    unverified_customer = _create_pos_claim_customer(
        test_db_session,
        verified=False,
    )

    unverified_response = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=_customer_authorization_headers(unverified_customer),
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )
    assert unverified_response.status_code == 403
    assert order.customer_id is None

    claim = (
        test_db_session.query(PosOrderClaim)
        .filter(PosOrderClaim.order_id == order.id)
        .one()
    )
    assert claim.failed_attempt_count == 0

    order.paid_at = datetime.now(timezone.utc) - timedelta(
        days=14,
        seconds=1,
    )
    test_db_session.flush()

    verified_customer = _create_pos_claim_customer(test_db_session)
    expired_response = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=_customer_authorization_headers(verified_customer),
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )
    assert expired_response.status_code == 410
    assert order.customer_id is None
    assert claim.failed_attempt_count == 0


def test_staff_can_poll_pending_pos_card_order_status(
    api_client,
    test_db_session: Session,
):
    staff, variant, _, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    order = create_pending_pos_order(
        test_db_session,
        _checkout_request(variant.id, payment_method="CARD"),
        staff.id,
        f"pos-status-test-{uuid4().hex}",
    )
    payment = Payment(
        order_id=order.id,
        payment_method="CARD",
        provider="STRIPE",
        provider_payment_id=f"pi_pos_status_{uuid4().hex}",
        amount=order.total_gross,
        currency=order.currency,
        status="PENDING",
    )
    test_db_session.add(payment)
    test_db_session.flush()

    headers = _staff_authorization_headers(
        staff,
        f"pos-status-poll-{uuid4().hex}",
    )
    response = api_client.get(
        f"/api/v1/staff/pos/orders/{order.id}/status",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["order_id"] == str(order.id)
    assert body["order_number"] == order.order_number
    assert body["payment_method"] == "CARD"
    assert body["order_status"] == "PENDING_PAYMENT"
    assert body["payment_status"] == "UNPAID"
    assert Decimal(str(body["total_gross"])) == order.total_gross
    assert body["currency"] == order.currency

    reservation = (
        test_db_session.query(Reservation)
        .filter(
            Reservation.order_id == order.id,
            Reservation.status == "ACTIVE",
        )
        .one()
    )
    assert (
        datetime.fromisoformat(body["reservation_expires_at"]) == reservation.expires_at
    )


@pytest.mark.parametrize(
    "read_surface",
    ["pos_status", "catalog_detail", "variant_stock"],
)
def test_inventory_read_commits_expired_reservation_cleanup(
    api_client,
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    read_surface: Literal[
        "pos_status",
        "catalog_detail",
        "variant_stock",
    ],
):
    staff, variant, _, _ = _create_pos_test_data(
        test_db_session,
        store_quantity=1,
    )
    order = create_pending_pos_order(
        test_db_session,
        _checkout_request(variant.id, payment_method="CARD"),
        staff.id,
        f"pos-expired-cleanup-{read_surface}-{uuid4().hex}",
    )
    test_db_session.add(
        Payment(
            order_id=order.id,
            payment_method="CARD",
            provider="STRIPE",
            provider_payment_id=f"pi_pos_expired_{uuid4().hex}",
            amount=order.total_gross,
            currency=order.currency,
            status="PENDING",
        )
    )

    test_db_session.flush()

    reservation = (
        test_db_session.query(Reservation)
        .filter(
            Reservation.order_id == order.id,
            Reservation.status == "ACTIVE",
        )
        .one()
    )
    unit = test_db_session.get(InventoryUnit, reservation.inventory_unit_id)
    assert unit is not None

    reservation.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    test_db_session.flush()

    in_stock_before_read = (
        test_db_session.query(InventoryUnit)
        .filter(InventoryUnit.status == "IN_STOCK")
        .count()
    )

    real_commit = test_db_session.commit
    commit_calls = 0

    def track_commit():
        nonlocal commit_calls
        commit_calls += 1
        real_commit()

    monkeypatch.setattr(test_db_session, "commit", track_commit)

    headers = _staff_authorization_headers(
        staff,
        f"pos-expired-cleanup-read-{uuid4().hex}",
    )

    if read_surface == "pos_status":
        response = api_client.get(
            f"/api/v1/staff/pos/orders/{order.id}/status",
            headers=headers,
        )
    elif read_surface == "catalog_detail":
        response = api_client.get(
            f"/api/v1/products/{variant.product_id}/detail",
        )
    elif read_surface == "variant_stock":
        response = api_client.get(
            f"/api/v1/products/{variant.product_id}/variants/{variant.id}/stock",
        )

    assert response.status_code == 200
    assert commit_calls == 1

    test_db_session.refresh(order)
    test_db_session.refresh(reservation)
    test_db_session.refresh(unit)

    assert order.order_status == "EXPIRED"
    assert order.payment_status == "UNPAID"
    assert reservation.status == "EXPIRED"
    assert reservation.released_at is not None
    assert unit.status == "IN_STOCK"

    release_movement = (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.inventory_unit_id == unit.id,
            InventoryMovement.movement_type == "RELEASE",
        )
        .one()
    )
    assert release_movement.from_status == "RESERVED"
    assert release_movement.to_status == "IN_STOCK"

    if read_surface == "pos_status":
        body = response.json()
        assert body["order_status"] == "EXPIRED"
        assert body["payment_status"] == "UNPAID"
        assert body["reservation_expires_at"] is None
    elif read_surface == "catalog_detail":
        matching_variant = next(
            item
            for item in response.json()["variants"]
            if item["id"] == str(variant.id)
        )
        assert matching_variant["available_stock"] == 1
    elif read_surface == "variant_stock":
        assert response.json()["available_stock"] == 1
    else:
        assert response.json()["by_status"]["IN_STOCK"] >= in_stock_before_read + 1


def test_pos_claim_is_accepted_at_inclusive_deadline(
    api_client,
    test_db_session: Session,
    monkeypatch,
):
    order, claim_code, _, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )
    customer = _create_pos_claim_customer(test_db_session)

    paid_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    claim_deadline = utc_deadline_after_14_days(paid_at)
    order.paid_at = paid_at
    test_db_session.flush()

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return (
                claim_deadline
                if tz is not None
                else claim_deadline.replace(tzinfo=None)
            )

    monkeypatch.setattr(pos_service, "datetime", FrozenDateTime)

    response = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=_customer_authorization_headers(customer),
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )

    assert response.status_code == 200
    assert response.json()["claimed"] is True
    assert datetime.fromisoformat(response.json()["claimed_at"]) == claim_deadline
    assert order.customer_id == customer.id


@pytest.mark.parametrize("source", ["PHYSICAL_STORE", "MAIN_WAREHOUSE"])
@pytest.mark.parametrize("staff_role", ["SALES_ASSOCIATE", "STORE_MANAGER"])
def test_pos_partial_return_restocks_to_original_sale_source(
    api_client,
    test_db_session: Session,
    source: Literal["PHYSICAL_STORE", "MAIN_WAREHOUSE"],
    staff_role: Literal["SALES_ASSOCIATE", "STORE_MANAGER"],
):
    order, claim_code, staff, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
        quantity=2,
        source=source,
    )
    staff.role = staff_role
    test_db_session.flush()
    original_paid_at = order.paid_at
    assert original_paid_at is not None
    customer = _create_pos_claim_customer(test_db_session)
    customer_headers = _customer_authorization_headers(customer)

    claim_response = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=customer_headers,
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )
    assert claim_response.status_code == 200

    allocations = (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .order_by(OrderLineUnit.id)
        .all()
    )
    assert len(allocations) == 2

    allocation = allocations[0]
    sale_movement = (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.inventory_unit_id == allocation.inventory_unit_id,
            InventoryMovement.movement_type == "SALE",
        )
        .one()
    )
    original_source_id = sale_movement.from_location_id
    assert original_source_id is not None

    staff_headers = _staff_authorization_headers(
        staff,
        f"pos-return-{uuid4().hex}",
    )
    return_response = api_client.post(
        f"/api/v1/staff/pos/orders/{order.id}/returns",
        headers=staff_headers,
        json={
            "reason": "POS partial return at the counter",
            "items": [{"order_line_unit_id": str(allocation.id)}],
        },
    )
    assert return_response.status_code == 200
    return_id = return_response.json()["return_id"]
    assert return_response.json()["returned_item_count"] == 1
    assert return_response.json()["amount"] == "189.00"
    assert return_response.json()["payment_status"] == "PARTIALLY_REFUNDED"
    test_db_session.refresh(order)
    assert order.paid_at == original_paid_at

    returned_unit = test_db_session.get(
        InventoryUnit,
        allocation.inventory_unit_id,
    )
    remaining_unit = test_db_session.get(
        InventoryUnit,
        allocations[1].inventory_unit_id,
    )
    payment = test_db_session.query(Payment).filter(Payment.order_id == order.id).one()
    return_record = test_db_session.get(ReturnRecord, return_id)
    refund = test_db_session.query(Refund).filter(Refund.return_id == return_id).one()
    return_movement = (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.return_id == return_id,
            InventoryMovement.movement_type == "RETURN",
        )
        .one()
    )

    assert returned_unit is not None
    assert returned_unit.status == "IN_STOCK"
    assert returned_unit.location_id == original_source_id
    assert remaining_unit is not None
    assert remaining_unit.status == "SOLD"
    assert remaining_unit.location_id is None
    assert payment.status == "PARTIALLY_REFUNDED"
    assert order.payment_status == "PARTIALLY_REFUNDED"
    assert return_record is not None
    assert return_record.status == "COMPLETED"
    assert refund.amount == Decimal("189.00")
    assert refund.method == "ORIGINAL_PAYMENT"
    assert return_movement.from_status == "SOLD"
    assert return_movement.to_status == "IN_STOCK"
    assert return_movement.to_location_id == original_source_id

    replay = api_client.post(
        f"/api/v1/staff/pos/orders/{order.id}/returns",
        headers=staff_headers,
        json={
            "reason": "Duplicate POS partial return",
            "items": [{"order_line_unit_id": str(allocation.id)}],
        },
    )
    assert replay.status_code == 409
    assert replay.json()["detail"] == "One or more items have already been returned"

    staff_return_event = (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "STAFF_POS_RETURN_PROCESSED",
            AuditEvent.target_type == "RETURN",
            AuditEvent.target_id == UUID(return_id),
        )
        .one()
    )
    assert staff_return_event.actor_type == "STAFF"
    assert staff_return_event.staff_user_id == staff.id
    assert staff_return_event.customer_id is None


def test_customer_pos_return_is_automatic_owned_and_system_attributed(
    api_client,
    test_db_session: Session,
):
    order, claim_code, _, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )
    customer = _create_pos_claim_customer(test_db_session)
    customer_headers = _customer_authorization_headers(customer)
    claim_response = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=customer_headers,
        json={"order_number": order.order_number, "claim_code": claim_code},
    )
    assert claim_response.status_code == 200

    allocation = (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .one()
    )
    request_body = {
        "reason": "Customer demo return",
        "items": [{"order_line_unit_id": str(allocation.id)}],
    }
    unauthorized_response = api_client.post(
        f"/api/v1/orders/pos/{order.id}/returns",
        headers=_customer_authorization_headers(
            _create_pos_claim_customer(test_db_session)
        ),
        json=request_body,
    )
    assert unauthorized_response.status_code == 404

    response = api_client.post(
        f"/api/v1/orders/pos/{order.id}/returns",
        headers=customer_headers,
        json=request_body,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["returned_item_count"] == 1
    assert body["payment_status"] == "REFUNDED"
    assert body["amount"] == "189.00"

    return_record = test_db_session.get(ReturnRecord, UUID(body["return_id"]))
    assert return_record is not None
    assert return_record.status == "COMPLETED"
    assert (
        test_db_session.query(Refund)
        .filter(Refund.return_id == return_record.id)
        .count()
        == 1
    )
    return_movement = (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.return_id == return_record.id,
            InventoryMovement.movement_type == "RETURN",
        )
        .one()
    )
    assert return_movement.actor_type == "SYSTEM"
    assert return_movement.staff_user_id is None

    customer_request_event = (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "CUSTOMER_POS_RETURN_REQUESTED",
            AuditEvent.target_id == return_record.id,
        )
        .one()
    )
    system_process_event = (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "SYSTEM_POS_RETURN_PROCESSED",
            AuditEvent.target_id == return_record.id,
        )
        .one()
    )
    assert customer_request_event.actor_type == "CUSTOMER"
    assert customer_request_event.customer_id == customer.id
    assert system_process_event.actor_type == "SYSTEM"
    assert system_process_event.customer_id is None
    assert system_process_event.staff_user_id is None

    return_history = api_client.get(
        f"/api/v1/orders/{order.id}/returns",
        headers=customer_headers,
    )
    assert return_history.status_code == 200
    assert len(return_history.json()) == 1
    assert return_history.json()[0]["status"] == "COMPLETED"
    assert return_history.json()[0]["refunds"][0]["amount"] == "189.00"

    replay = api_client.post(
        f"/api/v1/orders/pos/{order.id}/returns",
        headers=customer_headers,
        json=request_body,
    )
    assert replay.status_code == 409
    assert replay.json()["detail"] == "One or more items have already been returned"
    assert (
        test_db_session.query(Refund)
        .filter(Refund.return_id == return_record.id)
        .count()
        == 1
    )


def test_staff_pos_return_integrity_failure_rolls_back_and_audits(
    api_client,
    test_db_session: Session,
):
    order, claim_code, staff, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )
    customer = _create_pos_claim_customer(test_db_session)
    claim_response = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=_customer_authorization_headers(customer),
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )
    assert claim_response.status_code == 200

    allocation = (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .one()
    )
    payment = test_db_session.query(Payment).filter(Payment.order_id == order.id).one()
    payment.amount = Decimal("1.00")
    test_db_session.flush()

    staff_headers = _staff_authorization_headers(
        staff,
        f"pos-return-integrity-{uuid4().hex}",
    )
    response = api_client.post(
        f"/api/v1/staff/pos/orders/{order.id}/returns",
        headers=staff_headers,
        json={
            "reason": "Integrity rollback test",
            "items": [{"order_line_unit_id": str(allocation.id)}],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Refund exceeds the original payment"
    assert (
        test_db_session.query(ReturnRecord)
        .filter(ReturnRecord.order_id == order.id)
        .count()
        == 0
    )
    inventory_unit = test_db_session.get(
        InventoryUnit,
        allocation.inventory_unit_id,
    )
    assert inventory_unit is not None
    assert inventory_unit.status == "SOLD"
    assert (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "RETURN",
        )
        .count()
        == 0
    )
    failure_event = (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "RETURN_PROCESSING_INTEGRITY_FAILURE",
            AuditEvent.outcome == "FAILED",
            AuditEvent.target_type == "ORDER",
            AuditEvent.target_id == order.id,
        )
        .one()
    )
    assert failure_event.actor_type == "STAFF"
    assert failure_event.staff_user_id == staff.id


def test_legacy_paid_pos_order_without_paid_at_keeps_null_and_rejects_return(
    api_client,
    test_db_session: Session,
):
    order, claim_code, staff, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )
    customer = _create_pos_claim_customer(test_db_session)
    customer_headers = _customer_authorization_headers(customer)

    claim_response = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=customer_headers,
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )
    assert claim_response.status_code == 200

    allocation = (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .one()
    )
    order.paid_at = None
    test_db_session.commit()

    return_count_before = test_db_session.query(ReturnRecord).count()
    movement_count_before = (
        test_db_session.query(InventoryMovement)
        .filter(InventoryMovement.order_id == order.id)
        .count()
    )
    staff_headers = _staff_authorization_headers(
        staff,
        f"pos-return-legacy-{uuid4().hex}",
    )
    response = api_client.post(
        f"/api/v1/staff/pos/orders/{order.id}/returns",
        headers=staff_headers,
        json={
            "reason": "Legacy paid POS order without paid_at",
            "items": [{"order_line_unit_id": str(allocation.id)}],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Paid POS order has no paid_at timestamp"
    test_db_session.refresh(order)
    assert order.order_status == "COMPLETED"
    assert order.payment_status == "PAID"
    assert order.paid_at is None
    assert test_db_session.query(ReturnRecord).count() == return_count_before
    assert (
        test_db_session.query(InventoryMovement)
        .filter(InventoryMovement.order_id == order.id)
        .count()
        == movement_count_before
    )


def test_staff_pos_return_is_inclusive_and_rejects_expired_sales(
    api_client,
    test_db_session: Session,
    monkeypatch,
):
    order, claim_code, staff, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )
    customer = _create_pos_claim_customer(test_db_session)
    customer_headers = _customer_authorization_headers(customer)

    claim_response = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=customer_headers,
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )
    assert claim_response.status_code == 200

    order.paid_at = datetime.now(timezone.utc) - timedelta(days=14)
    test_db_session.flush()
    paid_at = order.paid_at
    assert paid_at is not None
    deadline = utc_deadline_after_14_days(paid_at)

    allocation = (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .one()
    )

    class FrozenDateTime(datetime):
        current_time: datetime

        @classmethod
        def now(cls, tz=None):
            return (
                cls.current_time
                if tz is not None
                else cls.current_time.replace(tzinfo=None)
            )

    FrozenDateTime.current_time = deadline
    monkeypatch.setattr(returns_service, "datetime", FrozenDateTime)

    staff_headers = _staff_authorization_headers(
        staff,
        f"pos-return-boundary-{uuid4().hex}",
    )
    completed_return = api_client.post(
        f"/api/v1/staff/pos/orders/{order.id}/returns",
        headers=staff_headers,
        json={
            "reason": "Return at the inclusive deadline",
            "items": [{"order_line_unit_id": str(allocation.id)}],
        },
    )
    assert completed_return.status_code == 200
    assert completed_return.json()["status"] == "COMPLETED"

    late_order, late_claim_code, late_staff, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )
    late_customer = _create_pos_claim_customer(test_db_session)
    late_customer_headers = _customer_authorization_headers(late_customer)
    late_claim = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=late_customer_headers,
        json={
            "order_number": late_order.order_number,
            "claim_code": late_claim_code,
        },
    )
    assert late_claim.status_code == 200

    late_allocation = (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == late_order.id)
        .one()
    )
    late_order.paid_at = datetime.now(timezone.utc) - timedelta(
        days=14,
        seconds=1,
    )
    test_db_session.flush()
    FrozenDateTime.current_time = datetime.now(timezone.utc)
    late_staff_headers = _staff_authorization_headers(
        late_staff,
        f"pos-return-expired-{uuid4().hex}",
    )
    late_return = api_client.post(
        f"/api/v1/staff/pos/orders/{late_order.id}/returns",
        headers=late_staff_headers,
        json={
            "reason": "Return after the deadline",
            "items": [{"order_line_unit_id": str(late_allocation.id)}],
        },
    )
    assert late_return.status_code == 409
    assert late_return.json()["detail"] == "POS return window has expired"
    assert (
        test_db_session.query(ReturnRecord)
        .filter(ReturnRecord.order_id == late_order.id)
        .count()
        == 0
    )


def test_pos_return_processing_requires_verified_customer(
    api_client,
    test_db_session: Session,
):
    order, _, staff, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )
    customer = _create_pos_claim_customer(test_db_session, verified=False)
    order.customer_id = customer.id
    test_db_session.flush()

    allocation = (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .one()
    )
    staff_headers = _staff_authorization_headers(
        staff,
        f"pos-return-unverified-{uuid4().hex}",
    )
    response = api_client.post(
        f"/api/v1/staff/pos/orders/{order.id}/returns",
        headers=staff_headers,
        json={
            "reason": "Unverified account return test",
            "items": [{"order_line_unit_id": str(allocation.id)}],
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "A verified customer account is required for returns"
    )
    payment = test_db_session.query(Payment).filter(Payment.order_id == order.id).one()
    assert (
        test_db_session.query(Refund).filter(Refund.payment_id == payment.id).count()
        == 0
    )
    assert (
        test_db_session.query(ReturnRecord)
        .filter(ReturnRecord.order_id == order.id)
        .count()
        == 0
    )


def test_unclaimed_anonymous_pos_order_cannot_request_return(
    api_client,
    test_db_session: Session,
):
    order, _, staff, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )

    allocation = (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .one()
    )
    staff_headers = _staff_authorization_headers(
        staff,
        f"pos-return-unclaimed-{uuid4().hex}",
    )
    lookup = api_client.get(
        "/api/v1/staff/pos/orders/lookup",
        headers=staff_headers,
        params={"order_number": order.order_number},
    )
    assert lookup.status_code == 409
    assert lookup.json()["detail"] == (
        "This POS order must be claimed by a customer before staff can "
        "view its details"
    )

    response = api_client.post(
        f"/api/v1/staff/pos/orders/{order.id}/returns",
        headers=staff_headers,
        json={
            "reason": "Unclaimed POS return test",
            "items": [{"order_line_unit_id": str(allocation.id)}],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "The customer must claim this POS order before a return"
    )
    assert (
        test_db_session.query(ReturnRecord)
        .filter(ReturnRecord.order_id == order.id)
        .count()
        == 0
    )


def test_customer_return_endpoint_rejects_pos_orders(
    api_client,
    test_db_session: Session,
):
    order, claim_code, _, _ = _create_claimable_pos_sale(
        api_client,
        test_db_session,
    )
    customer = _create_pos_claim_customer(test_db_session)
    customer_headers = _customer_authorization_headers(customer)
    claim_response = api_client.post(
        "/api/v1/orders/pos-claim",
        headers=customer_headers,
        json={
            "order_number": order.order_number,
            "claim_code": claim_code,
        },
    )
    assert claim_response.status_code == 200

    allocation = (
        test_db_session.query(OrderLineUnit)
        .join(OrderLine, OrderLineUnit.order_line_id == OrderLine.id)
        .filter(OrderLine.order_id == order.id)
        .one()
    )
    response = api_client.post(
        f"/api/v1/orders/{order.id}/returns",
        headers=customer_headers,
        json={
            "reason": "Customer POS return should be staff handled",
            "items": [{"order_line_unit_id": str(allocation.id)}],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "POS returns must be processed by store staff"
    assert (
        test_db_session.query(ReturnRecord)
        .filter(ReturnRecord.order_id == order.id)
        .count()
        == 0
    )


def test_product_list_returns_card_image_and_variant_data(
    api_client,
    test_db_session: Session,
):
    suffix = uuid4().hex
    product = Product(
        name=f"Catalog Test Necklace {suffix}",
        slug=f"catalog-test-necklace-{suffix}",
        description="Catalogue API test product",
        category="NECKLACE",
        image_url="https://example.invalid/catalog-test-necklace.png",
    )
    test_db_session.add(product)
    test_db_session.flush()

    gold_variant = ProductVariant(
        product_id=product.id,
        material="Gold",
        color="Yellow Gold",
        size_system=None,
        size_label="Standard",
        sku=f"CATALOG-G-{suffix}",
        ean=f"{uuid4().int % 10**13:013d}",
        retail_price=Decimal("169.00"),
        image_url="https://example.invalid/catalog-test-necklace-gold.png",
    )
    silver_variant = ProductVariant(
        product_id=product.id,
        material="Silver",
        color="Silver",
        size_system=None,
        size_label="Standard",
        sku=f"CATALOG-S-{suffix}",
        ean=f"{uuid4().int % 10**13:013d}",
        retail_price=Decimal("129.00"),
        image_url="https://example.invalid/catalog-test-necklace-silver.png",
    )
    test_db_session.add_all([gold_variant, silver_variant])
    test_db_session.flush()

    response = api_client.get("/api/v1/products")

    assert response.status_code == 200
    product_payload = next(
        item for item in response.json() if item["slug"] == product.slug
    )
    assert product_payload["id"] == str(product.id)
    assert product_payload["image_url"] == product.image_url

    variants_by_color = {item["color"]: item for item in product_payload["variants"]}
    assert set(variants_by_color) == {"Yellow Gold", "Silver"}
    assert variants_by_color["Yellow Gold"] == {
        "id": str(gold_variant.id),
        "color": "Yellow Gold",
        "size_system": None,
        "size_label": "Standard",
        "retail_price": "169.00",
        "image_url": gold_variant.image_url,
    }
    assert variants_by_color["Silver"] == {
        "id": str(silver_variant.id),
        "color": "Silver",
        "size_system": None,
        "size_label": "Standard",
        "retail_price": "129.00",
        "image_url": silver_variant.image_url,
    }
