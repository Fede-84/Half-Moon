from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"

    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('CUSTOMER', 'STAFF', 'SYSTEM', 'ANONYMOUS')",
            name="ck_audit_events_actor_type",
        ),
        CheckConstraint(
            "("
            "actor_type = 'CUSTOMER' AND customer_id IS NOT NULL "
            "AND staff_user_id IS NULL"
            ") OR ("
            "actor_type = 'STAFF' AND customer_id IS NULL "
            "AND staff_user_id IS NOT NULL"
            ") OR ("
            "actor_type IN ('SYSTEM', 'ANONYMOUS') "
            "AND customer_id IS NULL AND staff_user_id IS NULL"
            ")",
            name="ck_audit_events_actor_reference",
        ),
        CheckConstraint(
            "outcome IN ('SUCCEEDED', 'FAILED')",
            name="ck_audit_events_outcome",
        ),
        CheckConstraint(
            "("
            "outcome = 'SUCCEEDED' AND error_code IS NULL"
            ") OR ("
            "outcome = 'FAILED' AND error_code IS NOT NULL"
            ")",
            name="ck_audit_events_error_code",
        ),
        CheckConstraint(
            "("
            "target_type IS NULL AND target_id IS NULL"
            ") OR ("
            "target_type IS NOT NULL AND target_id IS NOT NULL"
            ")",
            name="ck_audit_events_target_reference",
        ),
        Index(
            "ix_audit_events_action_created_at",
            "action_code",
            "created_at",
        ),
        Index(
            "ix_audit_events_target_type_target_id",
            "target_type",
            "target_id",
        ),
        Index(
            "uq_audit_events_terminal_failure_target",
            "action_code",
            "target_type",
            "target_id",
            unique=True,
            postgresql_where=text(
                "outcome = 'FAILED' AND action_code IN "
                "('SYSTEM_PICKUP_EXPIRY', 'SYSTEM_LATE_PAYMENT_REFUND', "
                "'RETURN_PROCESSING_INTEGRITY_FAILURE')"
            ),
            sqlite_where=text(
                "outcome = 'FAILED' AND action_code IN "
                "('SYSTEM_PICKUP_EXPIRY', 'SYSTEM_LATE_PAYMENT_REFUND', "
                "'RETURN_PROCESSING_INTEGRITY_FAILURE')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    actor_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    customer_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    staff_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("staff_users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    action_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    outcome: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    target_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    target_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )

    error_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    correlation_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    details: Mapped[dict[str, object] | None] = mapped_column(
        JSON,
        nullable=True,
    )
