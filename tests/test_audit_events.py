from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from backend.app.models.inventory_unit import InventoryUnit
from backend.app.models.location import Location
from backend.app.models.audit_event import AuditEvent
from backend.app.models.product import Product
from backend.app.models.product_variant import ProductVariant
from backend.app.models.staff_user import StaffUser
from backend.app.schemas.inventory import InventoryTransferRequest
from backend.app.services.audit import record_audit_event
from backend.app.services.staff_operations import transfer_inventory_units


def test_audit_event_rejects_unapproved_details(test_db_session: Session) -> None:
    with pytest.raises(ValueError, match="unsupported field"):
        record_audit_event(
            test_db_session,
            actor_type="SYSTEM",
            action_code="SYSTEM_LATE_PAYMENT_REFUND",
            outcome="SUCCEEDED",
            target_type="ORDER",
            target_id=uuid4(),
            details={"provider_payload": "must not be stored"},
        )

    assert not test_db_session.new


def test_audit_event_validates_actor_and_outcome_values(
    test_db_session: Session,
) -> None:
    with pytest.raises(ValueError, match="customer reference"):
        record_audit_event(
            test_db_session,
            actor_type="CUSTOMER",
            action_code="CUSTOMER_POS_ORDER_CLAIM",
            outcome="SUCCEEDED",
            target_type="ORDER",
            target_id=uuid4(),
        )

    assert not test_db_session.new


def test_terminal_failure_audit_event_is_deduplicated(
    test_db_session: Session,
) -> None:
    shipment_id = uuid4()
    for _ in range(2):
        record_audit_event(
            test_db_session,
            actor_type="SYSTEM",
            action_code="SYSTEM_PICKUP_EXPIRY",
            outcome="FAILED",
            target_type="SHIPMENT",
            target_id=shipment_id,
            error_code="PICKUP_EXPIRY_PAYMENT_COUNT",
        )

    assert len(test_db_session.new) == 1
    test_db_session.commit()

    record_audit_event(
        test_db_session,
        actor_type="SYSTEM",
        action_code="SYSTEM_PICKUP_EXPIRY",
        outcome="FAILED",
        target_type="SHIPMENT",
        target_id=shipment_id,
        error_code="PICKUP_EXPIRY_PAYMENT_COUNT",
    )
    test_db_session.commit()

    assert (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "SYSTEM_PICKUP_EXPIRY",
            AuditEvent.outcome == "FAILED",
            AuditEvent.target_type == "SHIPMENT",
            AuditEvent.target_id == shipment_id,
        )
        .count()
        == 1
    )


def test_staff_inventory_transfer_and_audit_event_commit_together(
    test_db_session: Session,
) -> None:
    suffix = uuid4().hex
    product = Product(
        name="Audit Transfer Test",
        slug=f"audit-transfer-{suffix}",
        description="Transfer audit test",
        category="NECKLACE",
        image_url="https://example.com/audit-transfer.png",
    )
    source = Location(
        code=f"AUD-SRC-{suffix[:12]}",
        name="Audit Source",
        location_type="WAREHOUSE",
    )
    destination = Location(
        code=f"AUD-DST-{suffix[:12]}",
        name="Audit Destination",
        location_type="STORE",
    )
    staff = StaffUser(
        email_ciphertext=f"encrypted-{suffix}",
        email_lookup_hmac=suffix,
        first_name_ciphertext="encrypted-first-name",
        last_name_ciphertext="encrypted-last-name",
        password_hash="test-password-hash",
        role="STORE_MANAGER",
        account_status="ACTIVE",
    )
    test_db_session.add_all([product, source, destination, staff])
    test_db_session.flush()

    variant = ProductVariant(
        product_id=product.id,
        material="Silver",
        color="Silver",
        size_label="Standard",
        sku=f"AUDIT-{suffix}",
        ean=f"{uuid4().int % 10**13:013d}",
        retail_price=Decimal("20.00"),
    )
    test_db_session.add(variant)
    test_db_session.flush()

    inventory_unit = InventoryUnit(
        variant_id=variant.id,
        serial_number=f"AUDIT-{suffix}",
        location_id=source.id,
        status="IN_STOCK",
    )
    test_db_session.add(inventory_unit)
    test_db_session.flush()

    result = transfer_inventory_units(
        test_db_session,
        InventoryTransferRequest(
            variant_id=variant.id,
            source_location_id=source.id,
            destination_location_id=destination.id,
            quantity=1,
        ),
        staff.id,
    )

    assert result["moved_quantity"] == 1
    assert inventory_unit.location_id == destination.id
    transfer_event = (
        test_db_session.query(AuditEvent)
        .filter(
            AuditEvent.action_code == "STAFF_INVENTORY_TRANSFER",
            AuditEvent.target_type == "PRODUCT_VARIANT",
            AuditEvent.target_id == variant.id,
        )
        .one()
    )
    assert transfer_event.actor_type == "STAFF"
    assert transfer_event.staff_user_id == staff.id
    assert transfer_event.outcome == "SUCCEEDED"
    assert transfer_event.details == {
        "from_location": source.code,
        "to_location": destination.code,
        "quantity": 1,
    }
