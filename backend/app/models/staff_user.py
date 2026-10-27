from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base


class StaffUser(Base):
    __tablename__ = "staff_users"

    __table_args__ = (
        CheckConstraint(
            "role IN ('SALES_ASSOCIATE', 'STORE_MANAGER')",
            name="ck_staff_users_role",
        ),
        CheckConstraint(
            "account_status IN ('ACTIVE', 'SUSPENDED', 'DISABLED')",
            name="ck_staff_users_account_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    email_ciphertext: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    email_lookup_hmac: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    first_name_ciphertext: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    last_name_ciphertext: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    account_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="ACTIVE",
    )

    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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
