-- Read-only report for the store-credit retirement migration.
-- First confirm the returned database identity and Alembic revision.
-- Expected pre-migration revision: b841c6ad9f20.
-- Run only against the intended production database using a read-only role.

BEGIN TRANSACTION READ ONLY;

SELECT
    current_database() AS database_name,
    current_user AS role_name,
    host(inet_server_addr()) AS server_address,
    inet_server_port() AS server_port;

SELECT version_num
FROM alembic_version
ORDER BY version_num;

SELECT
    to_regclass('public.store_credit_ledger') IS NOT NULL AS has_ledger_table,
    to_regclass('public.returns') IS NOT NULL AS has_returns_table,
    to_regclass('public.orders') IS NOT NULL AS has_orders_table,
    to_regclass('public.payments') IS NOT NULL AS has_payments_table,
    to_regclass('public.refunds') IS NOT NULL AS has_refunds_table;

SELECT
    entry_type,
    currency,
    count(*) AS row_count,
    sum(amount) AS gross_amount
FROM store_credit_ledger
GROUP BY entry_type, currency
ORDER BY entry_type, currency;

SELECT
    'return_outcomes_to_normalize' AS check_name,
    count(*) AS row_count
FROM returns
WHERE requested_outcome <> 'REFUND'
   OR (
       completed_outcome IS NOT NULL
       AND completed_outcome <> 'REFUND'
   );

WITH checks AS (
    SELECT
        'unreconciled_ledger_debits' AS check_name,
        count(*) AS issue_count
    FROM store_credit_ledger
    WHERE entry_type <> 'CREDIT'

    UNION ALL

    SELECT
        'credits_without_return_link',
        count(*)
    FROM store_credit_ledger
    WHERE entry_type = 'CREDIT' AND return_id IS NULL

    UNION ALL

    SELECT
        'inconsistent_customer_or_order_links',
        count(*)
    FROM store_credit_ledger l
    JOIN returns r ON r.id = l.return_id
    WHERE l.entry_type = 'CREDIT'
      AND (
          l.customer_id <> r.customer_id
          OR (l.order_id IS NOT NULL AND l.order_id <> r.order_id)
      )

    UNION ALL

    SELECT
        'credits_without_linked_ecom_order',
        count(*)
    FROM store_credit_ledger l
    LEFT JOIN returns r ON r.id = l.return_id
    LEFT JOIN orders o ON o.id = r.order_id
    WHERE l.entry_type = 'CREDIT'
      AND (r.id IS NULL OR o.id IS NULL OR o.channel <> 'ECOM')

    UNION ALL

    SELECT
        'ledger_order_currency_mismatches',
        count(*)
    FROM store_credit_ledger l
    JOIN returns r ON r.id = l.return_id
    JOIN orders o ON o.id = r.order_id
    WHERE l.entry_type = 'CREDIT'
      AND l.currency <> o.currency

    UNION ALL

    SELECT
        'unsupported_payment_methods',
        count(*)
    FROM payments
    WHERE payment_method NOT IN ('CARD', 'CASH')

    UNION ALL

    SELECT
        'unsupported_refund_methods',
        count(*)
    FROM refunds
    WHERE method <> 'ORIGINAL_PAYMENT'

    UNION ALL

    SELECT
        'credits_without_exactly_one_eligible_payment',
        count(*)
    FROM (
        SELECT c.order_id
        FROM (
            SELECT DISTINCT r.order_id
            FROM store_credit_ledger l
            JOIN returns r ON r.id = l.return_id
            WHERE l.entry_type = 'CREDIT'
        ) c
        WHERE (
            SELECT count(*)
            FROM payments p
            WHERE p.order_id = c.order_id
              AND p.status IN ('PAID', 'PARTIALLY_REFUNDED')
              AND p.payment_method IN ('CARD', 'CASH')
        ) <> 1
    ) ambiguous_payments

    UNION ALL

    SELECT
        'credit_payment_currency_mismatches',
        count(*)
    FROM store_credit_ledger l
    JOIN returns r ON r.id = l.return_id
    JOIN payments p
      ON p.order_id = r.order_id
     AND p.status IN ('PAID', 'PARTIALLY_REFUNDED')
    WHERE l.entry_type = 'CREDIT'
      AND l.currency <> p.currency

    UNION ALL

    SELECT
        'credit_returns_already_linked_to_refunds',
        count(*)
    FROM store_credit_ledger l
    JOIN refunds f ON f.return_id = l.return_id
    WHERE l.entry_type = 'CREDIT'

    UNION ALL

    SELECT
        'pending_refunds_on_credit_orders',
        count(DISTINCT f.id)
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

    UNION ALL

    SELECT
        'credits_exceed_original_payment_after_prior_refunds',
        count(*)
    FROM (
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
        SELECT p.id
        FROM eligible_payments p
        JOIN credit_totals c ON c.order_id = p.order_id
        LEFT JOIN prior_refunds r ON r.payment_id = p.id
        WHERE coalesce(r.refunded_amount, 0) + c.credit_amount > p.amount
    ) excessive_refunds
)
SELECT check_name, issue_count
FROM checks
ORDER BY check_name;

ROLLBACK;
