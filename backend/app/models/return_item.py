from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base


class ReturnItem(Base):
    __tablename__ = "return_items"

    __table_args__ = (
        CheckConstraint(
            "inspection_result IN ('PENDING', 'RESTOCK', 'REPAIR', 'DAMAGED')",
            name="ck_return_items_inspection_result",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    return_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("returns.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    order_line_unit_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("order_line_units.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )

    inspection_result: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="PENDING",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
