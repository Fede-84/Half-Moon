"""add notifications

Revision ID: b841c6ad9f20
Revises: 3e64f262f21b
Create Date: 2026-10-05

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b841c6ad9f20"
down_revision: Union[str, Sequence[str], None] = "3e64f262f21b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("notification_type", sa.String(length=50), nullable=False),
        sa.Column("recipient_email_ciphertext", sa.Text(), nullable=False),
        sa.Column("payload_ciphertext", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'SENT', 'FAILED')",
            name="ck_notifications_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_notifications_attempt_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_notifications_customer_id"),
        "notifications",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notifications_order_id"),
        "notifications",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_status_next_attempt_at",
        "notifications",
        ["status", "next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notifications_status_next_attempt_at",
        table_name="notifications",
    )
    op.drop_index(
        op.f("ix_notifications_order_id"),
        table_name="notifications",
    )
    op.drop_index(
        op.f("ix_notifications_customer_id"),
        table_name="notifications",
    )
    op.drop_table("notifications")
