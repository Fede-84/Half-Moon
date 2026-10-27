from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"

    __table_args__ = (
        CheckConstraint(
            "movement_type IN ("
            "'RECEIPT', "
            "'TRANSFER', "
            "'RESERVATION', "
            "'RELEASE', "
            "'SALE', "
            "'RETURN', "
            "'PICKUP_EXPIRY', "
            "'REPAIR', "
            "'DAMAGE', "
            "'LOSS'"
            ")",
            name="ck_inventory_movements_movement_type",
        ),
        CheckConstraint(
            "actor_type IN ('SYSTEM', 'STAFF')",
            name="ck_inventory_movements_actor_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    inventory_unit_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("inventory_units.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    movement_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    from_location_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=True,
    )

    to_location_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=True,
    )

    from_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    to_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    order_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "orders.id",
            name="fk_inventory_movements_order_id_orders",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    return_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "returns.id",
            name="fk_inventory_movements_return_id_returns",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    actor_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    staff_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("staff_users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    note: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
