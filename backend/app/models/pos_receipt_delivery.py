from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base


class PosReceiptDelivery(Base):
    __tablename__ = "pos_receipt_deliveries"

    __table_args__ = (
        UniqueConstraint(
            "order_id",
            name="uq_pos_receipt_deliveries_order_id",
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
            "pos_order_claims.order_id",
            name="fk_pos_receipt_deliveries_order_id_pos_order_claims",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    encrypted_claim_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
