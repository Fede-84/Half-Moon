from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base


class Order(Base):
    __tablename__ = "orders"

    __table_args__ = (
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_orders_idempotency_key_hash",
        ),
        CheckConstraint(
            "channel IN ('ECOM', 'POS')",
            name="ck_orders_channel",
        ),
        CheckConstraint(
            "(channel = 'ECOM' AND fulfillment_method IS NOT NULL "
            "AND fulfillment_method IN ('SHIPPING', 'STORE_PICKUP')) "
            "OR (channel = 'POS' AND fulfillment_method IS NULL)",
            name="ck_orders_fulfillment_method",
        ),
        CheckConstraint(
            "order_status IN "
            "('PENDING_PAYMENT', 'PAID', 'CANCELLED', 'EXPIRED', "
            "'READY_TO_SHIP', 'READY_FOR_PICKUP', 'SHIPPED', 'DELIVERED', "
            "'PICKED_UP', 'COMPLETED')",
            name="ck_orders_order_status",
        ),
        CheckConstraint(
            "payment_status IN "
            "('UNPAID', 'PAID', 'FAILED', 'PARTIALLY_REFUNDED', 'REFUNDED')",
            name="ck_orders_payment_status",
        ),
        CheckConstraint(
            "subtotal_gross >= 0 "
            "AND discount_total_gross >= 0 "
            "AND taxable_gross >= 0 "
            "AND net_total >= 0 "
            "AND vat_total >= 0 "
            "AND total_gross >= 0",
            name="ck_orders_amounts_non_negative",
        ),
        CheckConstraint(
            "standard_vat_rate >= 0 "
            "AND standard_vat_rate <= 100 "
            "AND applied_vat_rate >= 0 "
            "AND applied_vat_rate <= 100",
            name="ck_orders_vat_rates_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    order_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        unique=True,
    )

    idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    request_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    customer_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    channel: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    fulfillment_method: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    order_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="PENDING_PAYMENT",
    )

    payment_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="UNPAID",
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        server_default="EUR",
    )

    subtotal_gross: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    discount_total_gross: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    taxable_gross: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    net_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    vat_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    total_gross: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    standard_vat_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
    )

    applied_vat_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
    )

    tax_free: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )

    tax_free_reason: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
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
