from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"

    __table_args__ = (
        CheckConstraint(
            "(customer_id IS NOT NULL AND staff_user_id IS NULL) "
            "OR (customer_id IS NULL AND staff_user_id IS NOT NULL)",
            name="ck_refresh_sessions_single_owner",
        ),
        CheckConstraint(
            "current_generation >= 0",
            name="ck_refresh_sessions_generation_nonnegative",
        ),
        Index("ix_refresh_sessions_customer_id", "customer_id"),
        Index("ix_refresh_sessions_staff_user_id", "staff_user_id"),
        Index("ix_refresh_sessions_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    customer_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=True,
    )

    staff_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("staff_users.id", ondelete="CASCADE"),
        nullable=True,
    )

    csrf_token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    current_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class RefreshSessionToken(Base):
    __tablename__ = "refresh_session_tokens"

    __table_args__ = (
        CheckConstraint(
            "state IN ('ACTIVE', 'ROTATED')",
            name="ck_refresh_session_tokens_state",
        ),
        CheckConstraint(
            "generation >= 0",
            name="ck_refresh_session_tokens_generation_nonnegative",
        ),
        UniqueConstraint(
            "session_id",
            "generation",
            name="uq_refresh_session_tokens_session_generation",
        ),
        Index(
            "uq_refresh_session_tokens_token_hash",
            "token_hash",
            unique=True,
        ),
        Index(
            "uq_refresh_session_tokens_one_active",
            "session_id",
            unique=True,
            postgresql_where=text("state = 'ACTIVE'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    session_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("refresh_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    state: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
