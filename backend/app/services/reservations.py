import logging
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.models.inventory_movement import InventoryMovement
from backend.app.models.inventory_unit import InventoryUnit
from backend.app.models.order import Order
from backend.app.models.reservation import Reservation

logger = logging.getLogger(__name__)


def release_expired_reservations(
    db: Session,
) -> dict[str, int]:
    now = datetime.now(timezone.utc)

    candidate_order_ids = (
        db.query(Reservation.order_id)
        .filter(
            Reservation.status == "ACTIVE",
            Reservation.expires_at <= now,
        )
        .distinct()
        .order_by(Reservation.order_id)
        .all()
    )

    released_count = 0

    for (order_id,) in candidate_order_ids:
        order = (
            db.query(Order)
            .filter(Order.id == order_id)
            .with_for_update(skip_locked=True)
            .one_or_none()
        )
        if order is None or order.order_status != "PENDING_PAYMENT":
            continue

        expired_filter = (
            Reservation.order_id == order_id,
            Reservation.status == "ACTIVE",
            Reservation.expires_at <= now,
        )
        expected_reservation_count = (
            db.query(func.count(Reservation.id)).filter(*expired_filter).scalar() or 0
        )
        reservations = (
            db.query(Reservation)
            .filter(*expired_filter)
            .order_by(Reservation.inventory_unit_id, Reservation.id)
            .with_for_update(skip_locked=True)
            .all()
        )
        if not reservations or len(reservations) != expected_reservation_count:
            continue

        inventory_units = (
            db.query(InventoryUnit)
            .filter(
                InventoryUnit.id.in_(
                    [reservation.inventory_unit_id for reservation in reservations]
                )
            )
            .order_by(InventoryUnit.id)
            .with_for_update(skip_locked=True)
            .all()
        )
        units_by_id = {unit.id: unit for unit in inventory_units}
        if (
            len(units_by_id) != len(reservations)
            or any(
                units_by_id.get(reservation.inventory_unit_id) is None
                for reservation in reservations
            )
        ):
            continue

        if any(unit.status != "RESERVED" for unit in inventory_units):
            logger.error(
                "Cannot release expired reservations for order %s: "
                "an inventory unit is not reserved",
                order_id,
            )
            continue

        for reservation in reservations:
            inventory_unit = units_by_id[reservation.inventory_unit_id]
            previous_status = inventory_unit.status
            inventory_unit.status = "IN_STOCK"

            db.add(
                InventoryMovement(
                    inventory_unit_id=inventory_unit.id,
                    movement_type="RELEASE",
                    from_location_id=inventory_unit.location_id,
                    to_location_id=inventory_unit.location_id,
                    from_status=previous_status,
                    to_status="IN_STOCK",
                    order_id=order_id,
                    actor_type="SYSTEM",
                    note="Reservation expired",
                )
            )

            reservation.status = "EXPIRED"
            reservation.released_at = now

        order.order_status = "EXPIRED"
        released_count += len(reservations)

    return {
        "expired_reservations": released_count,
    }
