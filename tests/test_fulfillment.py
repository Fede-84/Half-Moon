import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from cryptography.fernet import InvalidToken
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import backend.app.services.fulfillment as fulfillment_service
from backend.app.core.authentication import get_current_customer
from backend.app.core.config import settings
from backend.app.core.data_protection import (
    create_email_lookup_hmac,
    decrypt_pii,
    encrypt_pii,
)
from backend.app.main import app
from backend.app.models.customer import Customer
from backend.app.models.audit_event import AuditEvent
from backend.app.models.inventory_movement import InventoryMovement
from backend.app.models.inventory_unit import InventoryUnit
from backend.app.models.location import Location
from backend.app.models.notification import Notification
from backend.app.models.order import Order
from backend.app.models.order_line import OrderLine
from backend.app.models.order_line_unit import OrderLineUnit
from backend.app.models.payment import Payment
from backend.app.models.product import Product
from backend.app.models.product_variant import ProductVariant
from backend.app.models.refund import Refund
from backend.app.models.shipment import Shipment
from backend.app.services.fulfillment import advance_one_due_mock_shipment
from backend.app.services.audit import record_audit_event


def _create_order_with_shipment(
    db: Session,
    *,
    channel: str = "ECOM",
    order_status: str | None = None,
    payment_status: str = "PAID",
    fulfillment_method: str = "SHIPPING",
    shipment_status: str | None = None,
    shipment_created_at: datetime | None = None,
    shipped_at: datetime | None = None,
    ready_for_pickup_at: datetime | None = None,
    pickup_deadline_at: datetime | None = None,
    picked_up_at: datetime | None = None,
    quantities: tuple[int, ...] = (1,),
) -> tuple[Order, Shipment, str]:
    suffix = uuid4().hex
    email = f"fulfillment-{suffix}@example.com"
    customer = Customer(
        email_ciphertext=encrypt_pii(email),
        email_lookup_hmac=create_email_lookup_hmac(email),
    )
    db.add(customer)
    db.flush()
    product = Product(
        name="Fulfillment Test Necklace",
        slug=f"fulfillment-test-{suffix}",
        description="Test product",
        category="NECKLACE",
        image_url="https://example.com/test-necklace.png",
    )
    if fulfillment_method == "STORE_PICKUP":
        location = (
            db.query(Location).filter(Location.code == "PHYSICAL_STORE").one_or_none()
        )
        if location is None:
            location = Location(
                code="PHYSICAL_STORE",
                name="Physical Store",
                location_type="STORE",
            )
    else:
        location = Location(
            code=f"FULFILLMENT-{suffix}",
            name="Test Warehouse",
            location_type="WAREHOUSE",
        )

    effective_shipment_status = shipment_status or (
        "READY_FOR_PICKUP" if fulfillment_method == "STORE_PICKUP" else "READY_TO_SHIP"
    )
    effective_order_status = order_status or effective_shipment_status
    quantity_total = sum(quantities)
    order_total = Decimal("10.00") * quantity_total
    order = Order(
        order_number=f"HM-FUL-{suffix[:12]}",
        customer_id=customer.id,
        channel=channel,
        fulfillment_method=(fulfillment_method if channel == "ECOM" else None),
        order_status=effective_order_status,
        payment_status=payment_status,
        currency="EUR",
        subtotal_gross=order_total,
        discount_total_gross=Decimal("0.00"),
        taxable_gross=order_total,
        net_total=Decimal("8.20") * quantity_total,
        vat_total=Decimal("1.80") * quantity_total,
        total_gross=order_total,
        standard_vat_rate=Decimal("22.00"),
        applied_vat_rate=Decimal("22.00"),
    )

    db.add_all([product, location, order])
    db.flush()

    variant = ProductVariant(
        product_id=product.id,
        material="Silver",
        color="Silver",
        size_label="Standard",
        sku=f"FULFILLMENT-{suffix}",
        ean=f"{uuid4().int % 10**13:013d}",
        retail_price=Decimal("10.00"),
    )
    db.add(variant)
    db.flush()

    for quantity in quantities:
        db.add(
            OrderLine(
                order_id=order.id,
                variant_id=variant.id,
                quantity=quantity,
                product_name_snapshot=product.name,
                variant_snapshot={"material": "Silver", "color": "Silver"},
                sku_snapshot=variant.sku,
                ean_snapshot=variant.ean,
                retail_price_gross=Decimal("10.00"),
                discount_gross=Decimal("0.00"),
                taxable_gross=Decimal("10.00") * quantity,
                net_amount=Decimal("8.20") * quantity,
                vat_rate=Decimal("22.00"),
                vat_amount=Decimal("1.80") * quantity,
                total_gross=Decimal("10.00") * quantity,
            )
        )

    shipment = Shipment(
        order_id=order.id,
        source_location_id=location.id,
        fulfillment_method=fulfillment_method,
        status=effective_shipment_status,
        created_at=shipment_created_at
        or datetime.now(timezone.utc) - timedelta(seconds=6),
        shipped_at=shipped_at,
        ready_for_pickup_at=ready_for_pickup_at,
        pickup_deadline_at=pickup_deadline_at,
        picked_up_at=picked_up_at,
    )
    db.add(shipment)
    db.flush()

    return order, shipment, email


@pytest.mark.parametrize(
    ("fulfillment_method", "expected_order_status"),
    [
        ("SHIPPING", "READY_TO_SHIP"),
        ("STORE_PICKUP", "READY_FOR_PICKUP"),
    ],
)
def test_paid_order_persists_before_advancing_to_fulfillment_ready(
    test_db_session: Session,
    fulfillment_method: str,
    expected_order_status: str,
) -> None:
    order, shipment, _ = _create_order_with_shipment(
        test_db_session,
        order_status="PAID",
        fulfillment_method=fulfillment_method,
    )

    assert advance_one_due_mock_shipment(test_db_session) is True
    test_db_session.refresh(order)
    test_db_session.refresh(shipment)

    assert order.order_status == expected_order_status
    assert shipment.status == expected_order_status
    if fulfillment_method == "STORE_PICKUP":
        assert shipment.ready_for_pickup_at is not None
        assert shipment.pickup_deadline_at is not None
        assert (
            test_db_session.query(Notification)
            .filter(
                Notification.order_id == order.id,
                Notification.notification_type == "ORDER_READY_FOR_PICKUP",
            )
            .count()
            == 1
        )
    else:
        assert shipment.ready_for_pickup_at is None
        assert shipment.pickup_deadline_at is None
        assert (
            test_db_session.query(Notification)
            .filter(Notification.order_id == order.id)
            .count()
            == 0
        )


def test_due_shipment_advances_one_stage_and_enqueues_each_email(
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "mock_ship_after_seconds", 5)
    monkeypatch.setattr(settings, "mock_delivery_after_seconds", 5)
    order, shipment, email = _create_order_with_shipment(
        test_db_session,
        quantities=(2, 3),
    )

    assert advance_one_due_mock_shipment(test_db_session) is True
    test_db_session.refresh(order)
    test_db_session.refresh(shipment)

    assert shipment.status == "SHIPPED"
    assert shipment.shipped_at is not None
    assert shipment.mock_tracking_number == (
        f"HM-SHP-{order.order_number.removeprefix('HM-')}"
    )
    assert order.order_status == "SHIPPED"

    notifications = (
        test_db_session.query(Notification)
        .filter(Notification.order_id == order.id)
        .all()
    )
    assert len(notifications) == 1
    shipped_notification = notifications[0]
    assert shipped_notification.notification_type == "ORDER_SHIPPED"
    assert decrypt_pii(shipped_notification.recipient_email_ciphertext) == email
    assert json.loads(decrypt_pii(shipped_notification.payload_ciphertext)) == {
        "order_number": order.order_number,
        "shipment_reference": shipment.mock_tracking_number,
    }

    # A second immediate pass must not skip the five-second shipped interval.
    assert advance_one_due_mock_shipment(test_db_session) is False
    assert (
        test_db_session.query(Notification)
        .filter(Notification.order_id == order.id)
        .count()
        == 1
    )

    shipment.shipped_at = datetime.now(timezone.utc) - timedelta(seconds=6)
    test_db_session.flush()

    assert advance_one_due_mock_shipment(test_db_session) is True
    test_db_session.refresh(order)
    test_db_session.refresh(shipment)

    assert shipment.status == "DELIVERED"
    assert shipment.delivered_at is not None
    assert order.order_status == "DELIVERED"

    notifications = (
        test_db_session.query(Notification)
        .filter(Notification.order_id == order.id)
        .all()
    )
    assert len(notifications) == 2
    delivered_notification = next(
        notification
        for notification in notifications
        if notification.notification_type == "ORDER_DELIVERED"
    )
    assert decrypt_pii(delivered_notification.recipient_email_ciphertext) == email
    assert json.loads(decrypt_pii(delivered_notification.payload_ciphertext)) == {
        "order_number": order.order_number,
        "item_count": "5",
    }


def test_due_store_pickup_advances_after_mock_delay_and_notifies_once(
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "mock_ship_after_seconds", 5)

    order, shipment, email = _create_order_with_shipment(
        test_db_session,
        fulfillment_method="STORE_PICKUP",
        pickup_deadline_at=datetime.now(timezone.utc) + timedelta(days=7),
    )

    shipment.ready_for_pickup_at = datetime.now(timezone.utc) - timedelta(seconds=4)
    test_db_session.flush()

    assert advance_one_due_mock_shipment(test_db_session) is False
    assert shipment.status == "READY_FOR_PICKUP"
    assert (
        test_db_session.query(Notification)
        .filter(Notification.order_id == order.id)
        .count()
        == 0
    )

    shipment.ready_for_pickup_at = datetime.now(timezone.utc) - timedelta(seconds=6)
    test_db_session.flush()

    assert advance_one_due_mock_shipment(test_db_session) is True
    test_db_session.refresh(order)
    test_db_session.refresh(shipment)

    assert shipment.status == "PICKED_UP"
    assert shipment.picked_up_at is not None
    assert order.order_status == "PICKED_UP"

    notifications = (
        test_db_session.query(Notification)
        .filter(Notification.order_id == order.id)
        .all()
    )
    assert len(notifications) == 1
    assert notifications[0].notification_type == "ORDER_PICKED_UP"
    assert decrypt_pii(notifications[0].recipient_email_ciphertext) == email
    assert json.loads(decrypt_pii(notifications[0].payload_ciphertext)) == {
        "order_number": order.order_number,
    }
    assert advance_one_due_mock_shipment(test_db_session) is False
    assert (
        test_db_session.query(Notification)
        .filter(Notification.order_id == order.id)
        .count()
        == 1
    )


def test_store_pickup_expiry_waits_until_deadline_then_refunds_and_restocks_once(
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(fulfillment_service, "datetime", FrozenDateTime)
    monkeypatch.setattr(settings, "mock_ship_after_seconds", 5)

    order, shipment, email = _create_order_with_shipment(
        test_db_session,
        fulfillment_method="STORE_PICKUP",
        ready_for_pickup_at=fixed_now - timedelta(seconds=1),
        pickup_deadline_at=fixed_now,
        quantities=(2, 1),
    )

    payment = Payment(
        order_id=order.id,
        payment_method="CARD",
        provider="STRIPE",
        amount=order.total_gross,
        currency=order.currency,
        status="PAID",
    )
    test_db_session.add(payment)
    test_db_session.flush()

    order_lines = (
        test_db_session.query(OrderLine)
        .filter(OrderLine.order_id == order.id)
        .order_by(OrderLine.id)
        .all()
    )
    inventory_unit_ids = []

    for order_line in order_lines:
        for _ in range(order_line.quantity):
            unit = InventoryUnit(
                variant_id=order_line.variant_id,
                serial_number=f"PU-EXP-{uuid4().hex}",
                location_id=None,
                status="SOLD",
            )
            test_db_session.add(unit)
            test_db_session.flush()
            inventory_unit_ids.append(unit.id)

            test_db_session.add(
                OrderLineUnit(
                    order_line_id=order_line.id,
                    inventory_unit_id=unit.id,
                )
            )
            test_db_session.add(
                InventoryMovement(
                    inventory_unit_id=unit.id,
                    movement_type="SALE",
                    from_location_id=shipment.source_location_id,
                    to_location_id=None,
                    from_status="RESERVED",
                    to_status="SOLD",
                    order_id=order.id,
                    actor_type="SYSTEM",
                    note="Payment confirmed",
                )
            )

    test_db_session.flush()

    # The deadline is still inclusive here. The pickup is not yet due to progress.
    assert advance_one_due_mock_shipment(test_db_session) is False
    assert shipment.status == "READY_FOR_PICKUP"
    assert (
        test_db_session.query(Refund).filter(Refund.payment_id == payment.id).count()
        == 0
    )

    # A deadline even one microsecond in the past has expired.
    shipment.pickup_deadline_at = fixed_now - timedelta(microseconds=1)
    test_db_session.flush()

    assert advance_one_due_mock_shipment(test_db_session) is True

    test_db_session.refresh(order)
    test_db_session.refresh(shipment)
    test_db_session.refresh(payment)

    assert shipment.status == "CANCELLED"
    assert order.order_status == "CANCELLED"
    assert order.payment_status == "REFUNDED"
    assert payment.status == "REFUNDED"

    refunds = (
        test_db_session.query(Refund).filter(Refund.payment_id == payment.id).all()
    )
    assert len(refunds) == 1
    assert refunds[0].amount == payment.amount == order.total_gross
    assert refunds[0].method == "ORIGINAL_PAYMENT"
    assert refunds[0].status == "REFUNDED"
    assert refunds[0].return_id is None

    units = (
        test_db_session.query(InventoryUnit)
        .filter(InventoryUnit.id.in_(inventory_unit_ids))
        .all()
    )
    assert len(units) == len(inventory_unit_ids)
    assert all(unit.status == "IN_STOCK" for unit in units)
    assert all(unit.location_id == shipment.source_location_id for unit in units)

    expiry_movements = (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order.id,
            InventoryMovement.movement_type == "PICKUP_EXPIRY",
        )
        .all()
    )
    assert len(expiry_movements) == len(inventory_unit_ids)
    assert {movement.inventory_unit_id for movement in expiry_movements} == set(
        inventory_unit_ids
    )
    assert all(movement.from_status == "SOLD" for movement in expiry_movements)
    assert all(movement.to_status == "IN_STOCK" for movement in expiry_movements)
    assert all(
        movement.to_location_id == shipment.source_location_id
        for movement in expiry_movements
    )

    notifications = (
        test_db_session.query(Notification)
        .filter(Notification.order_id == order.id)
        .all()
    )
    assert len(notifications) == 1
    assert notifications[0].notification_type == "ORDER_PICKUP_EXPIRED"
    assert notifications[0].customer_id == order.customer_id
    assert decrypt_pii(notifications[0].recipient_email_ciphertext) == email
    assert json.loads(decrypt_pii(notifications[0].payload_ciphertext)) == {
        "order_number": order.order_number,
    }
    expiry_event = (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "SYSTEM_PICKUP_EXPIRY",
            AuditEvent.target_type == "SHIPMENT",
            AuditEvent.target_id == shipment.id,
        )
        .one()
    )
    assert expiry_event.outcome == "SUCCEEDED"
    assert expiry_event.details == {"amount": "30.00", "item_count": 3}

    # A repeat pass must not duplicate the refund, restock movements, or email.
    assert advance_one_due_mock_shipment(test_db_session) is False
    assert (
        test_db_session.query(Refund).filter(Refund.payment_id == payment.id).count()
        == 1
    )
    assert test_db_session.query(InventoryMovement).filter(
        InventoryMovement.order_id == order.id,
        InventoryMovement.movement_type == "PICKUP_EXPIRY",
    ).count() == len(inventory_unit_ids)
    assert (
        test_db_session.query(Notification)
        .filter(
            Notification.order_id == order.id,
            Notification.notification_type == "ORDER_PICKUP_EXPIRED",
        )
        .count()
        == 1
    )


def test_pickup_expiry_integrity_failure_quarantines_shipment_once(
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(fulfillment_service, "datetime", FrozenDateTime)
    order, shipment, _ = _create_order_with_shipment(
        test_db_session,
        fulfillment_method="STORE_PICKUP",
        ready_for_pickup_at=fixed_now - timedelta(seconds=1),
        pickup_deadline_at=fixed_now - timedelta(microseconds=1),
    )
    test_db_session.commit()

    assert advance_one_due_mock_shipment(test_db_session) is True

    test_db_session.refresh(order)
    test_db_session.refresh(shipment)
    assert shipment.status == "FAILED"
    assert order.order_status == "READY_FOR_PICKUP"
    assert order.payment_status == "PAID"
    assert (
        test_db_session.query(Refund)
        .filter(Refund.payment_id.is_not(None))
        .count()
        == 0
    )
    assert (
        test_db_session.query(Notification)
        .filter(Notification.order_id == order.id)
        .count()
        == 0
    )

    failure_event = (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "SYSTEM_PICKUP_EXPIRY",
            AuditEvent.outcome == "FAILED",
            AuditEvent.target_type == "SHIPMENT",
            AuditEvent.target_id == shipment.id,
        )
        .one()
    )
    assert failure_event.error_code == "PICKUP_EXPIRY_PAYMENT_COUNT"
    assert failure_event.details is None

    record_audit_event(
        test_db_session,
        actor_type="SYSTEM",
        action_code="SYSTEM_PICKUP_EXPIRY",
        outcome="FAILED",
        target_type="SHIPMENT",
        target_id=shipment.id,
        error_code="PICKUP_EXPIRY_PAYMENT_COUNT",
    )
    assert advance_one_due_mock_shipment(test_db_session) is False
    assert (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "SYSTEM_PICKUP_EXPIRY",
            AuditEvent.outcome == "FAILED",
            AuditEvent.target_type == "SHIPMENT",
            AuditEvent.target_id == shipment.id,
        )
        .count()
        == 1
    )


def test_unclassified_fulfillment_value_error_quarantines_shipment(
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    def fail_expiry(
        db: Session,
        order: Order,
        shipment: Shipment,
        now: datetime,
    ) -> tuple[Decimal, int]:
        raise ValueError("unexpected invalid fulfillment data")

    monkeypatch.setattr(fulfillment_service, "datetime", FrozenDateTime)
    monkeypatch.setattr(fulfillment_service, "_expire_store_pickup_order", fail_expiry)
    order, shipment, _ = _create_order_with_shipment(
        test_db_session,
        fulfillment_method="STORE_PICKUP",
        ready_for_pickup_at=fixed_now - timedelta(days=8),
        pickup_deadline_at=fixed_now - timedelta(microseconds=1),
    )
    test_db_session.commit()

    assert advance_one_due_mock_shipment(test_db_session) is True

    test_db_session.refresh(order)
    test_db_session.refresh(shipment)
    assert shipment.status == "FAILED"
    assert order.order_status == "READY_FOR_PICKUP"
    assert advance_one_due_mock_shipment(test_db_session) is False


def test_invalid_customer_ciphertext_quarantines_due_shipment(
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "mock_ship_after_seconds", 1)
    order, shipment, _ = _create_order_with_shipment(
        test_db_session,
        shipment_created_at=datetime.now(timezone.utc) - timedelta(seconds=2),
    )
    customer = test_db_session.get(Customer, order.customer_id)
    assert customer is not None
    customer.email_ciphertext = "invalid-ciphertext"
    test_db_session.commit()

    with pytest.raises(InvalidToken):
        decrypt_pii(customer.email_ciphertext)

    assert advance_one_due_mock_shipment(test_db_session) is True

    test_db_session.refresh(order)
    test_db_session.refresh(shipment)
    assert shipment.status == "FAILED"
    assert order.order_status == "READY_TO_SHIP"
    assert (
        test_db_session.query(Notification)
        .filter(Notification.order_id == order.id)
        .count()
        == 0
    )
    assert advance_one_due_mock_shipment(test_db_session) is False


def test_sqlalchemy_error_leaves_due_shipment_retryable(
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "mock_ship_after_seconds", 1)
    order, shipment, _ = _create_order_with_shipment(
        test_db_session,
        shipment_created_at=datetime.now(timezone.utc) - timedelta(seconds=2),
    )
    test_db_session.commit()

    def fail_progress_due_shipment(
        db: Session,
        selected_shipment: Shipment,
        selected_order: Order,
        current_time: datetime,
    ) -> bool:
        raise SQLAlchemyError("temporary database failure")

    monkeypatch.setattr(
        fulfillment_service,
        "_progress_due_shipment",
        fail_progress_due_shipment,
    )

    with pytest.raises(SQLAlchemyError, match="temporary database failure"):
        advance_one_due_mock_shipment(test_db_session)

    test_db_session.refresh(order)
    test_db_session.refresh(shipment)
    assert shipment.status == "READY_TO_SHIP"
    assert order.order_status == "READY_TO_SHIP"


@pytest.mark.parametrize(
    ("channel", "order_status", "payment_status", "fulfillment_method"),
    [
        ("ECOM", "PAID", "UNPAID", "SHIPPING"),
        ("POS", "COMPLETED", "PAID", "SHIPPING"),
        ("ECOM", "READY_FOR_PICKUP", "PAID", "STORE_PICKUP"),
    ],
    ids=["unpaid", "pos", "store-pickup"],
)
def test_ineligible_order_does_not_advance_or_enqueue(
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    channel: str,
    order_status: str,
    payment_status: str,
    fulfillment_method: str,
) -> None:
    monkeypatch.setattr(settings, "mock_ship_after_seconds", 5)
    monkeypatch.setattr(settings, "mock_delivery_after_seconds", 5)
    order, shipment, _ = _create_order_with_shipment(
        test_db_session,
        channel=channel,
        order_status=order_status,
        payment_status=payment_status,
        fulfillment_method=fulfillment_method,
    )

    assert advance_one_due_mock_shipment(test_db_session) is False
    test_db_session.refresh(order)
    test_db_session.refresh(shipment)

    expected_shipment_status = (
        "READY_FOR_PICKUP" if fulfillment_method == "STORE_PICKUP" else "READY_TO_SHIP"
    )
    assert shipment.status == expected_shipment_status
    assert order.order_status == order_status
    assert (
        test_db_session.query(Notification)
        .filter(Notification.order_id == order.id)
        .count()
        == 0
    )


def test_order_status_get_does_not_advance_shipment(
    api_client: TestClient,
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "mock_ship_after_seconds", 5)
    order, shipment, _ = _create_order_with_shipment(test_db_session)
    customer = test_db_session.get(Customer, order.customer_id)
    assert customer is not None

    monkeypatch.setitem(
        app.dependency_overrides,
        get_current_customer,
        lambda: customer,
    )

    response = api_client.get(f"/api/v1/orders/{order.id}/status")

    assert response.status_code == 200
    assert response.json()["status"] == "READY_TO_SHIP"
    assert response.json()["order_status"] == "READY_TO_SHIP"

    test_db_session.refresh(shipment)
    assert shipment.status == "READY_TO_SHIP"
    assert (
        test_db_session.query(Notification)
        .filter(Notification.order_id == order.id)
        .count()
        == 0
    )


def test_pickup_order_status_includes_pickup_timestamps(
    api_client: TestClient,
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready_at = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)
    deadline_at = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)
    picked_up_at = datetime(2026, 10, 2, 10, tzinfo=timezone.utc)

    order, _, _ = _create_order_with_shipment(
        test_db_session,
        fulfillment_method="STORE_PICKUP",
        shipment_status="PICKED_UP",
        ready_for_pickup_at=ready_at,
        pickup_deadline_at=deadline_at,
        picked_up_at=picked_up_at,
    )
    customer = test_db_session.get(Customer, order.customer_id)
    assert customer is not None

    monkeypatch.setitem(
        app.dependency_overrides,
        get_current_customer,
        lambda: customer,
    )

    response = api_client.get(f"/api/v1/orders/{order.id}/status")

    assert response.status_code == 200
    response_body = response.json()
    assert response_body["status"] == "PICKED_UP"
    assert response_body["order_status"] == "PICKED_UP"
    assert datetime.fromisoformat(response_body["ready_for_pickup_at"]) == ready_at
    assert datetime.fromisoformat(response_body["pickup_deadline_at"]) == deadline_at
    assert datetime.fromisoformat(response_body["picked_up_at"]) == picked_up_at
