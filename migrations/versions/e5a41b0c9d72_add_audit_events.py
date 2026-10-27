"""Add high-risk audit events."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a41b0c9d72"
down_revision: str | Sequence[str] | None = "c7f48d21a063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("customer_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("staff_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("action_code", sa.String(length=100), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=True),
        sa.Column("target_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "actor_type IN ('CUSTOMER', 'STAFF', 'SYSTEM', 'ANONYMOUS')",
            name="ck_audit_events_actor_type",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "outcome IN ('SUCCEEDED', 'FAILED')",
            name="ck_audit_events_outcome",
        ),
        sa.CheckConstraint(
            "("
            "outcome = 'SUCCEEDED' AND error_code IS NULL"
            ") OR ("
            "outcome = 'FAILED' AND error_code IS NOT NULL"
            ")",
            name="ck_audit_events_error_code",
        ),
        sa.CheckConstraint(
            "("
            "target_type IS NULL AND target_id IS NULL"
            ") OR ("
            "target_type IS NOT NULL AND target_id IS NOT NULL"
            ")",
            name="ck_audit_events_target_reference",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_audit_events_customer_id_customers",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["staff_user_id"],
            ["staff_users.id"],
            name="fk_audit_events_staff_user_id_staff_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_events_customer_id",
        "audit_events",
        ["customer_id"],
    )
    op.create_index(
        "ix_audit_events_staff_user_id",
        "audit_events",
        ["staff_user_id"],
    )
    op.create_index(
        "ix_audit_events_action_created_at",
        "audit_events",
        ["action_code", "created_at"],
    )
    op.create_index(
        "ix_audit_events_target_type_target_id",
        "audit_events",
        ["target_type", "target_id"],
    )
    op.create_index(
        "uq_audit_events_terminal_failure_target",
        "audit_events",
        ["action_code", "target_type", "target_id"],
        unique=True,
        postgresql_where=sa.text(
            "outcome = 'FAILED' AND action_code IN "
            "('SYSTEM_PICKUP_EXPIRY', 'SYSTEM_LATE_PAYMENT_REFUND', "
            "'RETURN_PROCESSING_INTEGRITY_FAILURE')"
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_audit_events = connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM audit_events)")
    )
    if has_audit_events:
        raise RuntimeError("Cannot drop audit history while audit events exist")

    op.drop_index(
        "uq_audit_events_terminal_failure_target",
        table_name="audit_events",
    )
    op.drop_index(
        "ix_audit_events_target_type_target_id",
        table_name="audit_events",
    )
    op.drop_index(
        "ix_audit_events_action_created_at",
        table_name="audit_events",
    )
    op.drop_index(
        "ix_audit_events_staff_user_id",
        table_name="audit_events",
    )
    op.drop_index(
        "ix_audit_events_customer_id",
        table_name="audit_events",
    )
    op.drop_table("audit_events")
