from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base


class OrderLine(Base):
    __tablename__ = "order_lines"

    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="ck_order_lines_quantity_positive",
        ),
        CheckConstraint(
            "discount_gross >= 0",
            name="ck_order_lines_discount_non_negative",
        ),
        CheckConstraint(
            "total_gross >= 0",
            name="ck_order_lines_total_non_negative",
        ),
        CheckConstraint(
            "retail_price_gross >= 0 "
            "AND taxable_gross >= 0 "
            "AND net_amount >= 0 "
            "AND vat_rate >= 0 "
            "AND vat_rate <= 100 "
            "AND vat_amount >= 0 "
            "AND total_gross >= 0",
            name="ck_order_lines_amounts_valid",
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

    variant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    quantity: Mapped[int] = mapped_column(
        nullable=False,
    )

    product_name_snapshot: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    variant_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
    )

    sku_snapshot: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    ean_snapshot: Mapped[str] = mapped_column(
        String(13),
        nullable=False,
    )

    retail_price_gross: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    discount_gross: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        server_default="0",
    )

    taxable_gross: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    net_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    vat_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
    )

    vat_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    total_gross: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
