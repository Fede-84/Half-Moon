from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base


class PosOrderClaim(Base):
    __tablename__ = "pos_order_claims"

    __table_args__ = (
        CheckConstraint(
            "failed_attempt_count BETWEEN 0 AND 5",
            name="ck_pos_order_claims_failed_attempt_count",
        ),
        CheckConstraint(
            "("
            "last_reset_at IS NULL "
            "AND last_reset_by_staff_id IS NULL"
            ") OR ("
            "last_reset_at IS NOT NULL "
            "AND last_reset_by_staff_id IS NOT NULL"
            ")",
            name="ck_pos_order_claims_reset_metadata",
        ),
        UniqueConstraint(
            "order_id",
            name="uq_pos_order_claims_order_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    order_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "orders.id",
            name="fk_pos_order_claims_order_id_orders",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    claim_code_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    failed_attempt_count: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default="0",
    )

    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    receipt_delivery_acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_reset_by_staff_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "staff_users.id",
            name="fk_pos_order_claims_last_reset_by_staff_id_staff_users",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
