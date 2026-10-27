import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from backend.app.core.data_protection import (
    create_email_lookup_hmac,
    decrypt_pii,
    encrypt_pii,
)
from backend.app.models.audit_event import AuditEvent
from backend.app.models.customer import Customer
from backend.app.models.inventory_movement import InventoryMovement
from backend.app.models.inventory_unit import InventoryUnit
from backend.app.models.location import Location
from backend.app.models.notification import Notification
from backend.app.models.order import Order
from backend.app.models.order_address import OrderAddress
from backend.app.models.order_line import OrderLine
from backend.app.models.order_line_unit import OrderLineUnit
from backend.app.models.payment import Payment
from backend.app.models.product import Product
from backend.app.models.product_variant import ProductVariant
from backend.app.models.reservation import Reservation
from backend.app.models.shipment import Shipment
from backend.app.models.stripe_webhook_event import StripeWebhookEvent
from backend.app.services import fulfillment as fulfillment_service
from backend.app.services import payments


def _event_payload() -> tuple[bytes, int]:
    timestamp = int(time.time())
    payload = json.dumps(
        {
            "id": "evt_test_webhook",
            "object": "event",
            "api_version": "2024-06-20",
            "created": timestamp,
            "data": {
                "object": {
                    "id": "cus_test",
                    "object": "customer",
                }
            },
            "livemode": False,
            "pending_webhooks": 1,
            "request": None,
            "type": "customer.created",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return payload, timestamp


def _signature(payload: bytes, secret: str, timestamp: int) -> str:
    signed_payload = str(timestamp).encode("utf-8") + b"." + payload
    digest = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_verify_accepts_valid_signature(monkeypatch):
    secret = "whsec_test_fixture_only"
    monkeypatch.setattr(payments.settings, "stripe_webhook_secret", secret)
    payload, timestamp = _event_payload()

    event = payments.verify_stripe_webhook_event(
        payload,
        _signature(payload, secret, timestamp),
    )

    assert event.id == "evt_test_webhook"
    assert event.type == "customer.created"


def test_verify_rejects_invalid_signature(monkeypatch):
    secret = "whsec_test_fixture_only"
    monkeypatch.setattr(payments.settings, "stripe_webhook_secret", secret)
    payload, timestamp = _event_payload()

    with pytest.raises(ValueError, match="Invalid Stripe webhook"):
        payments.verify_stripe_webhook_event(
            payload,
            f"t={timestamp},v1=invalid",
        )


def test_verify_fails_closed_without_secret(monkeypatch):
    monkeypatch.setattr(payments.settings, "stripe_webhook_secret", None)

    with pytest.raises(RuntimeError, match="not configured"):
        payments.verify_stripe_webhook_event(b"{}", "t=0,v1=invalid")


def test_pos_webhook_service_runs_outside_async_event_loop(
    api_client,
    monkeypatch,
):
    from backend.app import main as main_module

    secret = "whsec_test_fixture_only"
    monkeypatch.setattr(payments.settings, "stripe_webhook_secret", secret)
    observed_worker_context: list[bool] = []

    def record_worker_context(db, event):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            observed_worker_context.append(True)
        else:
            observed_worker_context.append(False)

    monkeypatch.setattr(
        main_module,
        "process_pos_payment_intent_event",
        record_worker_context,
    )
    timestamp = int(time.time())
    payload = json.dumps(
        {
            "id": f"evt_threaded_{uuid4().hex}",
            "object": "event",
            "api_version": "2024-06-20",
            "created": timestamp,
            "data": {
                "object": {
                    "id": f"pi_threaded_{uuid4().hex}",
                    "object": "payment_intent",
                    "status": "succeeded",
                    "amount": 100,
                    "currency": "eur",
                    "metadata": {"channel": "POS"},
                }
            },
            "livemode": False,
            "pending_webhooks": 1,
            "request": None,
            "type": "payment_intent.succeeded",
        },
        separators=(",", ":"),
    ).encode("utf-8")

    response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": _signature(payload, secret, timestamp)},
    )

    assert response.status_code == 200
    assert observed_worker_context == [True]


@pytest.mark.parametrize(
    ("ready_at", "expected_deadline"),
    [
        (
            datetime(2026, 3, 22, 12, tzinfo=timezone.utc),
            datetime(2026, 3, 29, 11, tzinfo=timezone.utc),
        ),
        (
            datetime(2026, 10, 18, 8, tzinfo=timezone.utc),
            datetime(2026, 10, 25, 9, tzinfo=timezone.utc),
        ),
    ],
    ids=["spring-forward", "fall-back"],
)
def test_pickup_deadline_preserves_rome_local_time_across_dst(
    ready_at,
    expected_deadline,
):
    assert fulfillment_service._pickup_deadline_at(ready_at) == expected_deadline


@pytest.mark.parametrize(
    ("fulfillment_method", "expected_order_status"),
    [
        ("SHIPPING", "READY_TO_SHIP"),
        ("STORE_PICKUP", "READY_FOR_PICKUP"),
    ],
    ids=["shipping", "store-pickup"],
)
def test_paid_checkout_webhook_fulfills_once_and_ignores_replay(
    api_client,
    test_db_session,
    monkeypatch,
    fulfillment_method: str,
    expected_order_status: str,
):
    secret = "whsec_test_fixture_only"
    monkeypatch.setattr(payments.settings, "stripe_webhook_secret", secret)
    unique_suffix = uuid4().hex
    customer_email = f"webhook-test-{unique_suffix}@example.com"
    customer = Customer(
        email_ciphertext=encrypt_pii(customer_email),
        email_lookup_hmac=create_email_lookup_hmac(customer_email),
    )
    order_total = Decimal("189.00")

    product = Product(
        name="Webhook Test Necklace",
        slug=f"webhook-test-{unique_suffix}",
        description="Test product",
        category="NECKLACE",
        image_url="https://example.com/test-necklace.png",
    )
    location_code = (
        "PHYSICAL_STORE"
        if fulfillment_method == "STORE_PICKUP"
        else f"TEST-{unique_suffix}"
    )
    location = (
        test_db_session.query(Location)
        .filter(Location.code == location_code)
        .one_or_none()
    )
    if location is None:
        location = Location(
            code=location_code,
            name=(
                "Physical Store"
                if fulfillment_method == "STORE_PICKUP"
                else "Test Warehouse"
            ),
            location_type=(
                "STORE" if fulfillment_method == "STORE_PICKUP" else "WAREHOUSE"
            ),
        )
    test_db_session.add_all([product, location, customer])
    test_db_session.flush()

    variant = ProductVariant(
        product_id=product.id,
        material="Silver",
        color="Silver",
        size_label="Standard",
        sku=f"TEST-{unique_suffix}",
        ean=f"{uuid4().int % 10**13:013d}",
        retail_price=order_total,
    )
    test_db_session.add(variant)
    test_db_session.flush()

    order = Order(
        order_number=f"HM-TEST-{unique_suffix[:12]}",
        customer_id=customer.id,
        channel="ECOM",
        fulfillment_method=fulfillment_method,
        order_status="PENDING_PAYMENT",
        payment_status="UNPAID",
        currency="EUR",
        subtotal_gross=order_total,
        discount_total_gross=Decimal("0.00"),
        taxable_gross=order_total,
        net_total=Decimal("154.92"),
        vat_total=Decimal("34.08"),
        total_gross=order_total,
        standard_vat_rate=Decimal("22.00"),
        applied_vat_rate=Decimal("22.00"),
    )
    test_db_session.add(order)
    test_db_session.flush()

    order_line = OrderLine(
        order_id=order.id,
        variant_id=variant.id,
        quantity=1,
        product_name_snapshot=product.name,
        variant_snapshot={"material": "Silver", "color": "Silver"},
        sku_snapshot=variant.sku,
        ean_snapshot=variant.ean,
        retail_price_gross=order_total,
        discount_gross=Decimal("0.00"),
        taxable_gross=order_total,
        net_amount=Decimal("154.92"),
        vat_rate=Decimal("22.00"),
        vat_amount=Decimal("34.08"),
        total_gross=order_total,
    )
    inventory_unit = InventoryUnit(
        variant_id=variant.id,
        serial_number=f"TEST-WH-{unique_suffix}",
        location_id=location.id,
        status="RESERVED",
    )
    test_db_session.add_all([order_line, inventory_unit])
    test_db_session.flush()

    reservation = Reservation(
        order_id=order.id,
        order_line_id=order_line.id,
        inventory_unit_id=inventory_unit.id,
        status="ACTIVE",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=3),
    )
    session_id = f"cs_test_{unique_suffix}"
    payment = Payment(
        order_id=order.id,
        payment_method="CARD",
        provider="STRIPE",
        provider_checkout_session_id=session_id,
        amount=order_total,
        currency="EUR",
        status="PENDING",
    )
    if fulfillment_method == "SHIPPING":
        test_db_session.add(
            OrderAddress(
                order_id=order.id,
                address_type="SHIPPING",
                recipient_name="Test Buyer",
                address_line_1="1 Test Street",
                city="Rome",
                postal_code="00100",
                country_code="IT",
            )
        )
    test_db_session.add_all([reservation, payment])
    test_db_session.flush()

    event_id = f"evt_test_{unique_suffix}"
    payment_intent_id = f"pi_test_{unique_suffix}"
    timestamp = int(time.time())
    payload = json.dumps(
        {
            "id": event_id,
            "object": "event",
            "api_version": "2024-06-20",
            "created": timestamp,
            "data": {
                "object": {
                    "id": session_id,
                    "object": "checkout.session",
                    "amount_total": int(order_total * 100),
                    "client_reference_id": str(order.id),
                    "currency": "eur",
                    "metadata": {"order_id": str(order.id)},
                    "payment_intent": payment_intent_id,
                    "payment_status": "paid",
                }
            },
            "livemode": False,
            "pending_webhooks": 1,
            "request": None,
            "type": "checkout.session.completed",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {
        "Stripe-Signature": _signature(payload, secret, timestamp),
    }

    first_response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )

    assert first_response.status_code == 200
    assert first_response.json() == {"received": True}
    assert test_db_session.get(Order, order.id).order_status == "PAID"
    assert test_db_session.get(Order, order.id).payment_status == "PAID"
    assert test_db_session.get(Payment, payment.id).status == "PAID"
    assert (
        test_db_session.get(Payment, payment.id).provider_payment_id
        == payment_intent_id
    )
    assert test_db_session.get(Reservation, reservation.id).status == "COMMITTED"
    assert test_db_session.get(InventoryUnit, inventory_unit.id).status == "SOLD"
    assert test_db_session.get(InventoryUnit, inventory_unit.id).location_id is None

    def row_count(model, *conditions):
        return test_db_session.scalar(
            select(func.count()).select_from(model).where(*conditions)
        )

    assert (
        row_count(
            Notification,
            Notification.order_id == order.id,
            Notification.notification_type == "ORDER_READY_FOR_PICKUP",
        )
        == 0
    )

    assert (
        row_count(
            OrderLineUnit,
            OrderLineUnit.order_line_id == order_line.id,
        )
        == 1
    )
    assert (
        row_count(
            InventoryMovement,
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "SALE",
        )
        == 1
    )
    shipment = (
        test_db_session.query(Shipment).filter(Shipment.order_id == order.id).one()
    )
    assert shipment.fulfillment_method == fulfillment_method
    expected_shipment_status = (
        "READY_FOR_PICKUP" if fulfillment_method == "STORE_PICKUP" else "READY_TO_SHIP"
    )
    assert shipment.status == expected_shipment_status
    assert shipment.source_location_id == location.id

    assert fulfillment_service.advance_one_due_mock_shipment(test_db_session) is True
    test_db_session.refresh(order)
    test_db_session.refresh(shipment)
    assert order.order_status == expected_order_status

    if fulfillment_method == "STORE_PICKUP":
        assert shipment.shipping_address_snapshot is None
        assert shipment.ready_for_pickup_at is not None
        assert shipment.pickup_deadline_at is not None
        expected_ready_notification_count = 1
        assert row_count(OrderAddress, OrderAddress.order_id == order.id) == 0
        pickup_notification = (
            test_db_session.query(Notification)
            .filter(
                Notification.order_id == order.id,
                Notification.notification_type == "ORDER_READY_FOR_PICKUP",
            )
            .one()
        )
        assert pickup_notification.customer_id == customer.id
        assert (
            decrypt_pii(pickup_notification.recipient_email_ciphertext)
            == customer_email
        )
        assert json.loads(decrypt_pii(pickup_notification.payload_ciphertext)) == {
            "order_number": order.order_number
        }
    else:
        assert shipment.shipping_address_snapshot is not None
        assert shipment.ready_for_pickup_at is None
        assert shipment.pickup_deadline_at is None
        expected_ready_notification_count = 0
        assert row_count(OrderAddress, OrderAddress.order_id == order.id) == 1
    assert (
        row_count(
            StripeWebhookEvent,
            StripeWebhookEvent.stripe_event_id == event_id,
        )
        == 1
    )

    replay_response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )

    assert replay_response.status_code == 200
    assert replay_response.json() == {"received": True}
    assert (
        row_count(
            OrderLineUnit,
            OrderLineUnit.order_line_id == order_line.id,
        )
        == 1
    )
    assert (
        row_count(
            InventoryMovement,
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "SALE",
        )
        == 1
    )
    assert row_count(Shipment, Shipment.order_id == order.id) == 1
    assert (
        row_count(
            Notification,
            Notification.order_id == order.id,
            Notification.notification_type == "ORDER_READY_FOR_PICKUP",
        )
        == expected_ready_notification_count
    )
    assert (
        row_count(
            StripeWebhookEvent,
            StripeWebhookEvent.stripe_event_id == event_id,
        )
        == 1
    )


def test_late_paid_checkout_refunds_when_unit_is_reserved_by_another_order(
    api_client,
    test_db_session,
    monkeypatch,
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from backend.app.models.refund import Refund

    secret = "whsec_test_fixture_only"
    monkeypatch.setattr(payments.settings, "stripe_webhook_secret", secret)
    unique_suffix = uuid4().hex
    order_total = Decimal("189.00")
    product = Product(
        name="Late Payment Test Necklace",
        slug=f"late-payment-test-{unique_suffix}",
        description="Test product",
        category="NECKLACE",
        image_url="https://example.com/test-necklace.png",
    )
    location = Location(
        code=f"LATE-TEST-{unique_suffix}",
        name="Test Warehouse",
        location_type="WAREHOUSE",
    )
    test_db_session.add_all([product, location])
    test_db_session.flush()

    variant = ProductVariant(
        product_id=product.id,
        material="Silver",
        color="Silver",
        size_label="Standard",
        sku=f"LATE-TEST-{unique_suffix}",
        ean=f"{uuid4().int % 10**13:013d}",
        retail_price=order_total,
    )
    test_db_session.add(variant)
    test_db_session.flush()

    def create_order(order_number, order_status):
        order = Order(
            order_number=order_number,
            channel="ECOM",
            fulfillment_method="SHIPPING",
            order_status=order_status,
            payment_status="UNPAID",
            currency="EUR",
            subtotal_gross=order_total,
            discount_total_gross=Decimal("0.00"),
            taxable_gross=order_total,
            net_total=Decimal("154.92"),
            vat_total=Decimal("34.08"),
            total_gross=order_total,
            standard_vat_rate=Decimal("22.00"),
            applied_vat_rate=Decimal("22.00"),
        )
        test_db_session.add(order)
        test_db_session.flush()

        order_line = OrderLine(
            order_id=order.id,
            variant_id=variant.id,
            quantity=1,
            product_name_snapshot=product.name,
            variant_snapshot={"material": "Silver", "color": "Silver"},
            sku_snapshot=variant.sku,
            ean_snapshot=variant.ean,
            retail_price_gross=order_total,
            discount_gross=Decimal("0.00"),
            taxable_gross=order_total,
            net_amount=Decimal("154.92"),
            vat_rate=Decimal("22.00"),
            vat_amount=Decimal("34.08"),
            total_gross=order_total,
        )
        test_db_session.add(order_line)
        test_db_session.flush()
        return order, order_line

    late_order, late_order_line = create_order(
        f"HM-LATE-{unique_suffix[:12]}",
        "EXPIRED",
    )
    competing_order, competing_order_line = create_order(
        f"HM-COMP-{unique_suffix[:12]}",
        "PENDING_PAYMENT",
    )

    inventory_unit = InventoryUnit(
        variant_id=variant.id,
        serial_number=f"LATE-TEST-WH-{unique_suffix}",
        location_id=location.id,
        status="RESERVED",
    )
    test_db_session.add(inventory_unit)
    test_db_session.flush()

    late_reservation = Reservation(
        order_id=late_order.id,
        order_line_id=late_order_line.id,
        inventory_unit_id=inventory_unit.id,
        status="EXPIRED",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    competing_reservation = Reservation(
        order_id=competing_order.id,
        order_line_id=competing_order_line.id,
        inventory_unit_id=inventory_unit.id,
        status="ACTIVE",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=3),
    )
    session_id = f"cs_test_{unique_suffix}"
    payment = Payment(
        order_id=late_order.id,
        payment_method="CARD",
        provider="STRIPE",
        provider_checkout_session_id=session_id,
        amount=order_total,
        currency="EUR",
        status="PENDING",
    )
    shipping_address = OrderAddress(
        order_id=late_order.id,
        address_type="SHIPPING",
        recipient_name="Test Buyer",
        address_line_1="1 Test Street",
        city="Rome",
        postal_code="00100",
        country_code="IT",
    )
    test_db_session.add_all(
        [late_reservation, competing_reservation, payment, shipping_address]
    )
    test_db_session.flush()
    test_db_session.commit()

    event_id = f"evt_late_test_{unique_suffix}"
    payment_intent_id = f"pi_late_test_{unique_suffix}"
    refund_id = f"re_test_{unique_suffix}"
    mock_refund_creator = Mock(
        return_value=SimpleNamespace(
            id=refund_id,
            object="refund",
            amount=int(order_total * 100),
            currency="eur",
            payment_intent=payment_intent_id,
            status="succeeded",
        )
    )
    monkeypatch.setattr(
        payments,
        "create_late_stripe_payment_refund",
        mock_refund_creator,
    )

    timestamp = int(time.time())
    payload = json.dumps(
        {
            "id": event_id,
            "object": "event",
            "api_version": "2024-06-20",
            "created": timestamp,
            "data": {
                "object": {
                    "id": session_id,
                    "object": "checkout.session",
                    "amount_total": int(order_total * 100),
                    "client_reference_id": str(late_order.id),
                    "currency": "eur",
                    "metadata": {"order_id": str(late_order.id)},
                    "payment_intent": payment_intent_id,
                    "payment_status": "paid",
                }
            },
            "livemode": False,
            "pending_webhooks": 1,
            "request": None,
            "type": "checkout.session.completed",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {
        "Stripe-Signature": _signature(payload, secret, timestamp),
    }

    response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}
    mock_refund_creator.assert_called_once_with(payment, payment_intent_id)
    assert test_db_session.get(Order, late_order.id).order_status == "EXPIRED"
    assert test_db_session.get(Order, late_order.id).payment_status == "REFUNDED"
    assert test_db_session.get(Payment, payment.id).status == "REFUNDED"
    assert (
        test_db_session.get(Payment, payment.id).provider_payment_id
        == payment_intent_id
    )
    assert test_db_session.get(Reservation, late_reservation.id).status == "EXPIRED"
    assert test_db_session.get(Reservation, competing_reservation.id).status == "ACTIVE"
    assert test_db_session.get(InventoryUnit, inventory_unit.id).status == "RESERVED"
    assert (
        test_db_session.get(InventoryUnit, inventory_unit.id).location_id == location.id
    )

    refund = test_db_session.scalar(
        select(Refund).where(Refund.provider_ref == refund_id)
    )
    assert refund.payment_id == payment.id
    assert refund.amount == order_total
    assert refund.method == "ORIGINAL_PAYMENT"
    assert refund.status == "REFUNDED"
    late_refund_event = (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "SYSTEM_LATE_PAYMENT_REFUND",
            AuditEvent.outcome == "SUCCEEDED",
            AuditEvent.target_type == "ORDER",
            AuditEvent.target_id == late_order.id,
        )
        .one()
    )
    assert late_refund_event.actor_type == "SYSTEM"
    assert late_refund_event.details == {"amount": "189.00", "currency": "EUR"}

    assert (
        test_db_session.scalar(
            select(OrderLineUnit.id).where(
                OrderLineUnit.order_line_id == late_order_line.id
            )
        )
        is None
    )
    assert (
        test_db_session.scalar(
            select(Shipment.id).where(Shipment.order_id == late_order.id)
        )
        is None
    )
    assert (
        test_db_session.scalar(
            select(InventoryMovement.id).where(
                InventoryMovement.order_id == late_order.id,
                InventoryMovement.movement_type == "SALE",
            )
        )
        is None
    )


def test_refund_updated_marks_payment_refunded_once(
    api_client,
    test_db_session,
    monkeypatch,
):
    from backend.app.models.refund import Refund

    secret = "whsec_test_fixture_only"
    monkeypatch.setattr(payments.settings, "stripe_webhook_secret", secret)
    unique_suffix = uuid4().hex
    order_total = Decimal("189.00")
    order = Order(
        order_number=f"HM-REFUND-{unique_suffix[:12]}",
        channel="ECOM",
        fulfillment_method="SHIPPING",
        order_status="PAID",
        payment_status="PAID",
        currency="EUR",
        subtotal_gross=order_total,
        discount_total_gross=Decimal("0.00"),
        taxable_gross=order_total,
        net_total=Decimal("154.92"),
        vat_total=Decimal("34.08"),
        total_gross=order_total,
        standard_vat_rate=Decimal("22.00"),
        applied_vat_rate=Decimal("22.00"),
    )
    test_db_session.add(order)
    test_db_session.flush()

    payment_intent_id = f"pi_refund_test_{unique_suffix}"
    payment = Payment(
        order_id=order.id,
        payment_method="CARD",
        provider="STRIPE",
        provider_payment_id=payment_intent_id,
        amount=order_total,
        currency="EUR",
        status="PAID",
    )
    test_db_session.add(payment)
    test_db_session.flush()

    refund_id = f"re_test_{unique_suffix}"
    refund = Refund(
        payment_id=payment.id,
        amount=order_total,
        method="ORIGINAL_PAYMENT",
        provider_ref=refund_id,
        status="PENDING",
    )
    test_db_session.add(refund)
    test_db_session.flush()
    test_db_session.commit()

    event_id = f"evt_refund_test_{unique_suffix}"
    timestamp = int(time.time())
    payload = json.dumps(
        {
            "id": event_id,
            "object": "event",
            "api_version": "2024-06-20",
            "created": timestamp,
            "data": {
                "object": {
                    "id": refund_id,
                    "object": "refund",
                    "amount": int(order_total * 100),
                    "currency": "eur",
                    "metadata": {},
                    "payment_intent": payment_intent_id,
                    "status": "succeeded",
                }
            },
            "livemode": False,
            "pending_webhooks": 1,
            "request": None,
            "type": "refund.updated",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {
        "Stripe-Signature": _signature(payload, secret, timestamp),
    }

    response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}
    assert test_db_session.get(Refund, refund.id).status == "REFUNDED"
    assert test_db_session.get(Refund, refund.id).completed_at is not None
    assert test_db_session.get(Payment, payment.id).status == "REFUNDED"
    assert test_db_session.get(Order, order.id).payment_status == "REFUNDED"
    assert test_db_session.get(Order, order.id).order_status == "PAID"

    replay_response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers=headers,
    )

    assert replay_response.status_code == 200
    assert replay_response.json() == {"received": True}
    assert test_db_session.get(Refund, refund.id).status == "REFUNDED"
    assert test_db_session.get(Payment, payment.id).status == "REFUNDED"
    assert (
        test_db_session.scalar(
            select(func.count())
            .select_from(Refund)
            .where(Refund.provider_ref == refund_id)
        )
        == 1
    )
    assert (
        test_db_session.scalar(
            select(func.count())
            .select_from(StripeWebhookEvent)
            .where(StripeWebhookEvent.stripe_event_id == event_id)
        )
        == 1
    )
