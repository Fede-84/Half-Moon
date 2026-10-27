from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, func, select
from sqlalchemy.orm import DeclarativeBase

from backend.app.models.customer import Customer
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.audit_event import AuditEvent
from backend.app.models.inventory_movement import InventoryMovement
from backend.app.models.inventory_unit import InventoryUnit
from backend.app.models.location import Location
from backend.app.models.order import Order
from backend.app.models.order_line import OrderLine
from backend.app.models.order_line_unit import OrderLineUnit
from backend.app.models.payment import Payment
from backend.app.models.product import Product
from backend.app.models.product_variant import ProductVariant
from backend.app.models.refund import Refund
from backend.app.models.return_item import ReturnItem
from backend.app.models.return_record import ReturnRecord
from backend.app.models.shipment import Shipment
from backend.app.schemas.returns import CreateReturnRequest, ReturnItemRequest
from backend.app.services import returns as returns_service
from backend.app.services.deadlines import utc_deadline_after_14_days
from backend.app.main import (
    list_customer_order_returns_route,
    process_return_route,
)
from backend.app.services.returns import create_return_request, process_return


def test_14_day_deadline_is_elapsed_duration_across_dst() -> None:
    local_anchor = datetime(
        2026,
        10,
        12,
        12,
        0,
        tzinfo=ZoneInfo("Europe/Berlin"),
    )

    assert utc_deadline_after_14_days(local_anchor) == datetime(
        2026,
        10,
        26,
        10,
        0,
        tzinfo=timezone.utc,
    )


def test_14_day_deadline_rejects_naive_anchor() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        utc_deadline_after_14_days(datetime(2026, 10, 12, 12, 0))


def _check_constraint(model: type[DeclarativeBase], name: str) -> str:
    table = model.metadata.tables[model.__tablename__]
    constraint = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == name
    )
    return str(constraint.sqltext)


def test_return_request_defaults_to_original_payment_refund() -> None:
    request = CreateReturnRequest(
        reason="Changed my mind",
        items=[ReturnItemRequest(order_line_unit_id=uuid4())],
    )

    assert request.requested_outcome == "REFUND"


@pytest.mark.parametrize("outcome", ["STORE_CREDIT", "EXCHANGE"])
def test_return_request_rejects_retired_outcomes(outcome: str) -> None:
    with pytest.raises(ValidationError):
        CreateReturnRequest.model_validate(
            {
                "requested_outcome": outcome,
                "reason": "Return request",
                "items": [{"order_line_unit_id": uuid4()}],
            }
        )


def test_active_model_constraints_allow_only_original_payment_returns() -> None:
    assert (
        _check_constraint(
            ReturnRecord,
            "ck_returns_requested_outcome",
        )
        == "requested_outcome = 'REFUND'"
    )
    assert (
        _check_constraint(
            Payment,
            "ck_payments_payment_method",
        )
        == "payment_method IN ('CARD', 'CASH')"
    )
    assert (
        _check_constraint(
            Refund,
            "ck_refunds_method",
        )
        == "method = 'ORIGINAL_PAYMENT'"
    )


def test_test_runtime_uses_nonproduction_integration_settings() -> None:
    from backend.app.core.config import settings

    assert settings.stripe_secret_key == "sk_test_half_moon_fixture_only"
    assert settings.stripe_webhook_secret == "whsec_half_moon_fixture_only"
    assert settings.gmail_sender_email == "test@example.invalid"
    assert settings.gmail_delivery_worker_enabled is False
    assert settings.gmail_oauth_setup_enabled is False


def test_process_return_validates_refund_total_before_mutating_inventory(
    test_db_session,
) -> None:
    suffix = uuid4().hex
    customer = Customer(
        email_ciphertext=f"encrypted-{suffix}",
        email_lookup_hmac=suffix,
    )
    product = Product(
        name="Return Validation Test",
        slug=f"return-validation-{suffix}",
        description="Test product",
        category="NECKLACE",
        image_url="https://example.com/return-validation.png",
    )
    location = Location(
        code=f"RETURN-{suffix}",
        name="Return Test Location",
        location_type="WAREHOUSE",
    )
    test_db_session.add_all([customer, product, location])
    test_db_session.flush()

    order = Order(
        order_number=f"HM-RETURN-{suffix[:12]}",
        customer_id=customer.id,
        channel="ECOM",
        fulfillment_method="SHIPPING",
        order_status="DELIVERED",
        payment_status="PAID",
        currency="EUR",
        subtotal_gross=Decimal("20.00"),
        discount_total_gross=Decimal("0.00"),
        taxable_gross=Decimal("20.00"),
        net_total=Decimal("16.39"),
        vat_total=Decimal("3.61"),
        total_gross=Decimal("20.00"),
        standard_vat_rate=Decimal("22.00"),
        applied_vat_rate=Decimal("22.00"),
    )
    test_db_session.add(order)
    test_db_session.flush()

    variant = ProductVariant(
        product_id=product.id,
        material="Silver",
        color="Silver",
        size_label="Standard",
        sku=f"RETURN-{suffix}",
        ean=f"{uuid4().int % 10**13:013d}",
        retail_price=Decimal("20.00"),
    )
    test_db_session.add(variant)
    test_db_session.flush()

    order_line = OrderLine(
        order_id=order.id,
        variant_id=variant.id,
        quantity=1,
        product_name_snapshot=product.name,
        variant_snapshot={"material": "Silver", "color": "Silver"},
        sku_snapshot=variant.sku,
        ean_snapshot=variant.ean,
        retail_price_gross=Decimal("20.00"),
        discount_gross=Decimal("0.00"),
        taxable_gross=Decimal("20.00"),
        net_amount=Decimal("16.39"),
        vat_rate=Decimal("22.00"),
        vat_amount=Decimal("3.61"),
        total_gross=Decimal("20.00"),
    )
    inventory_unit = InventoryUnit(
        variant_id=variant.id,
        serial_number=f"RETURN-{suffix}",
        location_id=None,
        status="SOLD",
    )
    payment = Payment(
        order_id=order.id,
        payment_method="CARD",
        provider="STRIPE",
        amount=Decimal("10.00"),
        currency="EUR",
        status="PAID",
    )
    shipment = Shipment(
        order_id=order.id,
        source_location_id=location.id,
        fulfillment_method="SHIPPING",
        status="DELIVERED",
    )
    test_db_session.add_all([order_line, inventory_unit, payment, shipment])
    test_db_session.flush()

    order_line_unit = OrderLineUnit(
        order_line_id=order_line.id,
        inventory_unit_id=inventory_unit.id,
    )
    return_record = ReturnRecord(
        order_id=order.id,
        customer_id=customer.id,
        status="REQUESTED",
        requested_outcome="REFUND",
        reason="Test refund validation",
    )
    test_db_session.add_all([order_line_unit, return_record])
    test_db_session.flush()

    return_item = ReturnItem(
        return_id=return_record.id,
        order_line_unit_id=order_line_unit.id,
        inspection_result="PENDING",
    )
    test_db_session.add(return_item)
    test_db_session.flush()

    test_db_session.commit()
    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            process_return_route(return_record.id, customer, test_db_session)
        assert error.value.status_code == 409
        assert error.value.detail == "Refund exceeds the original payment"

    assert inventory_unit.status == "SOLD"
    assert inventory_unit.location_id is None
    assert return_item.inspection_result == "PENDING"
    assert (
        test_db_session.scalar(
            select(func.count())
            .select_from(InventoryMovement)
            .where(InventoryMovement.return_id == return_record.id)
        )
        == 0
    )
    assert (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "RETURN_PROCESSING_INTEGRITY_FAILURE",
            AuditEvent.outcome == "FAILED",
            AuditEvent.target_type == "RETURN",
            AuditEvent.target_id == return_record.id,
        )
        .count()
        == 1
    )


def _create_picked_up_ecom_order(
    test_db_session,
    *,
    picked_up_at: datetime | None,
    shipment_status: str = "PICKED_UP",
    order_status: str | None = None,
) -> tuple[Customer, Order, Shipment, OrderLineUnit, InventoryUnit, Location]:
    suffix = uuid4().hex
    customer = Customer(
        email_ciphertext=f"encrypted-{suffix}",
        email_lookup_hmac=suffix,
    )
    test_db_session.add(customer)
    test_db_session.flush()
    test_db_session.add(
        CustomerAccount(
            customer_id=customer.id,
            password_hash="test-password-hash",
            email_verified_at=datetime.now(timezone.utc),
        )
    )

    product = Product(
        name="Pickup Return Test",
        slug=f"pickup-return-{suffix}",
        description="Pickup return test product",
        category="NECKLACE",
        image_url="https://example.com/pickup-return.png",
    )
    location = Location(
        code=f"PICKUP-{suffix}",
        name="Pickup Return Test Location",
        location_type="STORE",
    )
    test_db_session.add_all([product, location])
    test_db_session.flush()

    order = Order(
        order_number=f"HM-PICKUP-RETURN-{suffix[:12]}",
        customer_id=customer.id,
        channel="ECOM",
        fulfillment_method="STORE_PICKUP",
        order_status=order_status or shipment_status,
        payment_status="PAID",
        currency="EUR",
        subtotal_gross=Decimal("20.00"),
        discount_total_gross=Decimal("0.00"),
        taxable_gross=Decimal("20.00"),
        net_total=Decimal("16.39"),
        vat_total=Decimal("3.61"),
        total_gross=Decimal("20.00"),
        standard_vat_rate=Decimal("22.00"),
        applied_vat_rate=Decimal("22.00"),
    )
    variant = ProductVariant(
        product_id=product.id,
        material="Silver",
        color="Silver",
        size_label="Standard",
        sku=f"PICKUP-RETURN-{suffix}",
        ean=f"{uuid4().int % 10**13:013d}",
        retail_price=Decimal("20.00"),
    )
    test_db_session.add_all([order, variant])
    test_db_session.flush()

    order_line = OrderLine(
        order_id=order.id,
        variant_id=variant.id,
        quantity=1,
        product_name_snapshot=product.name,
        variant_snapshot={"material": "Silver", "color": "Silver"},
        sku_snapshot=variant.sku,
        ean_snapshot=variant.ean,
        retail_price_gross=Decimal("20.00"),
        discount_gross=Decimal("0.00"),
        taxable_gross=Decimal("20.00"),
        net_amount=Decimal("16.39"),
        vat_rate=Decimal("22.00"),
        vat_amount=Decimal("3.61"),
        total_gross=Decimal("20.00"),
    )
    inventory_unit = InventoryUnit(
        variant_id=variant.id,
        serial_number=f"PICKUP-RETURN-{suffix}",
        location_id=None,
        status="SOLD",
    )
    payment = Payment(
        order_id=order.id,
        payment_method="CARD",
        provider="STRIPE",
        amount=Decimal("20.00"),
        currency="EUR",
        status="PAID",
    )
    shipment = Shipment(
        order_id=order.id,
        source_location_id=location.id,
        fulfillment_method="STORE_PICKUP",
        status=shipment_status,
        picked_up_at=picked_up_at,
    )
    test_db_session.add_all([order_line, inventory_unit, payment, shipment])
    test_db_session.flush()

    allocation = OrderLineUnit(
        order_line_id=order_line.id,
        inventory_unit_id=inventory_unit.id,
    )
    test_db_session.add(allocation)
    test_db_session.flush()
    return customer, order, shipment, allocation, inventory_unit, location


def test_pickup_return_request_includes_pickup_deadline_and_processes_after(
    test_db_session,
    monkeypatch,
) -> None:
    deadline = datetime(2026, 1, 15, tzinfo=timezone.utc)
    customer, order, _, allocation, inventory_unit, location = (
        _create_picked_up_ecom_order(
            test_db_session,
            picked_up_at=deadline - timedelta(days=14),
        )
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

    return_record = create_return_request(
        test_db_session,
        customer.id,
        order.id,
        CreateReturnRequest(
            reason="Pickup return at the inclusive deadline",
            items=[ReturnItemRequest(order_line_unit_id=allocation.id)],
        ),
    )
    request_event = (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "CUSTOMER_RETURN_REQUESTED",
            AuditEvent.target_type == "RETURN",
            AuditEvent.target_id == UUID(str(return_record["return_id"])),
        )
        .one()
    )
    assert request_event.outcome == "SUCCEEDED"
    assert request_event.details == {"channel": "ECOM", "item_count": 1}

    FrozenDateTime.current_time = deadline + timedelta(days=1)
    return_id = UUID(str(return_record["return_id"]))
    result = process_return(
        test_db_session,
        customer.id,
        return_id,
    )
    process_event = (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "CUSTOMER_RETURN_PROCESSED",
            AuditEvent.target_type == "RETURN",
            AuditEvent.target_id == return_id,
        )
        .one()
    )
    assert process_event.outcome == "SUCCEEDED"
    assert process_event.details == {
        "amount": "20.00",
        "item_count": 1,
        "payment_status": "REFUNDED",
    }

    assert result["status"] == "COMPLETED"
    assert result["payment_status"] == "REFUNDED"
    assert result["amount"] == "20.00"
    assert inventory_unit.status == "IN_STOCK"
    assert inventory_unit.location_id == location.id
    assert (
        test_db_session.query(Refund)
        .filter(Refund.return_id == return_id)
        .one()
        .method
        == "ORIGINAL_PAYMENT"
    )
    assert (
        test_db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.return_id == return_id,
            InventoryMovement.movement_type == "RETURN",
        )
        .one()
        .to_location_id
        == location.id
    )


def test_customer_order_returns_route_recovers_requested_and_completed_state(
    test_db_session,
) -> None:
    customer, order, _, allocation, _, _ = _create_picked_up_ecom_order(
        test_db_session,
        picked_up_at=datetime.now(timezone.utc),
    )
    return_result = create_return_request(
        test_db_session,
        customer.id,
        order.id,
        CreateReturnRequest(
            reason="Return status recovery test",
            items=[ReturnItemRequest(order_line_unit_id=allocation.id)],
        ),
    )
    return_id = UUID(str(return_result["return_id"]))

    requested_returns = list_customer_order_returns_route(
        order.id,
        customer,
        test_db_session,
    )

    assert len(requested_returns) == 1
    assert requested_returns[0].return_id == return_id
    assert requested_returns[0].status == "REQUESTED"
    assert requested_returns[0].items[0].order_line_unit_id == allocation.id
    assert requested_returns[0].items[0].inspection_result == "PENDING"
    assert requested_returns[0].refunds == []

    process_return(test_db_session, customer.id, return_id)
    completed_returns = list_customer_order_returns_route(
        order.id,
        customer,
        test_db_session,
    )

    assert completed_returns[0].status == "COMPLETED"
    assert completed_returns[0].completed_at is not None
    assert completed_returns[0].items[0].inspection_result == "RESTOCK"
    assert len(completed_returns[0].refunds) == 1
    assert completed_returns[0].refunds[0].amount == "20.00"
    assert completed_returns[0].refunds[0].method == "ORIGINAL_PAYMENT"
    assert completed_returns[0].refunds[0].status == "REFUNDED"


def test_customer_order_returns_route_hides_other_customers_orders(
    test_db_session,
) -> None:
    customer, order, _, _, _, _ = _create_picked_up_ecom_order(
        test_db_session,
        picked_up_at=datetime.now(timezone.utc),
    )
    other_customer = Customer(
        email_ciphertext=f"encrypted-{uuid4().hex}",
        email_lookup_hmac=uuid4().hex,
    )
    test_db_session.add(other_customer)
    test_db_session.flush()

    with pytest.raises(HTTPException) as error:
        list_customer_order_returns_route(
            order.id,
            other_customer,
            test_db_session,
        )

    assert error.value.status_code == 404
    assert error.value.detail == "Order not found"


def test_pickup_return_request_rejects_expired_window(
    test_db_session,
    monkeypatch,
) -> None:
    deadline = datetime(2026, 1, 15, tzinfo=timezone.utc)
    customer, order, _, allocation, _, _ = _create_picked_up_ecom_order(
        test_db_session,
        picked_up_at=deadline - timedelta(days=14),
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

    FrozenDateTime.current_time = deadline + timedelta(seconds=1)
    monkeypatch.setattr(returns_service, "datetime", FrozenDateTime)

    with pytest.raises(ValueError, match="ECOM pickup return window has expired"):
        create_return_request(
            test_db_session,
            customer.id,
            order.id,
            CreateReturnRequest(
                reason="Pickup return after the deadline",
                items=[ReturnItemRequest(order_line_unit_id=allocation.id)],
            ),
        )


def test_pickup_return_request_rejects_uncollected_order(test_db_session) -> None:
    customer, order, _, allocation, _, _ = _create_picked_up_ecom_order(
        test_db_session,
        picked_up_at=None,
        shipment_status="READY_FOR_PICKUP",
        order_status="READY_FOR_PICKUP",
    )

    with pytest.raises(
        ValueError,
        match="Only picked-up store orders can be returned",
    ):
        create_return_request(
            test_db_session,
            customer.id,
            order.id,
            CreateReturnRequest(
                reason="Pickup has not been collected",
                items=[ReturnItemRequest(order_line_unit_id=allocation.id)],
            ),
        )
