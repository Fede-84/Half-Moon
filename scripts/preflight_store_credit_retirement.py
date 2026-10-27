"""Read-only preflight for the store-credit retirement migration."""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

EXPECTED_REVISION = "b841c6ad9f20"
LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1"}

PREFLIGHT_CHECKS: tuple[tuple[str, str], ...] = (
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
        "credits with inconsistent ledger/order currency",
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
        "credits with inconsistent payment currency",
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
        "credits that would exceed original payment after prior refunds",
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run aggregate reconciliation checks in a read-only PostgreSQL "
            "transaction. No credentials or customer data are printed."
        )
    )
    parser.add_argument(
        "--expected-host",
        required=True,
        help="Expected hostname in the injected production database URL",
    )
    parser.add_argument(
        "--expected-database",
        required=True,
        help="Expected production database name",
    )
    parser.add_argument(
        "--expected-user",
        help="Optional expected PostgreSQL role name",
    )
    return parser.parse_args()


def _is_expected_url(url: URL, args: argparse.Namespace) -> bool:
    if url.drivername != "postgresql+psycopg":
        print("Refusing connection: expected postgresql+psycopg.", file=sys.stderr)
        return False
    normalized_host = url.host.lower() if url.host else None
    if normalized_host in LOCAL_HOSTS or url.database == "half_moon_test_db":
        print("Refusing connection: URL targets a local or test database.", file=sys.stderr)
        return False
    if normalized_host is None or normalized_host.rstrip(".") != args.expected_host.rstrip(
        "."
    ).lower():
        print("Refusing connection: URL host does not match --expected-host.", file=sys.stderr)
        return False
    if url.database != args.expected_database:
        print(
            "Refusing connection: URL database does not match --expected-database.",
            file=sys.stderr,
        )
        return False
    if args.expected_user and url.username != args.expected_user:
        print(
            "Refusing connection: URL role does not match --expected-user.",
            file=sys.stderr,
        )
        return False
    return True


def _main() -> int:
    args = _parse_args()
    database_url = os.environ.get("HALF_MOON_PRODUCTION_DATABASE_URL")
    if not database_url:
        print(
            "Set HALF_MOON_PRODUCTION_DATABASE_URL through the production secret "
            "manager; do not put credentials in command arguments or chat.",
            file=sys.stderr,
        )
        return 2

    try:
        url = make_url(database_url)
    except ArgumentError:
        print("Refusing connection: injected database URL is invalid.", file=sys.stderr)
        return 2

    if not _is_expected_url(url, args):
        return 2

    engine = None
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                identity = connection.execute(
                    text(
                        """
                        SELECT
                            current_database() AS database_name,
                            current_user AS role_name,
                            host(inet_server_addr()) AS server_address,
                            inet_server_port() AS server_port
                        """
                    )
                ).mappings().one()

                if identity["database_name"] != args.expected_database:
                    print("Refusing connection: live database identity mismatch.", file=sys.stderr)
                    return 2
                if args.expected_user and identity["role_name"] != args.expected_user:
                    print("Refusing connection: live role identity mismatch.", file=sys.stderr)
                    return 2

                print(
                    "Verified target: "
                    f"database={identity['database_name']}, "
                    f"role={identity['role_name']}, "
                    f"server_address={identity['server_address']}, "
                    f"server_port={identity['server_port']}"
                )

                tables = connection.execute(
                    text(
                        """
                        SELECT
                            to_regclass('public.alembic_version') IS NOT NULL
                                AS has_revision_table,
                            to_regclass('public.store_credit_ledger') IS NOT NULL
                                AS has_ledger_table,
                            to_regclass('public.returns') IS NOT NULL
                                AS has_returns_table,
                            to_regclass('public.orders') IS NOT NULL
                                AS has_orders_table,
                            to_regclass('public.payments') IS NOT NULL
                                AS has_payments_table,
                            to_regclass('public.refunds') IS NOT NULL
                                AS has_refunds_table
                        """
                    )
                ).mappings().one()
                if not all(tables.values()):
                    print("Preflight blocked: one or more required tables are missing.")
                    return 1

                revisions = connection.execute(
                    text("SELECT version_num FROM alembic_version ORDER BY version_num")
                ).scalars().all()
                print(
                    "Alembic revision(s): "
                    + (", ".join(revisions) if revisions else "(none)")
                )
                if revisions != [EXPECTED_REVISION]:
                    print(
                        "Preflight blocked: expected exactly "
                        f"{EXPECTED_REVISION} before this migration."
                    )

                ledger_totals = connection.execute(
                    text(
                        """
                        SELECT entry_type, currency, count(*) AS row_count,
                               sum(amount) AS gross_amount
                        FROM store_credit_ledger
                        GROUP BY entry_type, currency
                        ORDER BY entry_type, currency
                        """
                    )
                ).mappings().all()
                print("Ledger totals by entry type/currency:")
                if ledger_totals:
                    for row in ledger_totals:
                        print(
                            f"  {row['entry_type']} {row['currency']}: "
                            f"rows={row['row_count']}, gross_amount={row['gross_amount']}"
                        )
                else:
                    print("  (empty)")

                non_refund_outcomes = connection.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM returns
                        WHERE requested_outcome <> 'REFUND'
                           OR (
                               completed_outcome IS NOT NULL
                               AND completed_outcome <> 'REFUND'
                           )
                        """
                    )
                )
                print(
                    "Return outcomes to normalize by migration: "
                    f"{non_refund_outcomes}"
                )

                blocked = False
                for label, query in PREFLIGHT_CHECKS:
                    count = connection.scalar(text(query))
                    status = "BLOCKER" if count else "OK"
                    print(f"{status}: {label}: {count}")
                    blocked = blocked or bool(count)

                if revisions != [EXPECTED_REVISION]:
                    blocked = True

                if blocked:
                    print(
                        "RESULT: BLOCKED. Review the reported rows; do not apply "
                        "the retirement migration."
                    )
                    return 1

                print(
                    "RESULT: No scripted reconciliation blockers found. Review "
                    "these results and take a verified backup before migration."
                )
                return 0
            finally:
                transaction.rollback()
    except SQLAlchemyError as error:
        print(
            f"Preflight failed ({type(error).__name__}); connection details "
            "were suppressed.",
            file=sys.stderr,
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(_main())
