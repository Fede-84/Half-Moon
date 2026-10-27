"""Remove reporting APIs and product acquisition-cost storage."""

from collections.abc import Sequence

from alembic import op

revision: str = "d8c72a4f91be"
down_revision: str | Sequence[str] | None = "f31bd892a6e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_product_variants_acquisition_cost_non_negative",
        "product_variants",
        type_="check",
    )
    op.drop_column("product_variants", "acquisition_cost")


def downgrade() -> None:
    raise RuntimeError(
        "Cannot restore acquisition-cost data removed by this migration"
    )
