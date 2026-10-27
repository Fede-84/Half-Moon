from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.db.database import commit_or_rollback, flush_or_rollback
from backend.app.models.inventory_movement import InventoryMovement
from backend.app.models.inventory_unit import InventoryUnit
from backend.app.models.location import Location
from backend.app.models.product_variant import ProductVariant
from backend.app.schemas.inventory import InventoryTransferRequest
from backend.app.services.audit import record_audit_event
from backend.app.services.reservations import release_expired_reservations


def transfer_inventory_units(
    db: Session,
    request: InventoryTransferRequest,
    staff_user_id: UUID,
) -> dict[str, object]:
    release_expired_reservations(db)
    flush_or_rollback(db)
    variant = (
        db.query(ProductVariant).filter(ProductVariant.id == request.variant_id).first()
    )

    if variant is None:
        raise ValueError("Product variant not found")

    source = (
        db.query(Location).filter(Location.id == request.source_location_id).first()
    )

    if source is None:
        raise ValueError("Source location not found")

    destination = (
        db.query(Location)
        .filter(Location.id == request.destination_location_id)
        .first()
    )

    if destination is None:
        raise ValueError("Destination location not found")

    if source.id == destination.id:
        raise ValueError("Source and destination must differ")

    available_units = (
        db.query(InventoryUnit)
        .filter(
            InventoryUnit.variant_id == request.variant_id,
            InventoryUnit.location_id == source.id,
            InventoryUnit.status == "IN_STOCK",
        )
        .order_by(InventoryUnit.serial_number)
        .with_for_update()
        .limit(request.quantity)
        .all()
    )

    if len(available_units) < request.quantity:
        raise ValueError(
            f"Only {len(available_units)} units are available at {source.code}"
        )

    serial_numbers: list[str] = []

    for inventory_unit in available_units:
        previous_status = inventory_unit.status
        inventory_unit.location_id = destination.id
        serial_numbers.append(inventory_unit.serial_number)

        db.add(
            InventoryMovement(
                inventory_unit_id=inventory_unit.id,
                movement_type="TRANSFER",
                from_location_id=source.id,
                to_location_id=destination.id,
                from_status=previous_status,
                to_status=previous_status,
                actor_type="STAFF",
                staff_user_id=staff_user_id,
                note=request.note,
            )
        )

    record_audit_event(
        db,
        actor_type="STAFF",
        staff_user_id=staff_user_id,
        action_code="STAFF_INVENTORY_TRANSFER",
        outcome="SUCCEEDED",
        target_type="PRODUCT_VARIANT",
        target_id=request.variant_id,
        details={
            "from_location": source.code,
            "to_location": destination.code,
            "quantity": len(serial_numbers),
        },
    )
    commit_or_rollback(db)

    return {
        "variant_id": str(request.variant_id),
        "from_location": source.code,
        "to_location": destination.code,
        "moved_quantity": len(serial_numbers),
        "serial_numbers": serial_numbers,
    }
