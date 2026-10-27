from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base


class OrderAddress(Base):
    __tablename__ = "order_addresses"

    __table_args__ = (
        CheckConstraint(
            "address_type IN ('SHIPPING', 'BILLING')",
            name="ck_order_addresses_address_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    order_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    address_type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    recipient_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    address_line_1: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    address_line_2: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    city: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    region: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    postal_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    country_code: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
    )

    phone: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
