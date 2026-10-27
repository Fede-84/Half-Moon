from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base


class InventoryUnit(Base):
    __tablename__ = "inventory_units"

    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'IN_STOCK', "
            "'RESERVED', "
            "'SOLD', "
            "'RETURNED', "
            "'REPAIR', "
            "'DAMAGED', "
            "'LOST'"
            ")",
            name="ck_inventory_units_status",
        ),
        CheckConstraint(
            "("
            "status IN ('SOLD', 'LOST') "
            "AND location_id IS NULL"
            ") OR ("
            "status NOT IN ('SOLD', 'LOST') "
            "AND location_id IS NOT NULL"
            ")",
            name="ck_inventory_units_location_by_status",
        ),
        Index(
            "ix_inventory_units_variant_status_location",
            "variant_id",
            "status",
            "location_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    variant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False,
    )

    serial_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
    )

    location_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="IN_STOCK",
        server_default="IN_STOCK",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
