"""add inventory unit serial aliases

Revision ID: ba49dc731e20
Revises: c2f40e7a91bd
Create Date: 2026-10-26 15:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ba49dc731e20"
down_revision: Union[str, Sequence[str], None] = "c2f40e7a91bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inventory_unit_serial_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("inventory_unit_id", sa.Uuid(), nullable=False),
        sa.Column("old_serial_number", sa.String(length=100), nullable=False),
        sa.Column("new_serial_number", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["inventory_unit_id"],
            ["inventory_units.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "new_serial_number",
            name="uq_inventory_unit_serial_aliases_new_serial_number",
        ),
        sa.UniqueConstraint(
            "old_serial_number",
            name="uq_inventory_unit_serial_aliases_old_serial_number",
        ),
    )
    op.create_index(
        op.f("ix_inventory_unit_serial_aliases_inventory_unit_id"),
        "inventory_unit_serial_aliases",
        ["inventory_unit_id"],
        unique=False,
    )


def downgrade() -> None:
    alias_count = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM inventory_unit_serial_aliases")
    ).scalar_one()
    if alias_count:
        raise RuntimeError(
            "Serial aliases exist. Restore a pre-conversion database backup "
            "before downgrading this revision."
        )

    op.drop_index(
        op.f("ix_inventory_unit_serial_aliases_inventory_unit_id"),
        table_name="inventory_unit_serial_aliases",
    )
    op.drop_table("inventory_unit_serial_aliases")
