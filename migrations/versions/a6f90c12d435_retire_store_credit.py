"""retire store credit

Revision ID: a6f90c12d435
Revises: b841c6ad9f20
Create Date: 2026-10-06

"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence, Union
from uuid import UUID, uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "a6f90c12d435"
down_revision: Union[str, Sequence[str], None] = "b841c6ad9f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _assert_no_rows(
    connection: sa.Connection,
    reason: str,
    query: str,
) -> None:
    row_count = connection.scalar(sa.text(query))
    if row_count:
        raise RuntimeError(
            f"Store-credit retirement aborted: {reason} ({row_count} row(s))"
        )


def _preflight(connection: sa.Connection) -> None:
    checks = (
        (
            "unreconciled ledger debits",
            "SELECT count(*) FROM store_credit_ledger WHERE entry_type <> 'CREDIT'",
        ),
        (
            "credits without a return link",
            """
            SELECT count(*)
            FROM store_credit_ledger
            WHERE entry_type = 'CREDIT' AND return_id IS NULL
            """,
        ),
        (
            "credits with inconsistent customer or order links",
            """
            SELECT count(*)
            FROM store_credit_ledger l
            JOIN returns r ON r.id = l.return_id
            WHERE l.entry_type = 'CREDIT'
              AND (
                  l.customer_id <> r.customer_id
                  OR (l.order_id IS NOT NULL AND l.order_id <> r.order_id)
              )
            """,
        ),
        (
            "credits without a linked ECOM order",
            """
            SELECT count(*)
            FROM store_credit_ledger l
            LEFT JOIN returns r ON r.id = l.return_id
            LEFT JOIN orders o ON o.id = r.order_id
            WHERE l.entry_type = 'CREDIT'
              AND (r.id IS NULL OR o.id IS NULL OR o.channel <> 'ECOM')
            """,
        ),
        (
            "credits with an inconsistent currency",
            """
            SELECT count(*)
            FROM store_credit_ledger l
            JOIN returns r ON r.id = l.return_id
            JOIN orders o ON o.id = r.order_id
            WHERE l.entry_type = 'CREDIT'
              AND l.currency <> o.currency
            """,
        ),
        (
            "unsupported payment methods",
            "SELECT count(*) FROM payments WHERE payment_method NOT IN ('CARD', 'CASH')",
        ),
        (
            "unsupported refund methods",
            "SELECT count(*) FROM refunds WHERE method <> 'ORIGINAL_PAYMENT'",
        ),
        (
            "credits without exactly one eligible original payment",
            """
            WITH credit_orders AS (
                SELECT DISTINCT r.order_id
                FROM store_credit_ledger l
                JOIN returns r ON r.id = l.return_id
                WHERE l.entry_type = 'CREDIT'
            )
            SELECT count(*)
            FROM credit_orders c
            WHERE (
                SELECT count(*)
                FROM payments p
                WHERE p.order_id = c.order_id
                  AND p.status IN ('PAID', 'PARTIALLY_REFUNDED')
                  AND p.payment_method IN ('CARD', 'CASH')
            ) <> 1
            """,
        ),
        (
            "credits with an inconsistent payment currency",
            """
            SELECT count(*)
            FROM store_credit_ledger l
            JOIN returns r ON r.id = l.return_id
            JOIN payments p
              ON p.order_id = r.order_id
             AND p.status IN ('PAID', 'PARTIALLY_REFUNDED')
            WHERE l.entry_type = 'CREDIT'
              AND l.currency <> p.currency
            """,
        ),
        (
            "credit returns already linked to refunds",
            """
            SELECT count(*)
            FROM store_credit_ledger l
            JOIN refunds f ON f.return_id = l.return_id
            WHERE l.entry_type = 'CREDIT'
            """,
        ),
        (
            "pending refunds on credit orders",
            """
            SELECT count(DISTINCT f.id)
            FROM refunds f
            JOIN payments p ON p.id = f.payment_id
            JOIN (
                SELECT DISTINCT r.order_id
                FROM store_credit_ledger l
                JOIN returns r ON r.id = l.return_id
                WHERE l.entry_type = 'CREDIT'
            ) c ON c.order_id = p.order_id
            WHERE f.status = 'PENDING'
              AND p.status IN ('PAID', 'PARTIALLY_REFUNDED')
            """,
        ),
        (
            "credits that would exceed an original payment after prior refunds",
            """
            WITH credit_totals AS (
                SELECT r.order_id, sum(l.amount) AS credit_amount
                FROM store_credit_ledger l
                JOIN returns r ON r.id = l.return_id
                WHERE l.entry_type = 'CREDIT'
                GROUP BY r.order_id
            ),
            eligible_payments AS (
                SELECT p.id, p.order_id, p.amount
                FROM payments p
                JOIN credit_totals c ON c.order_id = p.order_id
                WHERE p.status IN ('PAID', 'PARTIALLY_REFUNDED')
                  AND p.payment_method IN ('CARD', 'CASH')
            ),
            prior_refunds AS (
                SELECT payment_id, sum(amount) AS refunded_amount
                FROM refunds
                WHERE status = 'REFUNDED'
                GROUP BY payment_id
            )
            SELECT count(*)
            FROM eligible_payments p
            JOIN credit_totals c ON c.order_id = p.order_id
            LEFT JOIN prior_refunds r ON r.payment_id = p.id
            WHERE coalesce(r.refunded_amount, 0) + c.credit_amount > p.amount
            """,
        ),
    )

    for reason, query in checks:
        _assert_no_rows(connection, reason, query)


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """
        LOCK TABLE store_credit_ledger, refunds, payments, orders, returns
        IN ACCESS EXCLUSIVE MODE
        """
    )
    _preflight(connection)

    credit_returns = connection.execute(
        sa.text(
            """
            SELECT
                r.id AS return_id,
                r.order_id,
                r.customer_id,
                l.currency,
                sum(l.amount) AS credit_amount
            FROM store_credit_ledger l
            JOIN returns r ON r.id = l.return_id
            WHERE l.entry_type = 'CREDIT'
            GROUP BY r.id, r.order_id, r.customer_id, l.currency
            ORDER BY r.id
            """
        )
    ).mappings().all()

    converted_amounts: dict[UUID, Decimal] = {}
    payment_orders: dict[UUID, UUID] = {}
    now = datetime.now(timezone.utc)

    for credit in credit_returns:
        payments = connection.execute(
            sa.text(
                """
                SELECT id, amount, currency
                FROM payments
                WHERE order_id = :order_id
                  AND status IN ('PAID', 'PARTIALLY_REFUNDED')
                  AND payment_method IN ('CARD', 'CASH')
                FOR UPDATE
                """
            ),
            {"order_id": credit["order_id"]},
        ).mappings().all()

        if len(payments) != 1:
            raise RuntimeError(
                "Store-credit retirement aborted: original payment mapping changed "
                "after preflight"
            )

        payment = payments[0]
        if payment["currency"] != credit["currency"]:
            raise RuntimeError(
                "Store-credit retirement aborted: payment currency changed "
                "after preflight"
            )

        payment_id = payment["id"]
        prior_refunds = connection.scalar(
            sa.text(
                """
                SELECT coalesce(sum(amount), 0)
                FROM refunds
                WHERE payment_id = :payment_id AND status = 'REFUNDED'
                """
            ),
            {"payment_id": payment_id},
        )
        converted_amount = converted_amounts.get(payment_id, 0) + credit[
            "credit_amount"
        ]
        if prior_refunds + converted_amount > payment["amount"]:
            raise RuntimeError(
                "Store-credit retirement aborted: original payment refund limit "
                "would be exceeded"
            )

        connection.execute(
            sa.text(
                """
                INSERT INTO refunds (
                    id, return_id, payment_id, amount, method, provider_ref,
                    status, created_at, completed_at
                )
                VALUES (
                    :id, :return_id, :payment_id, :amount, 'ORIGINAL_PAYMENT',
                    :provider_ref, 'REFUNDED', :created_at, :completed_at
                )
                """
            ),
            {
                "id": uuid4(),
                "return_id": credit["return_id"],
                "payment_id": payment_id,
                "amount": credit["credit_amount"],
                "provider_ref": f"sim_store_credit_retirement_{uuid4().hex}",
                "created_at": now,
                "completed_at": now,
            },
        )
        converted_amounts[payment_id] = converted_amount
        payment_orders[payment_id] = credit["order_id"]

    for payment_id, order_id in payment_orders.items():
        payment_amount = connection.scalar(
            sa.text("SELECT amount FROM payments WHERE id = :payment_id"),
            {"payment_id": payment_id},
        )
        refunded_amount = connection.scalar(
            sa.text(
                """
                SELECT coalesce(sum(amount), 0)
                FROM refunds
                WHERE payment_id = :payment_id AND status = 'REFUNDED'
                """
            ),
            {"payment_id": payment_id},
        )
        payment_status = (
            "REFUNDED" if refunded_amount == payment_amount else "PARTIALLY_REFUNDED"
        )
        connection.execute(
            sa.text("UPDATE payments SET status = :status WHERE id = :payment_id"),
            {"status": payment_status, "payment_id": payment_id},
        )
        connection.execute(
            sa.text("UPDATE orders SET payment_status = :status WHERE id = :order_id"),
            {"status": payment_status, "order_id": order_id},
        )

    connection.execute(
        sa.text(
            """
            UPDATE returns
            SET requested_outcome = 'REFUND',
                completed_outcome = CASE
                    WHEN completed_outcome IS NULL THEN NULL
                    ELSE 'REFUND'
                END
            WHERE requested_outcome <> 'REFUND'
               OR completed_outcome IS NOT NULL
                  AND completed_outcome <> 'REFUND'
            """
        )
    )

    op.drop_table("store_credit_ledger")

    op.drop_constraint(
        "ck_payments_payment_method",
        "payments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_payments_payment_method",
        "payments",
        "payment_method IN ('CARD', 'CASH')",
    )

    op.drop_constraint("ck_refunds_method", "refunds", type_="check")
    op.create_check_constraint(
        "ck_refunds_method",
        "refunds",
        "method = 'ORIGINAL_PAYMENT'",
    )

    op.drop_constraint(
        "ck_returns_requested_outcome",
        "returns",
        type_="check",
    )
    op.create_check_constraint(
        "ck_returns_requested_outcome",
        "returns",
        "requested_outcome = 'REFUND'",
    )


def downgrade() -> None:
    raise RuntimeError(
        "This migration converts store-credit balances to refunds and drops their "
        "ledger. Restore from a verified backup instead of downgrading."
    )
