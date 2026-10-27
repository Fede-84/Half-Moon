# POS Checkout Process and Test Checklist

**Implementation and validation update (2026-10-27):** A shared frontend API
error formatter now handles string API details and FastAPI validation detail
arrays across authentication, customer account, POS, checkout, and product
detail screens. It formats field paths and messages without displaying
submitted input values and uses each screen's existing fallback for unusable
details. The Postman post-response example was corrected to parse responses
inside each test callback. Frontend lint, production build, and the focused
four-case formatter behavior check passed. The full backend suite passed all
201 tests against the isolated `half_moon_test_db`; the primary
`half_moon_db` was not touched. Render is selected for tester deployment, but
its service configuration, public domain, and hosted smoke tests remain
unverified. No migration, payment, or deployment was performed.
The workspace has `requirements-prod.lock` and the frontend package lock, but
no `render.yaml`, Dockerfile, Procfile, or GitHub Actions workflow. Docker is
unavailable, so the production lock has not received a real Linux install
check; the wheel-only cross-platform check is inconclusive for source-built
packages. The current workspace is not a Git checkout, so no remote source or
branch could be verified.
The user also read-only verified `half_moon_db` at Alembic revision
`ba49dc731e20` in DBeaver. The credential-free Postman login validation body
reported the expected missing `email` and `password` fields; the HTTP status
was not included in the captured result.
The pre-worker read-only queue snapshot showed no `PENDING` or `PROCESSING`
notifications: 10 were `SENT` and 3 were `FAILED` (one
`ORDER_DELIVERED`, two `ORDER_SHIPPED`). Seven paid shipping orders were
active: four were `PAID` / `READY_TO_SHIP`, and three were
`READY_TO_SHIP` / `READY_TO_SHIP`.

**Controlled worker acceptance (2026-10-27):** After the user confirmed all
seven were controlled test orders and explicitly approved the effects, a
bounded in-process run verified `half_moon_db`, the exact seven-order set,
and zero pending/processing notifications before starting. All seven orders
reached `DELIVERED` with `payment_status=PAID`. The worker sent seven
`ORDER_SHIPPED` and seven `ORDER_DELIVERED` emails; all 14 were marked `SENT`.
The three historical `FAILED` notifications were unchanged. Unrelated
cleanup workers were disabled, the worker tasks shut down gracefully, and no
persistent worker configuration was changed.

**Responsive browser spot-check (2026-10-27):** The shared homepage was
emulated at 320px and 390px viewport widths. Document width matched viewport
width at both sizes; no horizontal overflow was found. This is browser
emulation, not real-device coverage. The earlier route-wide 2026-10-26 sweep
is recorded below.

**Mobile and current validation update (2026-10-26):** Browser checks at 320px
and 390px CSS widths found no horizontal overflow on the storefront, product
details, checkout, customer account/order history, POS claim/purchase pages,
customer `/pos-demo`, authenticated staff `/pos`, or `/pos/orders`. Storefront
bag and filter drawers fit at phone width and close with Escape. The bag's
quantity controls measure 44×44px at phone widths and remain 30×30px at 768px;
the drawer remains within the viewport. Checkout was inspected with a
temporary cart item and the buyer default; no checkout was submitted, and the
temporary item was removed. Staff POS and order operations were inspected
with an authenticated sales-associate account; no lookup or sale was
submitted, and the till basket remained empty.

During browser restoration, one customer-session request returned HTTP 401
and routed to `/login`; the cause was not investigated. The user subsequently
authenticated as staff for the POS checks.

The full backend suite passed all 201 tests using the isolated
`half_moon_test_db` cluster on `127.0.0.1:55432`. The dedicated cluster was
started for testing and left running; the primary `half_moon_db` was not
touched. Frontend lint and the production build passed. The frontend package
has no separate automated test script. No Postman collection, hosted
checkout, live payment, or deployment smoke test was run in this block. A
Stripe auxiliary resource request was logged as aborted during browser route
changes; there were no uncaught browser page errors. The user selected
test-mode-only payments for any future tester deployment; no host or domain
has been selected, and deployment configuration has not been reviewed or
changed.

**Local E-Commerce checkout edge-test update (2026-10-26):** In the preceding
browser checkout run, checkout defaulted to the buyer; near-simultaneous
authenticated submits created one customer order, `HM-2026-000044`; a Stripe
test-card decline displayed the expected decline message; an unpaid checkout
expired; guest success cleared the cart on the same origin; and cancellation
preserved it. Return submission was correctly withheld for a paid
`READY_TO_SHIP` order, and an expired order was not returnable. Three local
test orders were created across that run: `HM-2026-000044` (customer/paid),
one guest order that expired unpaid, and one paid guest order. No return or
refund was processed and no real funds were taken. Those browser-created
records' database identity was not independently verified. These are local
test checks, not deployment smoke tests.

**Unified order lifecycle consolidation (implementation, migration, and
validation complete):** The customer-facing order lifecycle uses
`orders.order_status` for payment, fulfillment, and completion stages;
`payment_status` remains the payment/refund fact and `Shipment.status` remains
operational. ECOM payment persists `PAID`, then the fulfillment worker advances
to `READY_TO_SHIP` or `READY_FOR_PICKUP`; pickup readiness timestamps and the
ready notification are created in that transition. POS sales finish at
`COMPLETED`. Pickup expiry sets `order_status=CANCELLED` and
`payment_status=REFUNDED`. Migration `c2f40e7a91bd` preflights legacy status
combinations, backfills the unified state, and removes
`orders.fulfillment_status`. It was applied to `half_moon_test_db` first and
then `half_moon_db` on 2026-10-23; the main database revision was read-only
verified at `c2f40e7a91bd`. The full backend suite passed 197 tests against
the migrated test database. Frontend lint and production build passed.
A browser/database spot-check on 2026-10-23 confirmed that orders
`HM-2026-000001`, `000002`, `000005`, `000008`, `000010`, `000039`, and
`000040` are `EXPIRED`/`UNPAID` with no shipment, so they expired before
payment and were not delivered. `HM-2026-000022` remains `DELIVERED` in its
order and shipment records with `payment_status=REFUNDED`; `HM-2026-000004`
also remains delivered. The account UI displays the backend `order_status`
without remapping it. No exported Postman collection is available in the
workspace, and no live payment-provider or Postman end-to-end run was
performed.

**Worker readiness review update (2026-10-23):** A fresh aggregate query on
`half_moon_db` showed 10 notifications `SENT`, 3 `FAILED`, and no
`PENDING`/due/expired/`PROCESSING` rows; shipments were 3
`SHIPPING / READY_TO_SHIP` and 9 `SHIPPING / DELIVERED`. The delivered count
differs from the earlier snapshot and the difference is unattributed. The user
confirmed `GMAIL_DELIVERY_WORKER_ENABLED=false`; the per-shipment failure
guard is implemented, and `.venv/bin/python -m pytest -q
tests/test_fulfillment.py` passed all 12 tests. The full backend suite then
passed all 195 tests after the expired-session cleanup test was updated to
account for one pre-existing expired row while still asserting deletion of
its own session and token history.

**Launch worker decision (2026-10-23):** The user wants Gmail active for tester
launch but deferred the controlled worker test until deployment. The latest
read-only snapshot showed 10 `SENT` and 3 `FAILED` notifications, plus 3
`SHIPPING / READY_TO_SHIP` and 9 `SHIPPING / DELIVERED` shipments. All three
failed notifications had one attempt and a generic Gmail authorization or
configuration failure: two notifications for delivered order
`HM-2026-000021` (`ORDER_SHIPPED` and `ORDER_DELIVERED`) and one
`ORDER_SHIPPED` notification for delivered order `HM-2026-000022`. Leave these
historical failed notifications failed; the worker does not automatically
retry them, and replaying them would send stale shipment-status messages.
The three ready shipments are the user's paid ECOM test orders
`HM-2026-000025`, `HM-2026-000026`, and `HM-2026-000027`; the user confirmed
the recipients are controlled test recipients. The worker was not started,
so no emails or shipment states changed during this review. Enabling the
global flag starts both notification and fulfillment workers and will advance
these orders while generating simulated shipping/delivery messages. The
Google OAuth app remains in Testing; review its actual user type, publishing
status, and Gmail-scope verification requirements for sustained launch use.
Recheck the operational queue before enabling only if tester activity changes
these records before deployment.

**Scope clarification (2026-10-23):** Discounts/vouchers and a staff-facing
inventory movement-history API are explicitly out of scope and will not be
implemented. ECOM store-pickup returns are already implemented and tested
(2026-10-09); the earlier note that they were paused is historical and does
not describe current status.

**Status (updated 2026-10-21; later verification recorded below):** POS backend checkout, status, receipt delivery, customer claim handling, staff lookup/returns, manager claim reset, and staff `/pos/orders` are implemented. In addition to the staff terminal, the public `/pos-demo` UI submits cash-only anonymous POS sales to `POST /api/v1/orders/pos-demo`; the backend selects price and shared stock, requires an `Idempotency-Key`, and attributes reservation/sale movements to `SYSTEM`. The public receipt routes return the same code on retries before acknowledgement; the frontend automatically acknowledges it after ten seconds. Verified customers claim the sale at `/account/claim-pos-order`, see claimed POS orders separately at `/account/pos-purchases`, and can request exact-unit returns that are immediately processed by the backend when eligible. Return ownership, active verified-account status, `order_status=COMPLETED`, the inclusive 14-day `paid_at` window, remaining payment balance, and unique unreturned units are checked server-side. Refunds are simulated; restocking returns units to their sale source; return-processing audit events use `SYSTEM`. The POS staff authorization boundary remains intact. No additional public request/quantity caps or low-stock stop are added. Unclaimed demo sales remain paid and units remain sold after the 14-day claim-code expiry; only the encrypted delivery copy is cleaned up. The focused POS, returns, and audit suites passed 96 tests; frontend build and lint passed. Browser acceptance verified the public cash sale, ten-second receipt auto-acknowledgement, customer claim, separate POS history/detail, eligible automatic return, and updated E-Commerce guest-success wording. After the approved reset and reseed of only the isolated test database, the full backend suite passed 193 tests at this POS milestone; the later full backend suite passed 195 tests on 2026-10-23 after the fulfillment-worker guard and expired-session test correction. The isolated database baseline was 4 products, 18 variants, 18,000 inventory units, and 2 demo staff accounts. Local Gmail delivery is disabled, which also prevents the fulfillment worker from starting; ECOM orders therefore remain at `PAID` until that worker runs. Three older order-status notifications remain `FAILED` with the generic Gmail authorization/configuration error. At the 2026-10-21 checkpoint, staff POS card-confirmation browser acceptance still needed test-mode Stripe configuration; this was later completed using the user's local app in the delayed-webhook browser test for `HM-2026-000036` (see below). The 2026-10-26 responsive browser sweep is summarized above; final design approval and deployment remain deferred.**

**Browser acceptance update (2026-10-23):** In the user's local app, the customer claimed cash demo sale `HM-2026-000033` (EUR 189.00), viewed it in the separate POS purchase history/detail, and completed an eligible simulated EUR 189.00 return; available stock was 996 before and after the return. A staff test-mode card sale `HM-2026-000034` (EUR 99.00) produced a `payment_intent.succeeded` event forwarded by Stripe CLI to the local webhook (HTTP 200), and the terminal returned to a blank screen. No real payment was taken. The card sale remains paid and unclaimed, and its unit remains sold. The receipt was not directly observed during this first card test; the later delayed-webhook browser test documented below closed that acceptance gap. These records were created through the user's local API; its configured database identity was not independently verified.

**Gmail OAuth and send update (2026-10-23):** Local Google OAuth completed successfully and the callback saved encrypted authorized-user credentials; the local OAuth setup flag was disabled again after success. Read-only counts from the API database showed zero pending notifications, three failed notifications, three `SHIPPING / READY_TO_SHIP` shipments, and five `SHIPPING / DELIVERED` shipments. The global delivery flag stayed disabled because it starts both notification and fulfillment workers. After explicit approval, exactly one synthetic test email was sent directly through the existing Gmail client to the configured sender mailbox. No queued/customer notification was processed and no shipment was advanced by a worker. This verifies OAuth authorization, credential storage, and the direct Gmail send path only—not queued notification or worker behavior. The Google OAuth app remains in Testing mode. Do not enable global delivery without explicit approval and a separate review of queued work.

**Customer-only tester flow (implementation contract):** Use `/pos-demo` for an anonymous cash-only simulation. Submit only variant IDs and quantities; never accept client-supplied price, customer ID, or card payment. The `Idempotency-Key`, backend stock checks, and existing POS order lifecycle remain authoritative. Receipt claim-code display auto-acknowledges after ten seconds; leaving the page before acknowledgement leaves the existing encrypted delivery copy available for a retry. Claim at `/account/claim-pos-order` after signing in to an active, verified customer account. The claimed order appears only in `/account/pos-purchases`, not E-Commerce history. The customer POS detail submits an exact-unit return to the automatic backend return route; eligible requests are refunded in simulation and restocked atomically. Both original staff routes and manager-only lock reset remain protected. No schema migration is required.

**POS frontend increment (2026-10-19):** The staff-only split terminal reuses the existing catalog/detail APIs, calculates its preview using integer cents, and submits anonymous cash sales with an `Idempotency-Key`. After backend-confirmed completion, it displays the order number and one-time claim code, offers a copy control, and automatically acknowledges the receipt after ten seconds without showing a countdown. Acknowledgement success returns to a blank sale; receipt loading or acknowledgement errors remain visible for recovery. The card increment uses Stripe's Card Element with the public `VITE_STRIPE_PUBLISHABLE_KEY`; the backend remains authoritative for price and sale status, the browser keeps the PaymentIntent client secret in memory, and the receipt waits for the status endpoint to report the signed-webhook-confirmed paid state. A decline can be retried on the same PaymentIntent until reservation expiry. There is no card-cancel endpoint/action, so the UI waits for expiry to release the reservation. Customer lookup remains deferred. Manual browser acceptance completed for test-mode order `HM-2026-000024` at EUR 69.00: the signed `payment_intent.succeeded` webhook returned HTTP 200 and the receipt appeared; no real payment was taken. `npm run build` and `npm run lint` passed after the ten-second timer adjustment. Manual browser acceptance of the cash flow confirmed one sale, receipt display and copy, automatic acknowledgement, and return to a blank sale; no real payment was taken. The one-time claim code is intentionally not recorded here.

**POS post-sale operations implementation:** `/pos/orders` remains nested under the staff-protected route and uses the centralized API client, relative `/api` paths, and the existing CSRF interceptor. Staff can look up claimed POS orders and process refunds for explicitly selected unreturned units. Managers have a separate order-number reset form; that endpoint returns reset status only and does not expose unclaimed order details. Customers can claim from `/account/claim-pos-order`, then view only their own claimed POS orders at `/account/pos-purchases`. `/pos-demo` creates anonymous cash-only sales through the public API, receives a one-time code, and automatically acknowledges the receipt after ten seconds. Eligible customer returns use a dedicated POS submission route and complete as simulated original-payment refunds with source-location restocking and `SYSTEM` audit attribution. The POS purchase list and claim flow stay separate from E-Commerce history; owner-scoped order-detail and return-history reads support both channels, while E-Commerce return submission remains ECOM-only. Five failed claim attempts trigger a 30-second cooldown; claims unlock on a later attempt after expiry, and managers may reset a lock earlier through either the order-number or UUID route. The staff POS card flow remains staff-protected.

**Manual API and automated verification (2026-10-21):** Local Postman/terminal checks passed customer login, refresh, logout, staff login/profile/manager authorization, both cross-role `401` checks, catalog/detail reads, customer POS demo checkout/claim/history/detail/return, and staff cash checkout/receipt acknowledgement/status. The customer demo sale `HM-2026-000031` was returned with a simulated EUR 189.00 refund and its unit restocked. Staff cash sale `HM-2026-000032` remains `PAID` / `COMPLETED` and unclaimed after receipt acknowledgement; unclaimed staff lookup returned `409` without exposing order details. The selected variant's available stock was `997` before the POS checks, returned to `997` after the customer return, and is now `996` after the remaining staff sale. The API database identity for these manual requests was not independently verified; do not attribute these records to the isolated pytest database. No real payment, claim code, access token, or credential was recorded. The exact lookup 409 detail was corrected after the manual check; `.venv/bin/python -m pytest tests/test_pos_checkout.py` passed all 79 tests with the corrected message. The full backend suite, card checkout, Gmail OAuth, and manager reset of a newly locked claim remain untested. No Postman collection export was available; these were individual requests, not a collection run.

**ECOM browser acceptance (2026-10-19):** Guest checkout `HM-2026-000021` and authenticated customer checkout `HM-2026-000022` completed in Stripe test mode; both webhook requests returned HTTP 200, both orders reached `PAID` and mock `DELIVERED`, and the customer order appeared in `/account`. The customer then completed a return for one item from `HM-2026-000022`; the UI recorded a simulated EUR 129.00 refund, with no real Stripe refund. That acceptance identified an unclear required return-reason field. The customer form now requires a reason category (damaged, wrong size, wrong colour, or other), allows additional details, and requires details for "Other" while preserving the existing API reason-string contract. Manual browser acceptance on order `HM-2026-000018` confirmed that blank details are accepted for a standard category, required for "Other", and persisted in return activity as `Other: Test: Other reason details`; the return completed with a simulated EUR 189.00 refund and no real payment-provider refund. A later Stripe test-mode checkout for `HM-2026-000025` reached `PAID`; both signed webhook events returned HTTP 200, but the browser initially showed the backend JSON response. The backend return handlers now redirect to the frontend pages. Manual browser retesting confirmed that authenticated customers reach `/account`, successful guest checkout stays on the branded confirmation page, and guest cancellation reaches the branded cancellation page; neither guest path showed backend JSON. The guest success message was updated to use clearer payment-confirmation wording; its copy-only browser retest, pending at this checkpoint, was later confirmed in the current status above. Gmail authorization was refreshed locally and one `ORDER_DELIVERED` notification was retried successfully. Three older `ORDER_SHIPPED`/`ORDER_DELIVERED` notifications remain `FAILED` with the generic `Gmail authorization or configuration failed` error; this is non-retryable in the worker and does not distinguish authorization from configuration. See the current Gmail status in `docs/architecture.md`.

**Environment snapshot (2026-10-12; verify before reuse):** `half_moon_db` is the
local primary application database and is intended to be the hosted development
database after frontend completion. `half_moon_test_db` is the isolated pytest
database; never run pytest against `half_moon_db`. Both currently run locally.
The focused POS checkout and return-policy tests passed 82 tests on the
isolated test database. A manual API smoke test against `half_moon_db`
successfully completed cash sale `HM-2026-000019`, customer claim, and
staff-assisted return. Read-only verification confirmed `REFUNDED` /
`COMPLETED`, one return inventory movement, one unit restocked at
`PHYSICAL_STORE`, and one successful staff audit event. The `179.00` refund
was simulated; no card payment or real refund occurred. The authorized smoke
test records remain in `half_moon_db`.

The following earlier pre-pickup checkpoint is historical and has been
superseded by the staff-assisted POS return workflow documented below. At that
checkpoint, the pending-order and cash/card checkout services, signed
PaymentIntent webhook processing, staff-protected status and receipt routes,
receipt acknowledgement, customer claim, manager claim-lock reset, and
two-step POS returns were implemented. The focused POS checkout and
return-policy run passed 49 tests. The full project suite passed all 112 tests
after the expiry-cleanup and POS redirect-method changes, before the pickup
changes below. The focused expiry-cleanup regression passed all 4 cases, and
the focused POS PaymentIntent request-parameter test passed 1 case. A
controlled test-mode POS card checkout also succeeded end to end. That
full-suite result was superseded by the 138-test run on 2026-10-09.

User-run focused pickup tests passed: pickup deadline DST cases (2), pickup
progression and status timestamp cases (2), approved order-email formatting
cases (5 before the expiry copy was added), paid-checkout webhook cases (2),
the updated six-case email-formatting test, the one-case expiry-worker
regression, and the one-case normal pickup-progression regression. The pickup
webhook test checks notification count, customer, recipient, payload, and replay
idempotency. The full `test_fulfillment.py` module then passed all 8 tests.
The full project suite subsequently passed 138 tests on 2026-10-09. Live Gmail
delivery of pickup emails has not been verified.

## Stripe Test-Mode Payment Instructions for Testers

For a deployed environment where testers must not make real payments, configure
only Stripe test-mode credentials and the matching test-mode webhook signing
secret; do not use live Stripe credentials. Testers do not need a Stripe
account. In ECOM's hosted Stripe Checkout, use card number
`4242 4242 4242 4242`, any valid future expiry date, any three-digit CVC, and a
postal code if requested. These are test details only and do not move real
funds. See [Stripe's testing documentation](https://docs.stripe.com/testing).

POS card payments currently have backend PaymentIntent support, but no
customer-facing secure card-entry UI. The same test card can be used for POS
only after that UI is implemented and configured with a Stripe test-mode
publishable key.

## Handoff: POS Stripe Test-Mode Validation and Expiry Cleanup (2026-10-08)

This dated handoff is a historical snapshot; later POS return behavior and
current environment terminology are recorded in the current sections above
and below.

- The isolated test database is `half_moon_test_db`, role
  `half_moon_test_user`, loopback port `55432`, at Alembic head
  immediately before the later ECOM pickup-persistence migration. It was
  seeded with 4 products, 18 variants, 18,000 serialized units, and 2 test
  staff accounts. Both local databases have since advanced to verified head
  `c7f48d21a063`; the primary database was not changed during this POS test.
- The first card checkout created POS order `HM-2026-000721`
  (`d11420f9-4d5c-4d5b-b55d-95927405330c`), but no sale completed. That order
  is `EXPIRED` / `UNPAID` / `NOT_STARTED`; its database payment record remains
  `PENDING`.
- The associated Stripe PaymentIntent was in test mode (`livemode=false`).
  Stripe's confirm request log showed `automatic_payment_methods.enabled=true`
  and `allow_redirects=always`; the confirm request omitted `return_url`.
  Stripe rejected confirmation with `invalid_request_error`, explaining that
  a return URL is required when redirect methods are allowed. The intent had
  no reported `last_payment_error` or `next_action`; it was then canceled.
  No `payment_intent.succeeded` webhook was received, and no successful
  payment or sale occurred.
- The POS PaymentIntent creation parameters now set
  `automatic_payment_methods.allow_redirects` to `never`, preserving
  non-redirect payment methods without requiring a return URL. The existing
  mocked POS PaymentIntent parameter test was updated and passed 1 case.
  This change is limited to POS PaymentIntents; no ECOM payment flow changed.
- Expiry cleanup was updated in the POS status, catalog detail, variant stock,
  and then-existing staff inventory summary read path. The staff inventory
  summary API was retired on 2026-10-09; the focused cleanup regression passed
  all 4 cases at the earlier checkpoint.
- The staff status request committed cleanup for the existing order.
  Read-only verification confirmed its reservation is `EXPIRED` with
  `released_at` set, the inventory unit is `IN_STOCK`, active reservations for
  the order are zero, the variant has 1,000 units in stock and zero reserved,
  and one append-only `RELEASE` movement exists.
- After the redirect-method change was loaded, a fresh one-unit POS card
  checkout created order `HM-2026-000727`. The new PaymentIntent was verified
  as test mode, amount `18900` EUR cents, `allow_redirects=never`, and no
  last-payment error. Confirmation with Stripe test method `pm_card_visa`
  returned `succeeded`. The listener received `payment_intent.succeeded` and
  returned HTTP 200.
- The POS status API returned `PAID` / `PAID` / `COMPLETED`. Read-only
  verification on `half_moon_test_db` confirmed payment record `PAID`,
  reservation `COMMITTED`, inventory unit `SOLD`, zero active reservations,
  variant stock of 999 `IN_STOCK`, 0 `RESERVED`, and 1 `SOLD`, plus exactly
  one `SALE` movement. This was a Stripe test-mode payment; no live charge
  was made.
- The successful end-to-end POS card test is complete. The full project suite
  passed all 112 tests after the expiry-cleanup and redirect-method changes.
  Next, continue with ECOM pickup-window, progression, and notification work.
  Keep future payment validation in test mode and use only the isolated test
  database.

## Handoff: ECOM Store-Pickup Progression and Notifications (2026-10-08)

- Successful paid ECOM pickup checkout validates `PHYSICAL_STORE`, records
  `ready_for_pickup_at`, and calculates a seven-calendar-day deadline in
  `Europe/Rome`. It enqueues `ORDER_READY_FOR_PICKUP` in the payment
  transaction; the approved email asks the customer to collect within seven
  days.
- The fulfillment worker advances eligible pickup orders to `PICKED_UP` only
  after the configured mock delay and through the inclusive deadline. It
  updates `picked_up_at` and the order fulfillment status and enqueues
  `ORDER_PICKED_UP`. The authenticated status response includes all three
  pickup timestamps and does not advance fulfillment.
- User-run focused tests passed: DST deadline cases (2), pickup progression
  and status timestamp cases (2), approved order-email formatting cases (5),
  and paid-checkout webhook cases (2). The webhook test verifies the pickup
  notification's count, customer, recipient, payload, and idempotency under
  replay.
- At this 2026-10-08 checkpoint, the recorded 112-test full-suite run predated
  the pickup changes. The full suite subsequently passed 138 tests on
  2026-10-09. Live Gmail delivery of pickup emails has not been verified.
- The approved `ORDER_PICKUP_EXPIRED` email copy is implemented in the
  notification formatter; its focused parameterized formatting test passed
  all 6 cases.
- The expiry worker's focused regression passed 1 case. It verifies that the
  inclusive deadline is respected, the full simulated original-payment refund
  is recorded, sold units return to their source with one append-only
  `PICKUP_EXPIRY` movement each, the notification is enqueued, and a repeated
  worker pass creates no duplicates.
- The existing normal pickup-progression regression passed 1 case after the
  expiry-worker change.
- The full `test_fulfillment.py` module passed all 8 tests after the expiry
  worker change.
- Pickup returns were added to the shared return workflow on 2026-10-09, using
  the approved inclusive 14-day window anchored at `picked_up_at`; the full
  138-test suite passed afterward.

## Repository Cross-Check (Historical POS Milestone Snapshot)

This section records the repository state and validation counts at the initial
POS milestone. The current implementation and test status are recorded at the
top of this checklist and in the active architecture sections above.

The current tree already has foundational POS support:

- `backend/app/models/order.py` permits the `POS` order channel.
- `backend/app/models/payment.py` permits `CARD` and `CASH` payment methods.
- `backend/app/models/order.py` has nullable `paid_at`, and its
  `ck_orders_fulfillment_status` includes `COMPLETED`, matching the migration.
- `backend/app/models/pos_order_claim.py` and
  `backend/app/models/pos_receipt_delivery.py` contain the initial claim and
  encrypted-delivery models.
- `backend/app/schemas/pos.py` contains the initial checkout, status, and
  receipt-delivery request/response schemas.
- `migrations/versions/464acebca437_add_pos_claim_delivery_support.py` contains
  the matching schema change from `a6f90c12d435`. Both
  `half_moon_test_db` and local primary `half_moon_db` are at head
  `c7f48d21a063`.
- `backend/app/services/pos.py` creates idempotent pending POS orders and
  completes cash sales independently of ECOM order/payment services.
- `backend/app/services/pos.py` creates or retrieves a POS PaymentIntent using
  the server-calculated amount and stable Stripe idempotency key.
- `backend/app/services/pos.py` includes a late-payment reservation-safety
  helper that can restore expired reservations only when the exact units
  remain available. Focused tests cover safe restoration, extension of an
  active reservation whose expiry passed, and refusal when a unit is in repair.
- `backend/app/services/payments.py` now has a POS PaymentIntent-event
  validator for supported event status, order/payment linkage, metadata,
  amount, and currency. Tests cover all three supported event statuses and
  reject mismatched status, metadata, amount, currency, and unsupported events.
  Its processor is tested for retryable failure, cancellation and stock
  release, successful sale and replay, and late-success refund when the
  original unit is unavailable. The shared signed webhook route dispatches
  POS PaymentIntent events with `channel="POS"` metadata to this processor and
  acknowledges non-POS PaymentIntent events without sending them through the
  POS processor. Signed-route tests cover all three event types and channel
  isolation.
- `backend/app/main.py` has the shared Stripe webhook route, the
  staff-protected checkout, status, receipt retrieval, receipt acknowledgement,
  and manager claim-reset routes, the customer-authenticated POS claim route,
  and shared customer return-request/process routes for ECOM and POS.
- `backend/app/services/pos.py` implements transaction-neutral reservation
  cleanup during status polling, encrypted receipt delivery and acknowledgement,
  startup/daily expired-copy cleanup, customer claims with verified-account
  checks and five-attempt lockout, and manager-only claim-lock reset.
- `backend/app/services/returns.py` supports POS returns through the shared
  two-step request/process flow. POS requests require order ownership and a
  verified account and must be made through the inclusive 14-day `paid_at`
  deadline. Processing can occur later, validates original sale-movement
  sources before mutation, and commits the selected-unit restock, append-only
  `RETURN` movement, refund, and payment/order status changes together.
- `tests/test_pos_checkout.py` now has 43 passing cases for
  pending orders, store/warehouse allocation, idempotency, cash-sale
  completion/replay, and mocked card PaymentIntent creation/retry and
  configuration failure, late-payment reservation safety, and PaymentIntent
  event validation/processing, signed webhook dispatch, UUID-string
  deterministic multi-variant lock ordering, staff checkout routes, receipt
  cleanup and acknowledgement, customer claims, lockout, manager reset,
  status polling, claim-deadline boundaries, POS return eligibility, partial
  refunds, restocking to both original source locations, and verification at
  request and processing. The combined POS checkout and return-policy run
  passed 49 tests; the full project suite most recently passed 112 tests after
  the expiry-cleanup and POS redirect-method changes.

The ORM model and migration definitions were aligned at the POS milestone,
including the approved `COMPLETED` POS handover state. At that milestone, both
databases were at verified head `c7f48d21a063`. The POS migration was applied to
primary without a backup after a live identity/revision preflight;
post-migration verification confirmed the revision, `orders.paid_at`, the
fulfillment constraint, both POS tables, and the active-reservation partial
unique index. The test database was subsequently upgraded from
`464acebca437`; verification confirmed the same index. Its catalogue and
staff test data are now seeded. The POS migration's downgrade refuses once
claim/delivery data, `paid_at` timestamps, or completed orders exist.

## Verified Transaction, Concurrency, and Authentication Hardening

**Implementation status: complete (100%); latest full suite: 112 passed.**

- `release_expired_reservations` is transaction-neutral and contains no
  internal commit. Mutation services use `commit_or_rollback` and
  `flush_or_rollback`; SQLAlchemy errors trigger an explicit rollback before
  being re-raised.
- Checkout and return workflows validate before mutating inventory or staging
  append-only movement rows. Return totals remain exact `Decimal` values and
  are checked before inventory changes.
- Row-locking flows use the Order -> Payment -> Reservation -> InventoryUnit
  hierarchy. Multi-item ECOM checkout, POS allocation, and return-unit locks
  use deterministic UUID ordering. Expiry cleanup orders candidate work and
  uses `SKIP LOCKED` for row locks to reduce contention.
- PostgreSQL enforces a partial unique index on
  `reservations.inventory_unit_id` only where `status = 'ACTIVE'`. Migration
  `8d72372141d5` creates this index and is an ancestor of verified head
  `c7f48d21a063`; the index is present on both local primary `half_moon_db` and
  isolated `half_moon_test_db`.
- Customer login rejects an account with unverified email before issuing an
  active JWT.

## Cross-Channel Return Policy Change

The approved target removes store credit and the unused exchange outcome from
both ECOM and POS. Returns will refund the original payment method only. Keep
`requested_outcome` for API/audit compatibility but restrict it to `REFUND`;
requests asking for the retired outcomes must fail validation. The existing
store-credit ledger has been removed after the configured outstanding balance
was converted into a simulated original-payment refund. Keep historical
Alembic revisions and dated architecture checkpoints unchanged; removal used a
new forward migration.

The active request schema, return service, and model constraints now enforce
refund-only behavior. Forward migration `a6f90c12d435` was applied to both
configured databases. At that point, both were at revision
`a6f90c12d435`. In this project's terminology, the primary `half_moon_db` is
“production”; the isolated `half_moon_test_db` is “development”/test. The
primary-database preflight found one EUR 189 credit and two return outcomes to
normalize, with no reconciliation blockers. At the next POS-schema milestone,
`464acebca437` was applied and verified on both databases. Current revisions
are listed above. No separately hosted application deployment exists.

## Confirmed POS v1 Decisions

- POS is a demo flow: the app simulates the business steps; it does not depend
  on a real shop, physical terminal, or real staff operation.
- POS routes still require the app's simulated staff authentication; both
  `SALES_ASSOCIATE` and `STORE_MANAGER` may create ordinary POS sales.
- A sale may be anonymous or linked to a customer.
- POS checkout requires an `Idempotency-Key`: retrying the same request with
  the same key returns the original order; reusing the key with a changed
  request is rejected, matching ECOM.
- An anonymous buyer may later register and claim the sale using its order
  number and a one-time `HM-` plus eight-digit code shown once on the simulated
  receipt. Store only an HMAC-SHA-256 digest of the code in a POS-specific
  claim record tied to the order, using a dedicated application secret; a
  successful claim consumes it.
  The code proves ownership for a refund claim;
  it is valid through the inclusive 14-day return deadline and expires after
  that deadline. A registered,
  email-verified account is required before requesting or processing a refund,
  matching the ECOM return identity requirement.
- The POS temporarily stores an encrypted copy of the claim code separately
  from the hash-only claim record until the POS acknowledges receipt delivery,
  then deletes the encrypted copy. If delivery is never acknowledged, the
  copy expires and is deleted after the inclusive 14-day claim deadline by
  startup and daily cleanup sweeps. Expired codes cannot be delivered even
  before a sweep runs. If the customer later loses the receipt, no replacement
  code is issued; the order cannot be claimed without the original code. A
  manager's failed-attempt reset does not replace the code.
- A manager reset clears the failed-attempt count and stores only the latest
  reset timestamp and manager UUID on the claim record; earlier resets are not
  retained as history.
- The shared order lifecycle includes an immutable UTC `paid_at` timestamp,
  set when an ECOM or POS order first becomes paid, using
  `datetime.now(timezone.utc)`.
- Existing paid orders whose exact payment-completion time cannot be recovered
  keep `paid_at` as `NULL`; do not backfill an approximate value. Unpaid orders
  also have `paid_at=NULL`.
- POS returns are allowed for 14 days from successful sale/payment completion,
  inclusive through the deadline, matching ECOM's duration and boundary rule.
- POS returns use a simulated refund to the original payment method only.
  Neither the customer nor simulated staff chooses a store-credit outcome.
- POS inventory prefers `PHYSICAL_STORE` and may fall back to
  `MAIN_WAREHOUSE`. Use one source location for the complete order; split
  fulfillment is outside the documented v1 design.
- POS uses server-side catalog pricing and VAT calculations; no discount or
  voucher input is available in POS or ECOM v1. Existing order discount
  snapshot fields remain zero.
- Choosing cash records the payment as paid immediately.
- Card payment uses Stripe test mode and a PaymentIntent API flow, not hosted
  Stripe Checkout or physical Stripe Terminal hardware. The future POS screen
  uses Stripe's secure card-entry UI; the backend relies on the signed
  `payment_intent.succeeded` webhook as authoritative confirmation before
  committing the sale. While waiting, the POS polls a staff-protected order
  status endpoint; it does not issue a final receipt before the webhook commits
  the sale.
- A declined card attempt leaves the order pending and its reservation active
  for another attempt until the existing three-minute reservation expiry.
  Stripe retains the attempt details; do not create a local `Payment` row for
  each decline, matching ECOM.
- A completed POS sale records `order_status=COMPLETED` and
  `payment_status=PAID`, and sold serialized units as `SOLD`. An ordinary POS
  transaction does not use the later-collection pickup workflow. `PICKED_UP`
  is reserved for an order collected after being sent to a pickup location.
- Python 3.14, UUID identifiers for catalog/inventory lookups, and explicit
  `datetime.now(timezone.utc)` timestamping remain requirements.

## Proposed Process

1. A simulated staff user starts a POS checkout and submits variant UUIDs and
   positive quantities with an `Idempotency-Key`.
2. The service finds and transactionally reserves exact serialized units,
   preferring store stock and falling back to warehouse stock. The full order
   must be available from one location.
3. The order is anonymous unless a customer is selected. The system calculates
   the amount from trusted catalog/order data, not client-supplied prices.
4. For cash, the backend records payment and commits the sale in one
   transaction. For card, the backend creates a Stripe test-mode PaymentIntent;
   the client confirms it using Stripe's secure card-entry UI. A signed
   successful webhook triggers the same sale-commit rules. Until that webhook
   is processed, the POS polls a staff-protected order-status endpoint, shows
   payment as confirming, and does not show the sale as complete or issue its
   final receipt.
5. Sale commit updates the order, payment, serialized units, reservations,
   order-line-unit allocations, and append-only inventory movements
   transactionally. Duplicate webhook delivery must not commit the sale twice.
6. The simulated receipt provides the order number and one-time claim code.
   An anonymous customer can register and claim the order before staff can
   look it up for a return.
7. Five failed claim attempts start a 30-second lock. A later claim attempt
   after expiry resets the counter and proceeds; a manager may reset the lock
   earlier. Legacy locked records without a cooldown timestamp remain
   manager-reset-only.
8. After claim, `SALES_ASSOCIATE` or `STORE_MANAGER` looks up the sale by
   order number and completes the return in one action. The staff action
   validates the 14-day `paid_at` window and exact unreturned units, then
   simulates the original-payment refund, restocks to the original POS sale
   source location, and records staff attribution.

## Acceptance-Test Checklist

These are full v1 acceptance gates, not a checklist of only the currently
implemented subset. The verified checkout, receipt, claim, and return
functionality is summarized above; unchecked items identify acceptance
coverage not yet verified or end-to-end validation work. The first
2026-10-23 user-local test-mode card sale reached successful webhook
processing (HTTP 200) and returned the terminal to blank without directly
observing the receipt. That gap was closed by the subsequent delayed-webhook
browser acceptance for `HM-2026-000036`, which verified the unresolved lock
after expiry and receipt display only after successful webhook delivery.
These browser runs did not use the isolated pytest API, and the local API's
database identity was not independently verified. The approved customer-only tester scope is
an anonymous cash-only demo POS sale
using shared stock and existing stock-availability/idempotency checks only,
followed by receipt-code claim, separate POS purchase history, and
policy-eligible automatic returns. Cooldown-only claim recovery and explicit
system-actor audit attribution are required; the manager reset remains
internal-only. Unclaimed sales stay paid and sold after the claim-code expiry;
only the encrypted code-delivery copy is cleaned up. Preserve staff
authorization.

### Authorization and request validation

- [x] Unauthenticated requests cannot create a POS checkout.
- [x] Simulated staff roles can access the POS checkout according to the
      approved role policy; both roles can create ordinary sales.
- [x] Variant identifiers are UUIDs; zero or negative quantities are rejected.
- [x] Client-supplied prices cannot change the server-calculated total.
- [x] POS and ECOM use the same approved VAT calculations and preserve
      order-time financial snapshots.
- [x] POS does not expose or apply discount or voucher inputs; order-level and
      line-level discount snapshots remain zero.

### Inventory and order creation

- [x] Store stock is preferred; warehouse stock is used only when store stock
      cannot fulfill the complete order.
- [x] An order is rejected if no single source can fulfill every line.
- [x] Concurrent ECOM and POS checkouts cannot reserve or sell the same
      serialized unit; both channel lock-holder directions pass the PostgreSQL
      two-session regression test.
- [x] Failed checkout/payment does not leave an incorrectly sold unit or
      unmatched inventory movement; provider failure and payment-failed tests
      preserve the reservation ledger, and cancellation restores stock with a
      matching release movement and no sale allocation.
- [x] Order creation retries do not create duplicate orders or reservations;
      pending-order, cash-sale, and card PaymentIntent replay tests pass.
- [x] Reusing an `Idempotency-Key` with a changed request returns `409` and
      leaves the original order and inventory/payment/receipt records unchanged.

### Cash and card

- [x] Cash payment immediately records a paid order and commits inventory once;
      route/service tests verify the paid state, sold-unit allocation, and
      exactly-once payment and sale movement on replay.
- [x] Cash and webhook-confirmed card payments set `paid_at` when the order
      first becomes paid; cash replay, duplicate success-webhook delivery, and
      later POS refund processing preserve the original timestamp.
- [x] Historical paid orders without a recoverable exact completion time remain
      `paid_at=NULL`; the nullable no-default migration and legacy-order
      regression verify no timestamp is invented.
- [x] Card checkout creates a PaymentIntent for the server-calculated amount
      and currency; regressions verify `18,900` cents and `eur` for the
      server-priced `EUR 189.00` order.
- [x] A client-side success indication alone cannot mark the order paid; card
      checkout stays `PENDING_PAYMENT`/`UNPAID`, and the staff status endpoint
      is read-only until the signed success webhook is processed.
- [x] A valid signed `payment_intent.succeeded` webhook commits the sale once;
      an end-to-end replay regression confirms only one sale allocation,
      movement, and webhook-event record.
- [x] A staff-protected status poll reports pending before the success webhook
      commits and the resulting order/payment state afterward.
- [x] Browser-verify the POS card confirming and delayed-webhook paths: the
  terminal remains locked while payment is unresolved, withholds the receipt
  while backend status is pending, and displays the receipt only after the
  signed webhook commits the sale. For the delayed path, verify the checkout
  remains locked after reservation expiry, then resolves to the receipt when
  the successful event is delivered. Backend status and webhook behavior have
  regression coverage.
      The 2026-10-23 test of `HM-2026-000035` exposed the race: Stripe CLI
      delivered the successful event after the POS had stopped polling. The
      local application database confirms `PAID` / `PAID` / `COMPLETED` with no
      refund; the receipt was not shown, and its unit is sold. The frontend fix
      was verified with test-mode order `HM-2026-000036` (EUR 69.00): after
      reservation expiry, the UI remained locked and showed the unresolved
      warning; replaying the existing success event returned HTTP 200, then
      the receipt appeared and auto-cleared after ten seconds. Its unit is
      sold; no live payment was taken. No further test sale was created.
- [x] Duplicate webhook events are idempotent; signed success-event replay
      produces one webhook record and one sale allocation/movement.
- [x] A declined card attempt leaves the order pending and reservation active
      for retry without changing its three-minute deadline; expiry releases the
      units and records a release movement.
- [x] Declined card attempts do not create extra local `Payment` rows; a retry
      reuses the existing PaymentIntent and completes the same pending order
      before expiry.
- [x] A valid `payment_intent.payment_failed` event is acknowledged
      idempotently while the payment/order remain pending and reservation
      active until expiry; replay leaves one webhook record and no sale.
- [x] A valid `payment_intent.canceled` event is acknowledged idempotently,
      sets order status to `CANCELLED`, payment and order payment status to
      `FAILED`, marks reservations `RELEASED`, restores units to `IN_STOCK`
      with `RELEASE` movements and `released_at`, and terminates the pending
      checkout immediately; replay creates no sale and one release movement.
- [x] Failed, cancelled, invalid-signature, mismatched-order, or mismatched-
      amount events do not commit a sale; signed-route regressions verify
      rejected/rolled-back events leave no sale or webhook-event record.
- [x] Late payment after the reservation can no longer be safely honored
      follows the approved refund behavior; signed-event replay creates one
      refund/webhook record and no sale allocation or movement.

### Receipt claim and returns

- [x] The receipt presents the checkout order number and one-time claim code
      after sale completion; acknowledgement removes the delivery copy and
      prevents the code from being retrieved again.
- [x] The raw claim code is shown once and is never stored unencrypted; the
      POS-specific claim record stores only its HMAC-SHA-256 digest, keyed with
      the dedicated application secret, and is tied to the order. The test
      independently recomputes the digest and verifies the delivery copy is
      encrypted separately.
- [x] The temporary encrypted delivery copy is separate from the claim record
      and is deleted after the POS acknowledges receipt delivery or after the
      inclusive 14-day claim deadline if unacknowledged. Expired copies are
      removed by startup and daily cleanup sweeps and cannot be delivered
      after expiry; the deadline regression verifies access at the deadline
      and rejection immediately afterward.
- [x] A registered customer can claim an eligible anonymous POS order with the
      correct order number and unused code.
- [x] Invalid or already-used claim codes are rejected without changing order
      ownership.
- [x] Five failed claim attempts lock the order for 30 seconds; a correct code
      cannot bypass the active cooldown, and a manager can reset it early.
- [x] A manager reset clears the failed-attempt count and records the latest
      reset time and manager UUID on the claim record.
- [x] A successfully claimed code cannot be replayed.
- [x] A lost receipt does not permit claim-code recovery or replacement; a
      manager lock reset clears failed attempts only, leaves the digest
      unchanged, and does not recreate the acknowledged delivery copy.
- [x] A claim code is accepted through the inclusive 14-day return deadline and
      rejected after expiry.
- [x] Staff lookup of an unclaimed anonymous POS order returns `409` with
      detail `This POS order must be claimed by a customer before staff can
      view its details`; it reveals no order details. Unclaimed orders cannot
      be processed for a refund.
- [x] An active, email-verified customer account is required to claim a POS
      sale; staff return lookup requires that claim.
- [x] POS returns are accepted through the 14-day deadline from `paid_at` and
      rejected after it.
- [x] After customer claim, either `SALES_ASSOCIATE` or `STORE_MANAGER` can
      look up the order by order number and complete the return in one staff
      action; customer-authenticated return endpoints reject POS orders.
- [x] POS return inventory is restocked to the original sale source location,
      whether the order was sourced from `PHYSICAL_STORE` or `MAIN_WAREHOUSE`.
- [x] POS return completion records only a simulated original-method refund
      and updates payment/order status atomically with restocking and audit.

### Sale and fulfillment state

- [x] A completed POS order has `order_status=COMPLETED` and
      `payment_status=PAID`; sold units are `SOLD`.
- [x] ECOM payment persists `order_status=PAID` before the fulfillment worker
      advances the order to `READY_TO_SHIP` or `READY_FOR_PICKUP`.
- [x] An ordinary POS transaction does not enter the later-collection pickup
      workflow.
- [x] The worker's `READY_FOR_PICKUP` transition records the pickup timestamp
      and deadline and enqueues one order-linked `ORDER_READY_FOR_PICKUP`
      notification; shipping readiness does not enqueue this pickup
      notification.
- [x] Eligible store pickups progress to `PICKED_UP` through the inclusive
      deadline, record `picked_up_at`, and enqueue `ORDER_PICKED_UP` once.
- [x] The authenticated order-status response exposes
      `ready_for_pickup_at`, `pickup_deadline_at`, and `picked_up_at`.
- [x] Approved ready-for-pickup and picked-up email copy is formatted.
- [x] After the inclusive deadline passes, expire the pickup by setting both
      shipment and `order_status` to `CANCELLED`, and setting payment and order
      payment status to `REFUNDED` through the simulated original-payment
      refund flow.
- [x] Restore each sold unit to the shipment's original source and append its
      `PICKUP_EXPIRY` movement in the same transaction as inventory, refund,
      status, and notification changes.
- [x] Enqueue `ORDER_PICKUP_EXPIRED` with the approved copy explaining that
      the refund is simulated and no payment-provider refund was sent.
- [x] Pickup-order return requests use the approved inclusive 14-day window
      anchored at `picked_up_at`.

## Confirmed POS Decisions

1. **Receipt-code security:** Five failed claims start a 30-second cooldown.
   After expiry, the next claim attempt clears the counter and proceeds; a
   manager may reset the lock early. Legacy locks without a cooldown timestamp
   remain manager-reset-only.
2. **POS return workflow:** A customer first claims the anonymous sale with
   its order number and one-time receipt code using an active, email-verified
   account. The customer POS purchase detail can then submit an exact-unit
   return. The backend validates ownership, `order_status=COMPLETED`,
   `payment_status=PAID`, the inclusive 14-day `paid_at` deadline,
   verified-account status, refundable balance, and unreturned units before
   completing the simulated original-payment refund
   and restocking to the original sale source location. The same transaction
   records the customer request and a `SYSTEM` processing event. The unique
   return-item allocation is the duplicate-refund guard. Staff can continue
   using the separate one-step POS return route. E-Commerce customer return
   request/process routes continue to reject POS orders.
3. **Stripe events:** `payment_intent.payment_failed` is retryable; payment
   and order remain pending and the reservation remains active until the
   three-minute expiry. `payment_intent.canceled` is terminal and releases
   the reservation immediately. Both events are acknowledged idempotently
   and neither commits a sale.

## Approved POS v1 Architecture Wording

The following approved target wording is reflected in `docs/architecture.md`;
unchecked acceptance criteria identify behavior not yet implemented or not
specifically verified.

> POS checkout simulates an in-store sale. A successful cash payment or
> `payment_intent.succeeded` webhook-confirmed card payment commits the order
> as `COMPLETED` with `payment_status=PAID`, and records its serialized units
> as `SOLD` after the simulated in-store handover. It does not enter the later-collection
> pickup workflow. POS stock prefers `PHYSICAL_STORE` and may fall back to
> `MAIN_WAREHOUSE`, with one source location per order. ECOM orders may use
> shipping or a separate store-pickup lifecycle when available; `PICKED_UP`
> describes completion of that later collection flow. Anonymous POS sales may
> be claimed by a subsequently registered customer using the order number and
> one-time receipt code whose keyed HMAC-SHA-256 digest is stored in an
> order-linked POS-specific claim record. The digest key is a dedicated
> application secret. POS refunds require a registered, email-verified
> account, like ECOM returns. POS uses server-side catalog pricing and VAT
> calculations like ECOM; discount and voucher functionality is out of scope
> for both channels in v1, and discount snapshot fields remain zero. Both
> simulated `SALES_ASSOCIATE` and `STORE_MANAGER`
> users may create ordinary POS sales. A declined card attempt leaves the order
> pending and the reservation active for retry until its three-minute expiry.
> Following card confirmation, the POS polls a staff-protected order-status
> endpoint and waits for signed webhook processing before showing the sale as
> complete or issuing its final receipt.
> POS returns are allowed for 14 days from the shared order's `paid_at`
> timestamp, inclusive through the deadline. The receipt claim code proves
> ownership for that refund claim, is valid through the deadline, and expires
> afterward. Leave historical `paid_at` null when the exact payment time
> cannot be recovered; do not backfill an approximation.
> ECOM and POS returns refund only the original payment method; there is no
> customer/store-staff choice of store credit.
> Five failed anonymous-order claim attempts lock claims for that order until
> a manager resets it; the claim record retains only the latest reset time and
> manager UUID, not reset history. After a verified customer claims the sale,
> either `SALES_ASSOCIATE` or `STORE_MANAGER` can look it up by order number
> and complete a POS return in one action. Customer return-request and process
> endpoints are for ECOM only and reject POS orders. The staff action validates
> the inclusive 14-day `paid_at` deadline and exact unreturned units, then
> simulates the original-payment refund, restocks returned inventory to its
> original sale source location, and records `SYSTEM` attribution. Staff retain
> their separately protected POS return workflow. `payment_intent.payment_failed` leaves the pending checkout and
> reservation available for retry until expiry; `payment_intent.canceled`
> terminates the checkout and releases the reservation immediately. Both
> events are handled idempotently and neither commits a sale.

## Remaining Implementation Roadmap

1. **Complete:** align the `Order` ORM fulfillment constraint with migration
   `464acebca437` and validate the model/migration definitions. This migration
   supplied the POS fields and handover state; both databases have since
   advanced to verified head `c7f48d21a063`.
2. **Complete:** apply the migration to primary `half_moon_db` after a live
   identity and exact parent-revision preflight. The primary was migrated
   without a backup; post-migration checks verified the revision and schema
   objects.
3. **Complete:** implement POS orchestration in `backend/app/services/pos.py`.
   Pending-order creation, cash-sale completion, mocked PaymentIntent
   creation/retrieval, late-payment safety, PaymentIntent validation and event
   processing, signed webhook dispatch, staff checkout/status/receipt routes,
   receipt acknowledgement and cleanup, customer claims, and manager claim-lock
   reset are implemented. Staff POS returns use the claimed-order lookup and
   one-step return route. The focused POS checkout and return-policy run passed
   82 tests on 2026-10-12. Do not refactor
   the ECOM Checkout Session or shipping completion path for POS.
4. **Complete:** add the staff-protected POS status, receipt retrieval,
   acknowledgement, and manager claim-reset routes, plus the
   customer-authenticated claim route, in the current route module
   `backend/app/main.py`. Staff lookup resolves claimed POS orders by order
   number; staff completes returns in one action, while customer
   return-request/process routes reject POS orders. The focused POS checkout
   and return-policy run passed 82 tests on 2026-10-12.
5. **Complete:** validate one controlled POS card checkout in Stripe test
   mode. The initial attempt expired unpaid and its PaymentIntent was canceled;
   its expired reservation was released and verified. Stripe logs identified
   the missing `return_url` requirement caused by redirect payment methods.
   POS PaymentIntents now set `allow_redirects=never`. A fresh EUR 189 test
   checkout succeeded, its `payment_intent.succeeded` webhook received HTTP
   200, and the paid/completed state, sold unit, and `SALE` movement were
   verified in the isolated test database. Focused tests passed 4 expiry
   cleanup cases and 1 PaymentIntent-parameter case; the full suite passed
   112 tests after these changes.
6. **Complete for this phase:** implement ECOM store-pickup readiness, deadline
   creation, eligible progression to `PICKED_UP`, status timestamps, and
   ready/collected notification handling. The focused results are recorded
   above.
7. **Complete:** implement expired-pickup refund/restock to the original
   source, recording `PICKUP_EXPIRY` in the same transaction as inventory,
   refund, status, and notification changes. Pickup returns use the shared
   return workflow and the approved inclusive 14-day window anchored at
   `picked_up_at`. `test_fulfillment.py` passed all 8 tests;
   `test_returns_policy.py` passed all 9 tests; and the latest full project
   suite passed 148 tests on 2026-10-09.
