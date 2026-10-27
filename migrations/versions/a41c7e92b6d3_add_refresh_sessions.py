"""Add database-backed refresh sessions and token history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a41c7e92b6d3"
down_revision: str | Sequence[str] | None = "d8c72a4f91be"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("staff_user_id", sa.Uuid(), nullable=True),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "current_generation",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(customer_id IS NOT NULL AND staff_user_id IS NULL) "
            "OR (customer_id IS NULL AND staff_user_id IS NOT NULL)",
            name="ck_refresh_sessions_single_owner",
        ),
        sa.CheckConstraint(
            "current_generation >= 0",
            name="ck_refresh_sessions_generation_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["staff_user_id"],
            ["staff_users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_refresh_sessions_customer_id",
        "refresh_sessions",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_refresh_sessions_staff_user_id",
        "refresh_sessions",
        ["staff_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_refresh_sessions_expires_at",
        "refresh_sessions",
        ["expires_at"],
        unique=False,
    )

    op.create_table(
        "refresh_session_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('ACTIVE', 'ROTATED')",
            name="ck_refresh_session_tokens_state",
        ),
        sa.CheckConstraint(
            "generation >= 0",
            name="ck_refresh_session_tokens_generation_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["refresh_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "generation",
            name="uq_refresh_session_tokens_session_generation",
        ),
    )
    op.create_index(
        "ix_refresh_session_tokens_session_id",
        "refresh_session_tokens",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "uq_refresh_session_tokens_token_hash",
        "refresh_session_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "uq_refresh_session_tokens_one_active",
        "refresh_session_tokens",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("state = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_refresh_session_tokens_one_active",
        table_name="refresh_session_tokens",
    )
    op.drop_index(
        "uq_refresh_session_tokens_token_hash",
        table_name="refresh_session_tokens",
    )
    op.drop_index(
        "ix_refresh_session_tokens_session_id",
        table_name="refresh_session_tokens",
    )
    op.drop_table("refresh_session_tokens")

    op.drop_index(
        "ix_refresh_sessions_expires_at",
        table_name="refresh_sessions",
    )
    op.drop_index(
        "ix_refresh_sessions_staff_user_id",
        table_name="refresh_sessions",
    )
    op.drop_index(
        "ix_refresh_sessions_customer_id",
        table_name="refresh_sessions",
    )
    op.drop_table("refresh_sessions")
