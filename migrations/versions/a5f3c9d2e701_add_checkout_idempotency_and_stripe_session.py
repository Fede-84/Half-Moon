"""add checkout idempotency and Stripe session persistence

Revision ID: a5f3c9d2e701
Revises: 818ca3c44540
Create Date: 2026-10-02

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a5f3c9d2e701"
down_revision: Union[str, Sequence[str], None] = "818ca3c44540"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_orders_idempotency_key_hash",
        "orders",
        ["idempotency_key_hash"],
    )
    op.add_column(
        "payments",
        sa.Column(
            "provider_checkout_session_id",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.add_column(
        "payments",
        sa.Column("checkout_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column(
            "checkout_session_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_payments_provider_checkout_session_id",
        "payments",
        ["provider_checkout_session_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_payments_provider_checkout_session_id",
        "payments",
        type_="unique",
    )
    op.drop_column("payments", "checkout_session_expires_at")
    op.drop_column("payments", "checkout_url")
    op.drop_column("payments", "provider_checkout_session_id")
    op.drop_constraint(
        "uq_orders_idempotency_key_hash",
        "orders",
        type_="unique",
    )
    op.drop_column("orders", "request_fingerprint")
    op.drop_column("orders", "idempotency_key_hash")
