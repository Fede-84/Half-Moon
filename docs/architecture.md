# Half Moon Architecture

## Project Overview

Half Moon is an Italian boutique jewelry business with:

- A public React e-commerce website
- A protected React POS and operations panel
- Primary application PostgreSQL database: `half_moon_db`
- Isolated pytest PostgreSQL database: `half_moon_test_db`
- One physical store
- One warehouse
- One shared inventory pool
- Simulated business operations
- No real employees required to operate the application

### Environment Naming and Current Database Status

`half_moon_db` is the local primary application database, currently reached
on loopback port `5432`; it is intended to be the hosted development database
after the frontend is complete. It is not currently a hosted or production
database. `half_moon_test_db` is the isolated pytest database on loopback port
`55432`; pytest must remain restricted to that database. Migration
`b9d4e6f13a27` for POS claim cooldowns was applied to both local databases on
2026-10-21. On 2026-10-23, lifecycle migration `c2f40e7a91bd` was applied to
`half_moon_test_db` first and then to `half_moon_db`. The main database's
revision was read-only verified at `c2f40e7a91bd`; the test-database upgrade
reported the same migration transition. The full backend suite passed 197
tests against the migrated test database on 2026-10-23. On 2026-10-26, all
201 backend tests passed against the isolated test database after its dedicated
PostgreSQL cluster was started on `127.0.0.1:55432`; the cluster was left
running and the primary `half_moon_db` was not touched. Frontend lint and
production build passed after the mobile touch-target change. No migration,
live payment, or deployment was performed in this validation block. On
2026-10-27, the shared frontend API error formatter was added and integrated
into the existing authentication, account, POS, checkout, and product-detail
error helpers. Frontend lint and production build passed; a focused four-case
formatter behavior check passed; and the full backend suite passed all 201
tests against the isolated test database. The primary database was not
touched by the test run. The user subsequently read-only verified the local
primary `half_moon_db` in DBeaver at Alembic revision `ba49dc731e20`; the
corresponding migration descends from `c2f40e7a91bd`. The user also ran the
empty-JSON login validation request through Postman; its response body
identified missing `email` and `password` fields. The captured message did not
include the HTTP status code. No migration, payment, or deployment was
performed. On 2026-10-27, after the user's explicit approval, a bounded
in-process test of the notification and fulfillment workers verified
`half_moon_db`, the seven approved orders, and an empty pending-notification
queue before starting. All seven orders progressed to `DELIVERED` with
`payment_status=PAID`; seven `ORDER_SHIPPED` and seven `ORDER_DELIVERED`
notifications were sent successfully. The three historical failed
notifications were unchanged. The worker tasks shut down gracefully; no
persistent worker setting was changed.
The earlier 2026-10-23 browser/database spot-check of the signed-in customer
account confirmed that orders `HM-2026-000001`, `000002`, `000005`, `000008`,
`000010`, `000039`, and `000040` are `EXPIRED`/`UNPAID` with no shipment;
they expired before payment and were not delivered. The same read-only query
confirmed `HM-2026-000022` remains `DELIVERED` in both order and shipment
records, with `payment_status=REFUNDED`, and `HM-2026-000004` remains delivered.
The account UI displays `order_status` from the backend without remapping it.

The isolated test database is seeded with 4 products, 18 variants, 18,000
serialized in-stock units, and 2 demo staff accounts. The password-reset and
related auth focused run passed 14 tests. The 2026-10-21 reset and reseed
restored those counts at revision `b9d4e6f13a27`; the primary database was
untouched during that earlier operation. There is not yet a separately hosted
application/database deployment. Older dated checkpoints retain the
environment labels used when they were written.

### Current Implementation Status (2026-10-27)

- The FastAPI application does not register `CORSMiddleware`. Local frontend
  development uses Vite's `/api` proxy; the planned hosted setup requires a
  same-origin `/api/*` rewrite to the backend. The Render rewrite is not yet
  configured or verified.
- `POST /api/v1/webhooks/stripe` reads the raw request body once with
  `await request.body()` before signature verification. No application-defined
  global middleware or body parser was found that consumes the stream first.
- The database layer uses a synchronous SQLAlchemy engine with
  `pool_pre_ping=True`; pool sizing is left at SQLAlchemy defaults. FastAPI's
  `get_db` dependency yields a session inside `with SessionLocal() as session`,
  closing it after the request.
- The reviewed `frontend/src` source uses `VITE_STRIPE_PUBLISHABLE_KEY` for
  the Stripe public key. No recognizable private-key, database-URL, or Stripe
  secret-key patterns were found in that source scan. Environment/configuration
  files were not inspected, so their contents are not covered by this status.
  Backend credential settings are centralized in the Pydantic `Settings`
  class.
- Frontend production compilation (`tsc -b` and Vite build) passed on
  2026-10-27.

### Local pytest PostgreSQL cluster operations (macOS)

The dedicated pytest cluster is stored at
`$HOME/Library/Application Support/Half Moon/pg-test-cluster` and listens on
`127.0.0.1:55432`. On this macOS Homebrew setup, start and verify it with:

```bash
/opt/homebrew/bin/pg_ctl -D "$HOME/Library/Application Support/Half Moon/pg-test-cluster" -l "$HOME/Library/Application Support/Half Moon/pg-test-cluster/server.log" start
/opt/homebrew/bin/pg_isready -h 127.0.0.1 -p 55432
```

Stop only this cluster with:

```bash
/opt/homebrew/bin/pg_ctl -D "$HOME/Library/Application Support/Half Moon/pg-test-cluster" stop
```

The pytest harness validates the isolated database name, role, host, and port;
never redirect pytest to the primary application database. On 2026-10-21 this
cluster was started after a connection-refused setup error, accepted
connections on `55432`, and was left running after the focused POS test module
passed.

The project is designed as a GitHub portfolio project. Customers can test the storefront and POS experiences. The business side is automated by the backend.

## Document Guide

Read this document in the following order:

1. Project scope, business configuration, and money rules
2. Catalog, inventory, customer, and authentication concepts
3. Business processes and automated simulation
4. Technology stack and external integrations
5. Security, GDPR, and out-of-scope boundaries
6. Data modeling, relationships, constraints, and schema blueprint
7. Project structure and implementation sequence
8. Collaboration and document-maintenance rules

The schema blueprint is the approved design reference. The implementation sequence is the practical build order. They are intentionally separate: architecture decisions are reviewed first, then the project is built incrementally.

## Technology Stack

### Frontend

- TypeScript
- React
- Vite for local development and production builds
- React Router for URL-based storefront and POS route separation
- Axios through one centralized API client
- Regular CSS using CSS Modules for component-scoped styles, with a global stylesheet for resets and shared design tokens
- One Vite application has separate public storefront and POS route/layout shells. Customer sign-in is at `/login`; staff sign-in is at `/staff/login`. The POS shell requires an authenticated staff session. Its split catalog/cart terminal supports anonymous cash and card sales, reusing the product summary/detail APIs and server-authoritative checkout; product cards prefer the local variant image when available, matching storefront image selection. Card checkout uses Stripe's secure Card Element and the public `VITE_STRIPE_PUBLISHABLE_KEY`; the browser sends card details directly to Stripe, while the server-calculated amount and checkout idempotency key remain authoritative. After Stripe confirms a card attempt, the UI polls the staff order-status endpoint and does not show a receipt until backend `order_status` is `COMPLETED` and payment is confirmed, as committed by the signed webhook. A declined attempt remains retryable on the same PaymentIntent until the three-minute reservation expires; the current UI has no cancel action and waits for backend expiry before unlocking the basket. Cash and webhook-confirmed card sales show the order number and one-time receipt claim code with a copy control, acknowledge the receipt automatically after ten seconds without a visible countdown, then reset to a blank sale. The staff-protected `/pos/orders` page looks up claimed POS orders, processes exact-unit staff returns, and exposes claim-lock reset by order number to managers only. Five incorrect claim attempts create a 30-second lock; the next claim attempt after expiry clears the counter and proceeds, while a manager may reset early. The storefront links to the public customer `/pos-demo`. Manual browser acceptance on 2026-10-19 confirmed one cash sale, receipt display and copy, automatic acknowledgement, and return to a blank sale; a Stripe test-mode card sale for order `HM-2026-000024` at EUR 69.00 also completed, its signed `payment_intent.succeeded` webhook received HTTP 200, and the receipt appeared. No real payment was taken. The shared auth provider restores cookie-backed sessions only when the readable CSRF cookie is present, then loads `/auth/me` or `/staff/me` according to the refreshed session role; sign-out calls `/auth/logout`. Customer login honors only allowlisted in-app return paths for `/`, checkout, account, claim, POS-purchase, and order-detail routes. The storefront account icon sends users without a customer session directly to `/login` without saving `/account`, so that sign-in returns them to `/`; direct protected-account requests and checkout sign-in retain their requested allowlisted destinations. The successful email-verification continuation also returns customers to `/`. Staff sign-in sends users to `/pos`.
- The public `/pos-demo` page is a separate cash-only customer simulation. It submits only variant IDs and quantities to `POST /api/v1/orders/pos-demo`, with an `Idempotency-Key`; the backend chooses price, stock, and payment details. It uses the existing shared inventory availability and POS reservation rules and never accepts a customer ID, client price, or card payment. Reservation and sale movements are attributed to `SYSTEM`. The one-time claim code is retrieved separately and automatically acknowledged after ten seconds; receipt retries before acknowledgement return the same code. The existing staff `/pos` and `/pos/orders` authorization boundaries remain unchanged.
- The customer account keeps POS purchases in a separate list from E-Commerce history. Verified customers claim a sale at `/account/claim-pos-order`, view their claimed purchases at `/account/pos-purchases`, and can submit exact-unit returns through the POS purchase detail. The customer POS demo's “Claim this purchase” links prefill the order number on the claim page; the one-time claim code is never placed in the URL, and the order number is preserved through customer sign-in. `POST /api/v1/orders/pos/{order_id}/returns` verifies ownership, verified account status, `order_status=COMPLETED`, the inclusive 14-day `paid_at` policy, refundable payment balance, and unreturned units. The request and automatic processing occur in one transaction; refunds are simulated, sold units return to their original sale source, and the processing audit event is attributed to `SYSTEM`. Unique return-item allocation prevents duplicate refunds. Owner-scoped order-detail and return-history `GET` routes support both E-Commerce and claimed POS orders. The E-Commerce return-request `POST` remains ECOM-only, and staff return processing remains on its protected staff route.
- Local Vite `server.proxy` forwards relative `/api/...` requests to the FastAPI service at `http://127.0.0.1:8000`. This is the HTTP API port; `55432` is the isolated pytest PostgreSQL port and is not a proxy target.
- The frontend client reads the `half_moon_csrf` cookie and sends it in `X-CSRF-Token` on non-GET requests. The refresh cookie remains HttpOnly.
- Screen-level API error handling preserves string `detail` responses and formats FastAPI validation `detail` arrays from field locations and messages only; submitted `input` values are never rendered. Axios failures without a usable API detail use the screen-specific fallback.
- Short-lived access tokens remain in frontend module memory, and customer/staff profile data must remain in application memory; neither is persisted in `localStorage` or `sessionStorage`. Auth invalidation clears both in-memory token and profile state.
- A 401 from a protected API request triggers one shared `POST /api/v1/auth/refresh` request and at most one retry of each original request. Customer and staff login 401 responses remain available to their forms. A refresh 401/403 or a 401 after retry clears the in-memory access token and auth context, then hard-navigates to `/staff/login` for a known staff role or a current `/pos` or `/staff` route; otherwise it uses `/login`. When the role is unknown on a public route, the customer login is the fallback because the shared refresh cookie does not reveal the role. Transient network and server errors are surfaced without treating the session as invalid.
- Route-level auth tests cover missing CSRF headers/cookies on refresh and logout without rotating or revoking the refresh session, and reject customer/staff cross-access to their respective profile endpoints.
- The global stylesheet defines provisional neutral grayscale tokens. Choose the final palette after reviewing the homepage with its real images.

### E-Commerce Homepage Direction (2026-10-12; Initial Design Checkpoint)

This section records the pre-implementation design direction. Later decisions
and current implementation status below supersede references to unresolved
homepage discussion or unimplemented homepage work.

The user prefers to choose the final visual style and palette after reviewing the homepage with the real jewellery images in place. For the initial visual pass only, use `hero1.jpg` and `logo2.jpg`; both choices are temporary and should be compared with the alternatives when CSS is tuned. The prepared assets are in `frontend/public/images/`. All 18 local development database variant `image_url` values have been updated to their corresponding `/images/...` web paths (not local filesystem paths), reusing each colour image across the ring's sizes. The single product-level `image_url` values remain unchanged because the homepage cards are colour-specific and use variant images. The catalogue API response was verified against the updated paths. The production database has not been updated; apply these variant image paths when deploying the production catalogue to Render. Build and review the page before settling final colours and asset choices.

The agreed homepage direction is simple and elegant, using the Japanese MIKIMOTO site as broad inspiration without copying its branded content or design. The header has a simple menu control on the left, the Half Moon name centered with the user's existing logo beside it, and account and cart controls on the right; the logo filename and exact sizing will be determined after visual review. Tapping the menu opens the side-sliding filter panel. The panel provisionally shows expanded "Our Collection" and "Colour" sections. "Our Collection" lists the categories assigned to the catalogue, while "Colour" offers Gold and Silver. Shoppers can select multiple categories and multiple colours, or filter on either dimension alone. The filter panel has no separate Clear selections control; shoppers untick selected options individually. An icon-only magnifying-glass button applies the selected filters, closes the panel, and scrolls to the matching product grid. The magnifying-glass button must have a descriptive accessible name. After applying filters, reopening the panel retains the active selections as checked. Use actual catalogue categories rather than inventing labels, and confirm the available category and colour data before implementation. The current local catalogue data was inspected through the read-only product-list and product-variant endpoints: it contains four product designs (Necklace, Ring, Bracelet, Earrings). The user clarified that the homepage should show eight cards, one for each design in Gold and Silver. The variant `color` values are "Yellow Gold" and "Silver"; the website must display "Gold" everywhere customer-facing when the stored/API value is "Yellow Gold" (including filters, cards, product details, and any other customer-facing section), while retaining the underlying value for API and data matching. Silver displays as "Silver." Ring prices are now fixed across sizes at EUR 169.00 for Gold and EUR 99.00 for Silver. The current E-Commerce checkout creates orders in EUR; variant retail prices are returned as decimal strings without a currency field. Showing active filters near the results is also a suggested clarity improvement, not a final requirement. The account control remains in the header, with no duplicate account link assumed in the menu. No brand-history or customer-service pages are currently planned, so additional menu links should not imply content that does not exist. The user wants to discuss the complete homepage—including the footer and all other elements—before implementation; the visual design and final colour palette will be chosen after reviewing the assembled page with the provided images. The main hero is one shared container with temporary heading "Moon Collection" on the left and a hero image on the right; the image area is wider than the text area, and both sides have the same height. For the first pass, use `hero1.jpg`; compare with `hero2.jpg` during visual styling. The heading and initial image are provisional. The hero has no button; visitors scroll to the eight design-and-colour cards below it. The four product designs and their colour variants are the full planned catalogue for the current scope; future additions are not currently planned. Each homepage product card shows an image, name, and price; selecting a card opens a separate product detail page. As a provisional starting scope, the detail page may show photos, name, description, price, material/colour/size choices, and an Add to Cart action; these choices are explicitly open to revision after reviewing the design. The footer follows the grid and contains the selected Half Moon logo and a copyright line; the current footer logo is centered. Do not add GitHub links; defer Contact and social links until real Half Moon destinations are available. The footer logo uses the temporary `logo2.jpg` choice pending comparison with the alternative. The `GET /api/v1/products` response now includes one product record per design with its product image and variant ID, colour, size, retail price, and variant image; stock is intentionally excluded. A regression test verifies the card-relevant fields. The frontend groups variants by colour to display eight cards. Availability is deferred until the shopper has selected a size and fulfilment method. A separate all-products catalogue page is not currently planned. The exact content and layout of product detail pages, color palette, final copy, and remaining page elements remain undecided and must be discussed before implementation.

**Homepage decisions captured 2026-10-13 (historical status; superseded below):** The filter drawer slides in from the left; applying filters closes it and scrolls to the matching product grid. The cart drawer slides in from the right; the implemented drawer currently shows only an empty-cart message and Continue Shopping action. Cart contents, subtotal, and View Cart/Checkout actions are planned but not implemented. The mobile target is for both drawers to occupy nearly the full screen while retaining their slide directions; mobile-specific implementation is deferred until the site is built. The footer logo is centered provisionally, superseding the earlier left-placement suggestion. Its copyright year must be dynamic; exact wording, including whether to add “All rights reserved,” remains undecided. For the first product-detail pass, use a gallery, name, description, EUR price, material/colour/size choices, and Add to Cart; exact layout and copy remain provisional.

**Header and hero refinement (2026-10-13):** Center the brand group in the header with the logo image to the right of the “Half Moon” wordmark. The first-pass hero shows “Moon Collection” without a separate “Half Moon” eyebrow above it.

The filter drawer's magnifying-glass apply control is centered in the blank action area beneath the filter options and has a decorative black frame. Drawer content padding is 24px; the header's bottom padding is 24px; filter sections use 22px vertical padding and their headings have a 14px bottom margin. Filter-option rows remain 42px high. The centered action area has a 108px minimum height and 24px vertical padding. At short viewport heights, drawer content may scroll rather than overlap controls.

**E-Commerce frontend status as of 2026-10-13 (superseded below):** The implemented storefront currently consists of the catalogue homepage, product cards, category/colour filtering, loading/error/retry states, and an empty-cart drawer. The product-detail route, login page, and POS page are placeholders. Product details, cart state or persistence, checkout pages/forms, customer account flows, and POS operations are not implemented in the frontend; there are no frontend `/checkout/success` or `/checkout/cancelled` routes yet. The backend order and checkout APIs are implemented separately as documented above. For the planned guest-checkout milestone, persist only variant IDs and quantities; prices and availability remain server-authoritative. Offer shipping and store pickup. Collect buyer email and names; shipping requires recipient name (reuse buyer name when the buyer receives the order, otherwise collect a separate recipient name), and pickup requires buyer first and last name so staff can identify the order. Signed-in checkout and customer account UI belong to a later account milestone. The editable two-letter shipping country code field should default to `IT`. Stripe's local success and cancel URLs should target the frontend routes `/checkout/success` and `/checkout/cancelled`; production must configure the corresponding public frontend URLs. The success page must not treat the browser redirect as payment confirmation; webhook processing remains authoritative.

**Shipping-cost decision (2026-10-13):** The planned first guest-checkout milestone treats shipping as free. The amount charged is the product total; do not add a delivery fee until its amount and rules are designed and supported by the backend.

**Price-display clarification (2026-10-13):** Each homepage design/colour card shows its fixed price; do not display a size-dependent “From” price or range. Although the API stores retail price on each variant, current customer-facing prices do not vary by size. Ring pricing is fixed across sizes at EUR 169.00 for Gold and EUR 99.00 for Silver.

The exact `GET /api/v1/products` list contract is: each product contains `id`, `name`, `slug`, `description`, `category`, `image_url`, and `variants`; each variant contains only `id`, `color`, `size_system`, `size_label`, `retail_price` (a decimal string), and `image_url`. The list does not include variant `material`, `product_id`, SKU, EAN, or stock fields. Test `test_product_list_returns_card_image_and_variant_data` verifies the product image and exact variant field set.

The account icon remains in the shared storefront header and opens the account page. A small round brighter-purple dot, based on the Vite lightning-bolt favicon color, overlays the icon whenever any customer or staff auth session is confirmed; it is absent when signed out. Signed-out customers see sign-in and registration options there; signed-in customers see their account area and a Log out action on that page. After a successful customer or staff sign-out, the browser replaces the current route with the storefront homepage; if the sign-out request fails, the user remains on the current page and sees the error.

The signed-in customer account area is at `/account` and displays E-Commerce order history only. `GET /api/v1/orders` returns that customer's ECOM orders newest first; claimed POS orders are excluded. Selecting an order opens `/account/orders/:orderId`, which uses the customer-scoped order-detail endpoint. The frontend checks that the returned channel is ECOM and requests shipment progress for paid, ready, shipped, delivered, picked-up, and refunded pickup-cancellation states because unpaid orders without shipments have no status record. The status response contains the unified `order_status` and the separate operational `Shipment.status`. Customer returns are submitted inline only for orders in `DELIVERED` or `PICKED_UP` with a refundable payment balance; the backend enforces the inclusive 14-day window and verified-account requirement. The frontend creates the request and immediately calls the simulated processing endpoint; an order-scoped return lookup supports recovery and status display if processing is interrupted or the page is reloaded. No real payment-provider refund is sent. Customers opening a protected account route while signed out are sent to `/login` and returned to that internal account path after successful sign-in. The account area includes a sign-out action. Password reset request and confirmation use the existing generic response and single-use 30-minute token; the reset email links to `/reset-password?token=...`. `FRONTEND_PUBLIC_URL` defaults to `http://localhost:5173` locally and must point to the public storefront URL in the Render backend service's Environment settings.

In the header, the Half Moon wordmark and logo form one group centered together; the logo sits to the right of the writing. The wordmark is 1.5rem with a 48px logo on wider screens and 1.0625rem with a 34px logo at the compact breakpoint. Equal minimum-zero grid tracks preserve true center alignment. Compact action spacing is reduced; at viewports below 540px, the POS demo link and account/cart controls move together to a right-aligned second row to keep the link readable.

The homepage collection section uses the letter-spaced uppercase title “DISCOVER THE COLLECTION,” aligned vertically with its live piece count; do not add a second collection heading. Each homepage card shows the product image, product name, and price beneath the name; do not print Gold or Silver under the name. The image's accessible text may identify its colour variant. Keep eight separate design-and-colour cards and order them Necklace (Gold/Silver), Earrings (Gold/Silver), Ring (Gold/Silver), then Bracelet (Gold/Silver), so Necklace/Earrings occupy the top row and Ring/Bracelet the row beneath on desktop.

The menu's category and colour options provisionally use checkboxes to support multiple simultaneous selections; this visual control is open to revision after review.

The user prefers no separate Clear selections control; shoppers can untick chosen filters individually.

After applying filters, reopening the menu retains the active category and colour selections as checked.

Customer-facing category labels provisionally use the plural forms "Necklaces," "Rings," "Bracelets," and "Earrings," mapped to the catalogue's underlying category values.

Whether to display active-filter labels near the product grid is deferred until visual review.

**Current E-Commerce frontend implementation:** The `/` route renders the catalogue homepage with a hero, product cards, multi-select category/colour filters, result count, loading/error/retry states, and an empty-results state. Categories come from the catalogue API; customer-facing colour labels map Yellow Gold to Gold. Applying filters closes the left drawer and scrolls to the collection heading; the homepage cart drawer is always anchored and animated from the right, matching the product-detail bag. Selected categories and colours are URL parameters carried through product detail and restored on return. The current four-design catalogue produces eight design/colour cards. The `/products/:slug` route renders product details. The cart provider keeps cart state in memory and persists the basket in the current tab's `sessionStorage` so it survives a full-page Stripe redirect; the stored cart is removed when the tab closes. Guest and authenticated checkout routes create ECOM orders and redirect to Stripe-hosted Checkout; `/checkout/success` and `/checkout/cancelled` provide payment-return pages. Customer registration, email verification, password-reset screens, sign-in, and cookie-backed session restoration are implemented. Customer sign-in offers a resend-verification action after an unverified-email response; its generic message does not disclose whether the address belongs to an eligible account. Product-detail route entry scrolls to the top while collection-return links retain their deliberate collection anchor. Before an unauthenticated customer leaves the homepage from a collection anchor for account sign-in, the anchor is cleared and the scroll position is reset while preserving filter query parameters, so browser Back returns to the filtered homepage top. The browser tab uses the Half Moon moon logo and the page title “Half Moon.” The customer account at `/account` provides ECOM order history; `/account/orders/:orderId` provides customer-scoped details, paid-order shipment status, inline return submission, and return-status recovery. Return eligibility is enforced by the backend; refunds and restocking are simulated. The staff-protected `/pos` cash/card checkout terminal links to `/pos/orders`, which provides claimed-order lookup and exact-unit POS returns; a separate manager-only form resets a locked claim by order number without exposing unclaimed order details. Claim lockout automatically expires after 30 seconds.

Both storefront drawers close from their explicit close controls, backdrop clicks, or Escape. The dialog handles Escape directly and retains the native cancel-event handler as a fallback.

The product-detail page uses an editorial two-column desktop layout, stacking the single colour image first on narrow screens. It displays only existing catalogue data: product name, category, description, selected variant material and colour, ring-size options, EUR price, and a simple Available/Out of stock state. Non-ring variants do not expose their stored size labels; if a non-ring product ever has multiple variants, the selector uses neutral option labels. It does not display SKU, EAN, internal identifiers, location breakdowns, or invented care, gemstone, shipping, or returns copy. The product-list response resolves the route slug to its product UUID and supplies the product-level image fallback; the product-detail response supplies material, variant data, and current stock.

Customer E-Commerce and claimed POS order summaries display material and colour once when their customer-facing labels match, followed by size only for rings. The order-detail response adds the current product category to each variant for display; this is response-only and does not rewrite order snapshots or require a database migration. Legacy EU ring snapshots without category remain recognizable from their EU size system, and the frontend displays their Italian size and Italian-facing SKU without rewriting the historical snapshot.

Homepage cards pass the stored colour value in the URL. The detail page selects that colour when valid; without a valid colour query it selects Yellow Gold when present, otherwise the first returned colour. Yellow Gold displays as Gold. Colour and size choices use labelled buttons. A size must be explicitly selected when the selected colour has multiple variants; unavailable sizes are disabled. Add to Cart adds one unit and is disabled until the selected variant is available. Its confirmation message is “Added to your bag” and does not open the bag panel.

Cart state is held in React memory and persisted in the current tab's `sessionStorage`. Adding the same variant merges it into the existing line and increments its quantity. New cart entries carry product category; older saved entries remain valid and hydrate category from the existing catalogue API, with their EU size system retained as a legacy ring indicator. The bag shows thumbnail, product name, customer-facing colour, ring size only, unit price, quantity controls, remove action, and exact EUR subtotal using integer-cent arithmetic. Checkout follows the same ring-only size rule. Quantity increases are capped by the stock snapshot most recently loaded by the client; checkout still validates stock and recalculates price on the backend. The product page header uses a Back to collection link; the homepage retains its filter control. Only the product-detail product name uses the same bold Tangerine display font as the homepage “Moon Collection” title; its responsive size is `clamp(1.8rem, 3.5vw, 3.5rem)`, with a 12px bottom margin matching the category label's spacing above.

After a successful add from product details, the page scrolls smoothly to the top so the cart count is visible. “Back to collection” and all “Continue shopping” controls navigate to `/#collection-title`, carrying any selected collection category/colour URL parameters; the storefront waits for catalogue loading to finish, then scrolls the heading into view immediately before the page paints. Cart-drawer controls also close the drawer during navigation. The legacy `/#product-grid` hash is also supported; filter application updates the URL and scrolls smoothly to the same heading. Underlined links and buttons use a shared 5px offset to keep the underline clear of letter descenders.

The homepage hero stacks its “Moon Collection” text panel above the photo at viewport widths of 768 CSS pixels and below, retaining the existing side gutters; wider layouts keep the side-by-side composition. Below 540 CSS pixels, the storefront header places customer actions on a right-aligned second row and keeps “Try POS demo” on one line.

The checkout route is a single-page flow for guests and signed-in customers. Guests submit to `POST /api/v1/orders/guest`; signed-in customers submit to `POST /api/v1/orders`. Both requests use an `Idempotency-Key`, support shipping or store pickup, and redirect to Stripe-hosted Checkout. Shipping is free. A guest email associated with an account must use sign-in and the authenticated order route. For guest shipping, buyer first and last names are required when the buyer receives the order and supply the shipping recipient name; gift recipients use a separate recipient name. Guest store-pickup checkout requires buyer first and last names. The editable two-letter shipping country code defaults to `IT`. The shipping recipient summary reflects the selection: it says “Delivering to you” when the buyer is selected, and names the recipient when “Someone else” is selected; it never echoes raw account profile names. The form defaults to delivering to the buyer in both development and production. Shipping fields in all builds start with the same obviously fictional demo address, and `Test Shopper` remains the prefilled recipient if “Someone else” is selected. A visible warning explains that the address is fictional and submitted orders can still be recorded and affect inventory. Guest email and contact-name fields are never prefilled with test data.

Stripe return routes are derived from `frontend_public_url`: `/checkout/success` and `/checkout/cancelled`, unless explicit Stripe return URLs are configured. The backend success handler issues a `303` redirect to the frontend success route and forwards Stripe's Checkout Session ID as the `session_id` query parameter; cancellation redirects to its frontend route. On success, the frontend removes the Session ID from the visible URL, waits for auth restoration, and checks the persisted payment state every two seconds for at most 30 seconds; the signed webhook remains authoritative. Signed-in customers are redirected to `/account` after the check and receive a one-time thank-you notice only when the backend reports `payment_status=PAID`; pending, closed, or unavailable states show a non-success notice. Guests stay on the branded confirmation page and see the thank-you message only after the backend reports `PAID`. Cancellation stays on its branded page and preserves the tab's saved cart; a successful return clears the pending cart while cancellation only clears the pending-checkout marker. Auth state remains in memory and is not persisted across the Stripe redirect.

Customer and staff sign-in, cookie-backed session restoration, profile loading, logout, and staff route gating are implemented. Access tokens and profiles remain in application memory. Customer registration, email verification, password reset, order history, order details, shipment status, inline return submission/recovery, the customer POS claim form, the customer POS purchase area, and the public cash-only `/pos-demo` journey are implemented. Claimed POS orders remain separate from E-Commerce order history; eligible POS returns are processed automatically by the backend and attributed to `SYSTEM`. Staff POS checkout and the `/pos/orders` lookup, return, and manager claim-reset page remain staff-protected; backend claim attempts cool down for 30 seconds after five failures. The focused POS, returns, and audit suites passed 96 tests; frontend production build and lint passed. Browser acceptance verified the public cash sale, customer claim, separate POS history/detail, automatic simulated return, ten-second receipt auto-acknowledgement, and updated E-Commerce guest-success wording. The full backend suite passed 193 tests at the 2026-10-21 POS milestone after resetting and reseeding only the isolated test database; the later full backend run passed 195 tests on 2026-10-23, as recorded below. At the 2026-10-21 browser checkpoint, staff card-confirmation acceptance was blocked by missing local test-mode Stripe configuration; that blocker was superseded by the successful delayed-webhook browser acceptance for `HM-2026-000036` on 2026-10-23, described below. Manual browser acceptance on 2026-10-19 confirmed guest checkout (`HM-2026-000021`) and authenticated customer checkout (`HM-2026-000022`) in Stripe test mode. Both `checkout.session.completed` webhooks returned HTTP 200, both orders reached `PAID`, order 022 appeared in `/account`, and both mock shipments reached `DELIVERED` with the local fulfillment worker enabled. A later test-mode checkout (`HM-2026-000025`) also reached `PAID`, and its signed `payment_intent.succeeded` and `checkout.session.completed` webhook deliveries both returned HTTP 200; its browser return initially showed the backend JSON response, prompting the frontend redirect change. Manual browser retesting confirmed that authenticated customers land on `/account`, successful guest checkout stays on the branded confirmation page, and guest cancellation reaches the branded cancellation page; neither guest path showed backend JSON.

The homepage hero heading “Moon Collection” uses the Tangerine font from Google Fonts, loaded in `frontend/index.html` and scoped to the hero title through its CSS Module. It uses the available bold 700 weight and a slightly enlarged responsive size; only the initial “M” is enlarged to 1.12em. The two-line title block is centered horizontally and vertically in the left hero content area. The C in “Collection” is offset by 1.14em, approximately halfway between the typographic centers of the M and the first o in “Moon.” Earlier visual tests used 1.43em to align the C under the first o and 0.72em to place it around the center of the M. This staggered alignment is being visually reviewed; other headings retain the shared heading font, except for the product-detail product name.

Baseline responsive breakpoints are implemented in the storefront, checkout, account, and POS CSS Modules; the product-detail layout stacks at narrow widths. Storefront drawers are capped at 420px or the viewport width. At widths of 539 CSS pixels and below, cart quantity controls use 44×44px targets; their flex row may wrap to avoid widening the drawer. Browser checks on 2026-10-26 at 320px and 390px CSS widths found no horizontal overflow on the storefront, product details, checkout, account/order history, POS claim form, customer POS demo, authenticated staff POS terminal, or POS order operations page. These checks are a responsive baseline, not complete device/browser coverage or deployment approval.

### Remaining Work and Order of Completion (updated 2026-10-26)

Complete functional scope and acceptance work before final visual styling or
deployment:

1. **Complete:** implement the approved customer-only tester journey. The
   public `/pos-demo` flow creates an anonymous cash-only sale with shared
   stock, existing stock-availability/idempotency rules, and explicit system
   movement attribution. Customers can retrieve and claim the one-time code,
   see claimed purchases separately at `/account/pos-purchases`, and submit
   policy-eligible automatic returns. The return operation uses existing
   unique returned-unit allocations as its double-refund guard and records
   customer request plus `SYSTEM` processing attribution. The staff `/pos`
   and `/pos/orders` authorization boundary is unchanged. Preserve abandoned
   sale behavior: the sale stays paid and units stay sold after the 14-day
   claim-code deadline; only the encrypted delivery copy is cleaned up.
2. **Complete:** customer browser acceptance is complete for the public
   cash sale, ten-second receipt auto-acknowledgement, customer claim, separate
   POS purchase history/detail, automatic eligible return, and updated
   E-Commerce guest-success wording. On 2026-10-23, a staff test-mode POS card
   sale (`HM-2026-000034`, EUR 99.00) in the user's local app produced a
   `payment_intent.succeeded` event forwarded by Stripe CLI to the local
   webhook, which returned HTTP 200; the terminal returned to a blank screen.
   No real payment was taken. The order remains `PAID` and unclaimed, and its
   unit remains sold. A later approved test-mode sale,
   `HM-2026-000035` (EUR 69.00), exposed a delayed-webhook UI race: the
   reservation expired and the POS stopped polling before Stripe CLI replayed
   the successful event. The webhook returned HTTP 200; a read-only query of
   the local application database confirmed the order/payment are `PAID`, the
   fulfillment is `COMPLETED`, and no refund was recorded. The receipt was not
   shown, and the unit is sold. The frontend now keeps a submitted card
   checkout locked after expiry and polls with capped backoff until the
   backend reports a terminal payment outcome. On 2026-10-23, delayed-webhook
   browser acceptance passed using test-mode order `HM-2026-000036`
   (EUR 69.00): the unresolved state remained locked after expiry, Stripe CLI
   replay returned HTTP 200, and the receipt appeared before automatically
   clearing after ten seconds. The order's unit is sold; no live payment was
   taken, and no additional transaction was created.
   These runs used the user's local API/Vite configuration, not the isolated
   pytest API; the configured database identity was not independently
   verified. After a user-approved reset/reseed of only the isolated test
   database, the full backend suite passed all 193 tests.
3. **Gmail OAuth authorized; delivery readiness remains open:** on
   2026-10-23, local Google OAuth completed and the callback saved encrypted
   authorized-user credentials. The local OAuth setup switch was disabled
   again after success. Read-only counts from the API database showed zero
   pending notifications, three failed notifications, three `SHIPPING /
   READY_TO_SHIP` shipments, and five `SHIPPING / DELIVERED` shipments. The
   global delivery flag remained disabled because it starts both notification
   and fulfillment workers. After explicit approval, one synthetic test email
   was sent directly through the existing Gmail client to the configured
   sender mailbox; no queue or shipment was processed by a worker. This
   confirms direct Gmail sending, not queued notification or worker behavior.
   No recipient or credential values were recorded. The Google OAuth app
   remains in Testing mode, so refresh-token expiry and Google's production
   readiness requirements remain unresolved. Do not enable global delivery
   without explicit approval and a separate review of queued work.
   **Read-only worker review update (2026-10-23):** The latest aggregate on
   `half_moon_db` showed 10 notifications `SENT`, 3 `FAILED`, and none
   pending/due/expired/processing; shipments were 3 `SHIPPING / READY_TO_SHIP`
   and 9 `SHIPPING / DELIVERED`. This differs from the earlier snapshot
   (five delivered); the difference is unattributed because the earlier
   runtime/database state was not independently verified. The latest recorded
   API process was restarted with the delivery-worker flag explicitly false.
   The per-shipment failure guard is implemented and the focused fulfillment
   module passed 12 tests. The full backend suite subsequently passed 195
   tests on 2026-10-23. The expired-session cleanup regression compares
   against the expired rows present at cleanup time while verifying its own
   session and token history are removed. A read-only check found one
   pre-existing expired session in `half_moon_test_db`; the test fixture rolls
   back its cleanup, so no persistent test-database row was changed.
   **Notification/shipment review (2026-10-23):** the user confirmed
   `GMAIL_DELIVERY_WORKER_ENABLED=false`. Counts were 10 `SENT`, 3 `FAILED`,
   3 `SHIPPING / READY_TO_SHIP`, and 9 `SHIPPING / DELIVERED`; no
   `PENDING`/`PROCESSING` notifications were reported. The three failed
   notifications each had one attempt and a generic Gmail authorization or
   configuration failure: `ORDER_SHIPPED` for `HM-2026-000021` and
   `HM-2026-000022`, and `ORDER_DELIVERED` for `HM-2026-000021`. Both orders
   are already delivered; leave these historical failures failed rather than
   resending stale status emails. The three ready shipments are paid ECOM
   shipping test orders `HM-2026-000025` through `HM-2026-000027`; the user
   confirmed the recipients are controlled test recipients. The worker was
   not enabled, so no email or shipment status was changed by this review.
   The user wants Gmail active for tester launch but deferred the controlled
   worker test until deployment. The single global flag starts both the
   notification and fulfillment workers; enabling it will progress these
   ready shipments and can send their simulated shipping/delivery emails.
   The Google OAuth consent app remains in Testing. For an External app in
   Testing, Google's [OAuth app-state guidance](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview)
   limits authorization to allowlisted users, and [OAuth token guidance](https://developers.google.com/identity/protocols/oauth2#expiration)
   documents refresh-token expiration. Review the app's actual user type,
   publishing status, and Gmail-scope verification requirements before
   continuous launch use. Storefront testers do not need to authorize Gmail
   unless they connect their own Google accounts.
   **Deployment note:** today's test results are complete; rerun the backend
   suite only if code changes. The database queue snapshot is time-specific,
   so recheck it before enabling workers only if new tester activity changes
   the listed records.
   **Controlled worker acceptance (2026-10-27):** After the user's explicit
   approval, a bounded in-process run verified `half_moon_db`, the seven
   approved shipping orders, and no pending/processing notifications before
   starting the notification and fulfillment workers. All seven reached
   `DELIVERED` while remaining `PAID`; the run sent seven `ORDER_SHIPPED` and
   seven `ORDER_DELIVERED` emails with no send failures. The three historical
   failed notifications remained unchanged. Unrelated cleanup workers were
   disabled for the run, and the worker tasks shut down gracefully. No
   persistent worker configuration was changed.
4. **Partially complete:** responsive browser QA was run on 2026-10-26 at
   320px and 390px CSS widths across storefront, product, checkout, account,
   customer POS, staff POS, and POS operations routes. No horizontal overflow
   was found. Cart quantity controls use 44×44px targets at widths of 539px
   and below; desktop controls remain 30×30px. Remaining visual work is final
   design approval, real-device/browser coverage, and any palette, imagery,
   spacing, or accessibility refinements requested by the user.
5. **Pending:** Render is selected for the tester deployment, but Render
   services and a public domain have not been supplied or verified, and the
   deployment configuration has not been reviewed. Validate the production
   dependency lock on Linux, review the actual Render service configuration
   and migrations, use Stripe test-mode credentials only, verify the unified
   domain's `/api/*` rewrite, redirect URLs, and production catalogue image
   paths, then perform hosted smoke tests. Do not enable live Stripe
   credentials without explicit user approval.
   The 2026-10-27 workspace inventory found no `render.yaml`, Dockerfile,
   Procfile, or GitHub Actions workflow; service setup therefore requires
   explicit Render dashboard configuration or a reviewed deployment manifest.
   `requirements-prod.lock` is present, but a Linux install was not verified:
   Docker is unavailable, and the wheel-only cross-platform check cannot test
   packages that may need source builds.

### Backend

- Python
- FastAPI
- Pydantic v2 for API validation
- Pydantic v2 for API validation; `email-validator` is a direct dependency because authentication schemas use `EmailStr`
- SQLAlchemy 2.x
- Psycopg 3 as the PostgreSQL driver
- Alembic for migrations
- Argon2id through `argon2-cffi` for password hashing
- PyJWT for signed access tokens
- `cryptography` for Fernet encryption and HMAC-related data protection
- REST API using JSON

### Database

- Database: `half_moon_db`
- Separate customer and staff tables in the same database
- PostgreSQL is the single source of truth

### External Services

- Stripe test mode for payments
- Google Gmail API (OAuth 2.0) for transactional emails
- External image URLs
- Mock fulfillment and shipping
- Render is the selected tester-deployment platform. The approved target is
  separate frontend and FastAPI services under one unified domain, with
  `/api/*` rewritten to FastAPI. No Render services, public domain, or hosted
  deployment have been configured or verified.

#### Stripe Test Payments for Deployed Testers

Any deployment intended for testers who must not make real payments must use
Stripe test-mode credentials only, including the matching test-mode webhook
signing secret. Never configure live Stripe credentials for that deployment.
Testers do not need a Stripe account. For ECOM's hosted Stripe Checkout, they
can use the standard successful test card `4242 4242 4242 4242`, any valid
future expiry date, any three-digit CVC, and a postal code if requested. This
test card only works in Stripe test mode and does not move real funds. See
[Stripe's testing documentation](https://docs.stripe.com/testing).

The POS terminal includes a customer-facing secure card-entry UI using
Stripe's Card Element and the public `VITE_STRIPE_PUBLISHABLE_KEY`. Any
deployment for testers must use a Stripe test-mode publishable key together
with matching test-mode backend credentials and webhook signing secret; never
configure live Stripe credentials for the demo.

Mailboxlayer is optional and excluded from the first version. The Google Gmail API handles transactional email delivery; Mailboxlayer validates email quality. Pydantic handles basic email format validation initially.

### Deployment Architecture

The browser uses relative `/api/v1` requests. During local development,
Vite's `/api` proxy forwards requests to `http://127.0.0.1:8000`. The hosted
target is a React static frontend and FastAPI backend under one public origin,
with a Render `/api/*` rewrite to the backend. This same-origin design does
not require backend CORS middleware. The Render services, domain, and rewrite
remain unconfigured and unverified.

The frontend exposes only public client configuration in the reviewed source,
including the Stripe publishable key. Backend credentials belong in the
backend's server-side `Settings` configuration and must not be added to the
frontend bundle. The source scan did not inspect environment/configuration
files.

### Dependency Reproducibility

`pyproject.toml` records supported dependency ranges. `requirements-prod.lock` pins only runtime dependencies; `requirements-dev.lock` pins runtime plus the `dev` extra (tests and development tools). Production must install from the production lock, while local development and tests use the dev lock. Install either lock with `python -m pip install -r <lockfile>`, then install the project itself with `python -m pip install --no-deps .` (production) or `python -m pip install --no-deps -e .` (editable development).

Generate locks with `pip-tools`: `pip-compile --output-file=requirements-prod.lock pyproject.toml`, then `pip-compile --constraint=requirements-prod.lock --extra=dev --output-file=requirements-dev.lock pyproject.toml`. Regenerate both after changing dependencies, validate both locks, and run the test suites. Locks are currently resolved on macOS with Python 3.14; validate or regenerate the production lock for the Linux deployment target before deployment. `.vscode/extensions.json` records recommended extension IDs; extension versions are not runtime dependencies, but notable tooling changes should be documented.

## API Contract Rules

FastAPI route parameter types must match the database model field types used in SQLAlchemy. In this project, catalog and inventory primary keys are UUIDs, so route path parameters such as `product_id` and `variant_id` must use `UUID`, not `int`. When returning those values in JSON, convert them with `str()` before serialization.

The dependency injection pattern used in FastAPI routes (`db: Session = Depends(get_db)`) is the correct framework pattern. Ruff rule `B008` is a known false-positive for FastAPI dependency injection and should be suppressed with a targeted `# noqa: B008` comment when the project intentionally uses framework-standard dependency injection.

Service-layer domain errors are translated by endpoint routes into explicit `HTTPException` responses. Unclassified `SQLAlchemyError` failures are logged at the FastAPI boundary and returned as a sanitized HTTP 500 JSON response; session teardown handles request-scoped cleanup. Stripe webhook database failures are rolled back and re-raised so Stripe can retry, then logged once by the application handler.

The project runtime is Python 3.14.5. Use `datetime.now(timezone.utc)` for UTC timestamps: the selected interpreter does not expose `timezone.UTC`, so an editor suggestion to replace it with `datetime.UTC` is not compatible with this environment.

## Verified API Sequence

The backend implementation is built in the following order and should remain in this order unless the architecture is intentionally changed. Business routes use the `/api/v1` prefix; `/health` remains unprefixed:

1. `GET /health` verifies the application is running.
2. `GET /api/v1/products` returns the product catalog list.
3. `GET /api/v1/products/{product_id}` returns one product plus its variants.
4. `GET /api/v1/products/{product_id}/variants` returns all variants for a product.
5. `GET /api/v1/products/{product_id}/variants/{variant_id}/stock` returns the in-stock availability for a specific variant and groups totals by location.
6. `GET /api/v1/products/{product_id}/detail` is the storefront-oriented product page response and combines product data, variants, and stock availability in one payload.

This sequence is intentionally incremental: each route validates the lower-level model relationship before the next higher-level business view is added.

The stock and product-detail GET routes stage lazy expired-reservation cleanup and flush it so their availability queries see the released units. They do not commit that cleanup; the request-scoped session rolls it back at teardown. Cleanup staged by a mutation workflow is persisted only if that workflow commits its caller-owned transaction.

### Current Backend Implementation

- `POST /api/v1/auth/register` creates a customer and customer account in one transaction.
- `POST /api/v1/auth/login` verifies the Argon2id password hash and rejects accounts without verified email before issuing a short-lived JWT.
- `POST /api/v1/auth/staff/login` verifies seeded staff credentials and returns a JWT containing the staff role.
- `GET /api/v1/auth/me` is protected by the customer bearer-token dependency and decrypts the customer profile response.
- `GET /api/v1/auth/verify-email` validates a single-use verification token and sets `email_verified_at`.
- `POST /api/v1/auth/verify-email/resend` returns the same generic `202` response for every email address. It queues a new encrypted verification notification only for active, unverified customer accounts, enforces a 30-second per-account cooldown, and consumes earlier unused verification tokens before issuing a replacement.
- `POST /api/v1/auth/password-reset/request` returns a generic response, applies the demo 30-second per-customer cooldown, and queues a copyable reset token only for active, email-verified customer accounts.
- `POST /api/v1/auth/password-reset/confirm` validates a single-use 30-minute token, updates the customer password, consumes outstanding reset tokens, and revokes the customer's active refresh sessions.
- Customer and staff bearer-token dependencies verify the JWT and reject missing or inactive accounts. Customer login requires verified email; other email-verification checks remain endpoint-specific.
- `require_staff_role()` provides reusable role authorization and returns `403` for insufficient staff permissions.
- `POST /api/v1/staff/inventory/transfer` is manager-only and transfers any positive quantity of one variant between locations, including quantity `1`.
- `POST /api/v1/orders` creates an authenticated ECOM pending order, selects one fulfillment location, reserves exact serialized units, creates reservation and movement records, and returns the reservation expiry and Stripe Checkout URL. It requires an `Idempotency-Key` header.
- `POST /api/v1/orders/guest` creates an unauthenticated guest ECOM pending order through the shared order builder; it reuses CRM-only customers, requires sign-in for account-backed emails, and updates the buyer's shipping phone only when `recipient_is_buyer` is true. It requires an `Idempotency-Key` header and returns a Stripe Checkout URL. Guest identity reuse, account-backed email rejection, failed-order rollback, registration attachment, same-key replay, and changed-request rejection were verified as recorded in the guest-checkout checkpoints below.
- The transitional authenticated `POST /api/v1/orders/{order_id}/simulate-payment` bypass was removed on 2026-10-05. ECOM payment confirmation now comes from the signed Stripe webhook; successful fulfillment is covered by the webhook tests. Stripe handles declined-card attempt details; Half Moon does not create a separate `Payment` row for each decline. After a decline, Stripe Checkout can remain available for another attempt while the Half Moon payment and order remain `PENDING` and `PENDING_PAYMENT`; existing reservation expiry and late-payment refund rules still apply.
- `GET /api/v1/orders/{order_id}` returns an authenticated customer's own order details, including exact serialized order-line allocations and whether each allocation has already been returned. Each line includes the current catalog variant image, falling back to the current product image. Monetary values are serialized as strings. The E-Commerce customer order list and detail pages omit the duplicate Payment summary and paid-date row while retaining order status and paid-state checks; item photos appear in the order list, return choices, and return history.
- `GET /api/v1/orders/{order_id}/status` enforces customer ownership and returns the current shipment/order fulfillment status without changing state, including pickup readiness, deadline, and collection timestamps. The separately polled worker advances eligible paid ECOM shipping and store-pickup orders, and expires uncollected store pickups only after the inclusive deadline passes.
- `GET /api/v1/orders/{order_id}/returns` returns the authenticated customer's own ECOM or claimed POS return records, including request state, selected serialized units, timestamps, and refund state. This order-scoped lookup allows the customer UI to recover return state after navigation or a lost response; other customers' orders remain hidden.
- `POST /api/v1/orders/{order_id}/returns` creates a verified customer's return request for eligible ECOM orders and selected purchased allocations. ECOM shipping returns require a paid, delivered order and use `Shipment.delivered_at` for the inclusive 14-day request window; ECOM store-pickup returns use the approved inclusive window anchored at `picked_up_at`. This E-Commerce route continues to reject POS orders.
- Customer POS routes are `POST /api/v1/orders/pos-demo` for cash-only anonymous checkout, `GET /api/v1/orders/pos-demo/{order_id}/receipt` and `POST /api/v1/orders/pos-demo/{order_id}/receipt/acknowledgement` for short-lived one-time code delivery, `POST /api/v1/orders/pos-claim` for verified-customer claiming, `GET /api/v1/orders/pos` for claimed customer-owned history, and `POST /api/v1/orders/pos/{order_id}/returns` for automatic eligible return processing. Owner-scoped `GET /api/v1/orders/{order_id}` and `GET /api/v1/orders/{order_id}/returns` support E-Commerce and claimed POS orders; the E-Commerce return-request `POST /api/v1/orders/{order_id}/returns` remains ECOM-only. Existing staff routes remain protected: `POST /api/v1/staff/pos/checkout`, staff order status and receipt routes, `GET /api/v1/staff/pos/orders/lookup?order_number=...`, `POST /api/v1/staff/pos/orders/{order_id}/returns`, and manager-only claim resets by UUID (`POST /api/v1/staff/pos/orders/{order_id}/claim/reset`) or order number (`POST /api/v1/staff/pos/orders/claim/reset`). The order-number reset returns reset status only and does not disclose an unclaimed order's details. Staff returns retain staff attribution. Customer-triggered POS returns record a customer request and `SYSTEM` processing event; the service atomically simulates the original-payment refund and restores exact selected units to their original sale locations. Five failed customer claim attempts create a 30-second lock; a later attempt after expiry resets the counter and proceeds, while a manager may reset early. Legacy locked claims without a cooldown timestamp still require manager reset. These additions reuse the existing schema and constraints for the customer-only flow; the previously applied claim-cooldown migration adds `locked_until`.
- `POST /api/v1/returns/{return_id}/process` remains the customer-triggered one-step completion route for ECOM returns. It marks receipt and inspection, restocks the item, creates an original-payment refund, and updates payment/order-level `payment_status`. Customer POS returns are automatically completed through the dedicated owner-authenticated POS route; staff POS returns use their separate staff-authorized route.
- The active return API, service, and models now permit original-payment refunds only. Forward migration `a6f90c12d435` removed the ledger and narrowed constraints in both configured databases. See the 2026-10-06 store-credit retirement implementation update below.

Customer registration writes an encrypted email-verification notification to the outbox in the same transaction as the account and verification token. Gmail credentials loading, MIME/API sending, notification processing, verification-email formatting, and order-status email formatting are implemented. The verification and order-status senders use the opt-in worker, gated by `GMAIL_DELIVERY_WORKER_ENABLED` (default off); tests force the setting off. The verification email retries transient failures only while its token remains valid. Non-expiring order-status notifications retry transient failures indefinitely with capped backoff; permanent authorization/configuration failures are not retried. Tests mock Gmail. The user does not want a separate account-registration confirmation email. The `/verify-email` page submits the one-time token to the confirmation endpoint, displays its success or failure message, removes the token from the URL after success, and links to sign-in. The verification subject and body were finalized and tested. The OAuth setup flag is disabled.

Mutation services use shared `commit_or_rollback` and
`flush_or_rollback` helpers, which roll back and re-raise SQLAlchemy errors.
The reservation expiry helper is transaction-neutral and has no internal
commit; read services that release expired reservations conditionally commit
the cleanup. Checkout and return workflows validate
inputs and exact `Decimal` totals before changing inventory or staging
append-only movement rows. Flows that lock these record types follow Order ->
Payment -> Reservation -> InventoryUnit order. Multi-item ECOM checkout, POS
allocation, and return unit locks are acquired in deterministic UUID-string
order. ECOM and POS inventory selection uses PostgreSQL `SKIP LOCKED` and
continues only when one eligible source can fulfill the complete order;
otherwise, checkout is rejected without a partial reservation. A two-session
PostgreSQL regression test covers both ECOM/POS lock-holder directions and
confirms that only one active reservation and reservation movement are
created for a serialized unit. POS card-provider failure and
`payment_intent.payment_failed` tests confirm the unit remains reserved with
only its reservation movement; `payment_intent.canceled` restores stock with a
matching release movement and no sale allocation. The focused POS and ECOM
checkout suites passed (`64 passed`). The active-reservation partial unique
index on `inventory_unit_id` where `status = 'ACTIVE'` is applied to local
primary `half_moon_db`.

Automated fulfillment uses a separate one-second polling task so Gmail requests cannot delay shipment progression. After Stripe confirms payment, it locks one eligible paid ECOM shipment and advances at most one stage per pass. A non-database exception while progressing a selected shipment rolls back partial changes, then re-locks and marks that same shipment `FAILED` only if it still has the originally selected status. `SQLAlchemyError` is rolled back and re-raised so the fulfillment worker retains its database backoff behavior. This prevents one invalid record from being selected repeatedly without turning a transient database error into a permanent shipment failure. The focused fulfillment module passed 12 tests on 2026-10-23, including regressions for invalid Fernet ciphertext quarantine and SQLAlchemy errors remaining retryable. Shipping advances from `READY_TO_SHIP` to `SHIPPED`, then to `DELIVERED` after the configured delay. Store pickup advances from `READY_FOR_PICKUP` to `PICKED_UP` after the shared mock delay and only through its inclusive deadline; the transition and `ORDER_PICKED_UP` outbox record commit together. After the deadline passes, the same worker cancels uncollected pickups, records the simulated original-payment refund, restores units to their source with append-only `PICKUP_EXPIRY` movements, and enqueues `ORDER_PICKUP_EXPIRED` atomically. Successful pickup payment creates the shipment readiness/deadline and enqueues `ORDER_READY_FOR_PICKUP` within the payment transaction. The authenticated status endpoint is read-only and returns pickup timestamps. Shipping and pickup email formatting are implemented; pickup formatting and lifecycle paths have focused tests, but live Gmail delivery of pickup emails has not been verified. The controlled local Postman shipping test completed on 2026-10-06: Stripe's signed webhook was replayed to the local app, order `HM-2026-000018` reached `PAID` and `DELIVERED`, and the user confirmed receiving exactly one shipped and one delivered email. The focused fulfillment, notification, lifespan, and Stripe regression tests passed (`32 passed`), followed by a passing worker-lifespan test (`1 passed`). A worker startup also progressed two older paid shipments (`HM-2026-000007` and `HM-2026-000013`), generating one shipped and one delivered email for each. Future controlled worker enablement must check both queued notifications and eligible paid shipments; an empty outbox alone does not ensure a worker has no backlog. Customer and staff refresh-token cookie sessions are implemented with opaque database-backed tokens, strict device-scoped replay revocation, CSRF protection, fixed 30-day customer and 12-hour staff lifetimes, current-device logout, and expired-session cleanup. The migration is applied to both local databases at verified Alembic head `a41c7e92b6d3`; focused refresh-session service and route tests passed (`6 passed`), and the full project suite passed (`144 passed`) on 2026-10-09. Customer password reset is implemented in source for active, email-verified accounts with generic reset requests, a demo 30-second resend cooldown, copyable single-use 30-minute tokens, and active-session revocation; the password-reset and related auth focused run passed (`14 passed`). The full project suite passed 173 tests on 2026-10-09 after notification/fulfillment failure-isolation, worker-supervision, and regression-test updates; the latest full backend run passed all 195 tests on 2026-10-23.

Inventory transfers use one generic bulk operation. The previous single-unit transfer and warehouse-only replenishment paths were removed because a bulk request with `quantity: 1` already handles a single item while supporting larger transfers in either direction. Every selected serialized unit receives its own append-only movement record in the same transaction.

Inventory movements remain append-only transactional history. A staff-facing movement-history API is explicitly out of scope and will not be implemented. Inventory-changing endpoints remain role-restricted.

### Simplification Checkpoint: 2026-09-29

The first five simplification candidates were reviewed and implemented where appropriate:

- Catalog serialization is centralized in `backend/app/services/catalog.py`.
- Location-stock aggregation is centralized in the same catalog service.
- Catalog and stock-read routes delegate database and response logic to that service. The misleading temporary `/reserve` endpoint was removed; stock reads only report availability, while `POST /api/v1/orders` creates the persisted checkout reservation.
- At this checkpoint, the inventory-movement reference audit found zero non-null `order_id` or `return_id` values without matching rows. The model update and migration were still pending at the time; migration `818ca3c44540` was subsequently reviewed and applied on 2026-10-01, adding named nullable foreign keys with `ON DELETE RESTRICT`.
- Customer and staff authentication share email normalization, HMAC lookup, and password-status verification helpers while retaining separate security flows.
- Customer and staff token parsing share one subject/UUID helper while staff role validation remains separate.

This refactor preserves the API contracts and database schema. The migrations, seed data, and inventory transfer model were not changed by the simplification.

## Business Configuration

- Business location: Italy
- Currency: EUR
- Standard VAT: 22%
- Tax-free result: 0%
- Variant `retail_price` values include VAT and are customer-facing gross prices
- Demonstration ECOM return window: 14 calendar days from delivery, inclusive through the 14-day deadline (implemented)
- Demonstration POS return window: 14 calendar days from `paid_at`, inclusive through the deadline (implemented)
- Discounts and vouchers: explicitly out of scope for ECOM and POS v1 and will
  not be implemented; no discount is applied
- Checkout reservation duration: 3 minutes

Tax behavior demonstrates an application workflow. Real Italian fiscal certification, electronic invoicing, government reporting, and legal tax eligibility rules are outside the initial scope.

## Money Representation

Money must use exact decimal arithmetic.

- PostgreSQL: `NUMERIC(12, 2)` or an equivalent exact numeric type
- Python: `Decimal`
- No Python or JavaScript binary floating-point arithmetic for financial calculations
- API values must be validated and converted carefully at the backend boundary

Example:

```text
EUR 850.00
VAT 187.00
Total 1,037.00
```

The reason is that binary floating-point values cannot represent many decimal fractions exactly. For example, repeated calculations with `0.1` can produce small precision errors. Exact decimal values prevent incorrect totals, VAT, and refunds.

Because retail prices include VAT, taxable totals calculate the net amount in reverse: `net = gross / (1 + VAT rate)`, then `VAT = gross - net`. No discounts are applied in v1. If a discount feature is separately approved in the future, it must reduce the customer-facing gross amount before reverse VAT extraction. Tax-free totals remove the included VAT portion from the eligible gross amount, so a VAT-inclusive retail price is not incorrectly charged again as a tax-free gross price.

For multiple order lines, net and VAT values are rounded to two decimals per line before totals are summed, preventing unexplained receipt rounding differences.

## Catalog and Inventory

The catalog hierarchy is:

```text
Product
  -> Variant
      -> Serialized inventory unit
```

A variant may differ by material, color, size, and price.

Example:

```text
Product: Moon Ring
Variant: Gold, Italian 14
SKU: MRG-G-14
EAN: 2222222222223
Retail price: EUR 850.00
```

Every jewelry item has a unique serial number:

```text
MRG-M-0001
MRG-M-0002
MRG-M-0003
```

The EAN identifies the sellable variant. The serial number identifies the physical unit.

Product records contain the shared catalog information: name, description, category, and slug. Product name and description are both customer-facing but serve different purposes: the name identifies the item briefly, while the description explains it in detail. A product can have a default image, while a variant can have its own image when color or material changes the appearance.

Products do not have a status column initially. All seeded products are available catalog records. Whether a product can be purchased is derived from its variants and serialized units with status `IN_STOCK`. A future migration can add product or variant visibility if the application later needs to hide catalog records without deleting them.

The schema remains normalized rather than placing products, variants, and physical units in one large table. Product information is stored once, sellable variant information is stored per variant, and serial/location/status information is stored per physical unit.

Variants represent the sellable combinations of color or material and size. For example, `Moon Ring` can have Gold/Italian 14 and Silver/Italian 14 variants. Gold and silver are variants of the same design, not separate products. Moon Ring sizes are stored as Italian labels 10, 12, 14, 16, 18, and 20, with `size_system` unset. The original EU equivalents are 50, 52, 54, 56, 58, and 60 respectively.

The variant's financial field is `retail_price`. Acquisition-cost tracking and unit-level cost fields are out of scope. Migration `d8c72a4f91be` drops the former `acquisition_cost` column and its stored values; those values cannot be recovered by downgrade.

Products and initial inventory are inserted through database seed data. There is no staff-facing product creation, price-management, or stock-receiving interface.

Initial locations:

```text
MAIN_WAREHOUSE
PHYSICAL_STORE
```

POS checks `PHYSICAL_STORE` inventory first and falls back to `MAIN_WAREHOUSE` when the shop has no available unit. When the shop has the unit, the customer pays and takes the jewelry immediately. E-commerce checks only `MAIN_WAREHOUSE` during normal operation. If warehouse availability reaches zero, e-commerce can fall back to `PHYSICAL_STORE`. When the shop fulfills an e-commerce order, the customer can receive a shipment from the shop or choose store pickup when that option is available.

Availability is calculated live:

```text
Available stock = count of serialized units with status IN_STOCK
```

Inventory unit statuses are mutually exclusive, for example:

```text
IN_STOCK
RESERVED
SOLD
RETURNED
REPAIR
DAMAGED
LOST
```

No editable stock total is maintained separately. A variant reaching zero available units does not erase or nullify the locations of its serialized units; each unit retains its current location until it is sold, transferred, returned, or otherwise processed.

### Initial Seed Catalog

The initial catalog contains four product designs: `Half Moon Necklace`, `Moon Ring`, `Moon Bracelet`, and `Crescent Earrings`. Necklace, bracelet, and earrings each have Gold and Silver variants, with 500 serialized units of each color at each location. The ring has EU sizes 50, 52, 54, 56, 58, and 60 in both Gold and Silver, with 500 serialized units per size and color at each location.

This produces 9,000 serialized units per location and 18,000 units across `PHYSICAL_STORE` and `MAIN_WAREHOUSE`. Each Gold and Silver variant has one temporary mock image URL shared across its ring sizes where applicable. These placeholder URLs can later be replaced with final product images without changing the schema.

The approved VAT-inclusive retail prices for the initial seed catalog are:

```text
Half Moon Necklace Gold: EUR 189.00
Half Moon Necklace Silver: EUR 129.00
Moon Ring Gold, all sizes: EUR 169.00
Moon Ring Silver, all sizes: EUR 99.00
Moon Bracelet Gold: EUR 139.00
Moon Bracelet Silver: EUR 79.00
Crescent Earrings Gold: EUR 119.00
Crescent Earrings Silver: EUR 69.00
````

The local database's 12 Moon Ring variants have been updated to the fixed prices above. The catalogue seed script only creates missing variants; changing the CSV does not update prices for variants already present in an existing database.

The initial seed uses temporary `placehold.co` image URLs: one gold image and one silver image for each product design. Ring sizes share the image for their material. Serialized units use the readable format `{SKU}-{ORIGINAL_LOCATION_SHORT_CODE}-{SEQUENCE}`, such as `MRG-G-14-STORE-000001` and `MRG-G-14-WH-000001`. A controlled development-data migration may rename a serial number when its SKU changes; `inventory_unit_serial_aliases` retains the old and new serial strings against the same inventory-unit UUID. Back up the development database before conversion; Alembic downgrade is blocked while serial aliases exist, so rollback requires restoring the pre-conversion backup. The embedded location code identifies the original or intake location and never changes after a transfer; the current location is always read from `inventory_units.location_id` and the related `locations` row.

## Customers and Staff

Customers and staff are separate concepts in the same PostgreSQL database.

### Customer Rules

- Guest checkout is supported
- Guest checkout creates or finds a CRM customer
- Guest order creation uses a separate unauthenticated endpoint; authenticated order routes remain protected
- Guest and authenticated order creation reuse the same order-creation service
- Emails are normalized to lowercase
- Registration can attach to an existing guest record
- Guest orders become visible after email verification
- Customers can have multiple addresses
- Orders retain address snapshots
- Buyers and gift recipients are separate CRM identities
- POS checkout may link a customer through CRM lookup, but permits an anonymous sale. The public customer demo is cash-only and accepts only variant IDs and quantities; checkout prices, stock selection, and sale completion remain backend-authoritative and idempotent. Reservation and sale movements use the `SYSTEM` actor when no staff user is present. A registered customer may claim an anonymous POS sale using its order number and one-time receipt code; only the HMAC digest is stored in the permanent claim record, and successful claim consumes it. The receipt's encrypted delivery copy is available until acknowledged or the inclusive 14-day claim deadline; the public demo acknowledges automatically after ten seconds. Claiming and purchase history require an active, email-verified account. Claimed POS purchases are separate from E-Commerce history. POS returns are allowed for 14 days from `paid_at`, inclusive through the deadline. Eligible customer returns are automatically processed as simulated original-payment refunds, with exact-unit restocking to the original sale source and explicit `SYSTEM` audit attribution. A uniqueness constraint on returned unit allocations prevents duplicate refunds.
- Tax-free sales and return requests require a registered customer account with a verified email

Gift purchases keep the buyer as the order customer. Gift-recipient details attach to the relevant order line, allowing multiple recipients in one order. A recipient may link to an existing customer after email verification or remain a pending recipient contact. Recipients do not see buyer payment details or purchase prices. Refund-only returns go to the original payment method; recipient-initiated refunds remain unavailable unless the recipient is also the authenticated order customer. Because returns require a registered verified account, an unverified recipient remains unable to initiate a return.

Guest checkout design decision (2026-10-02): use a separate unauthenticated guest-order endpoint and keep the existing authenticated order endpoint protected. Both paths reuse the order-creation service and resolve the buyer through the existing normalized-email, HMAC lookup, and encrypted-email pattern. The guest request requires the buyer's email; buyer first and last names are optional and remain distinct from the shipping recipient. A required `recipient_is_buyer` field represents the checkout choice “Who should receive the order? Me / Someone else.” When true, buyer names come from the explicit buyer fields and the shipping phone may update the buyer's CRM phone; when false, recipient details remain only in the order-address snapshot. A matching email HMAC identifies a CRM record but does not authenticate the requester: guest checkout may update provided fields on CRM-only customer records while preserving omitted fields, but profile changes for a customer with an account require an authenticated flow. If the guest email matches a customer with an existing `CustomerAccount`, require sign-in and use the authenticated order endpoint; never attach an unauthenticated guest order to an account-backed customer. `release_expired_reservations` is transaction-neutral: guest checkout stages and flushes expired releases so its availability query sees them, then persists them only with the caller-owned order transaction. Guest checkout then resolves or creates the customer and creates the order using the same post-cleanup order-building logic as authenticated checkout. Guests pay through a Stripe-hosted Checkout Session; Stripe test mode supports this flow with test payment methods. A signature-verified, idempotently processed webhook is authoritative for payment confirmation and fulfillment; a browser redirect alone never confirms payment. The existing payment-simulation and order-status routes remain customer-authenticated, and guest orders become visible through a customer account only after email verification.

Saved customer addresses are separate from order-address snapshots. A guest checkout can use an address without saving it to the customer profile. When an order is created, the selected delivery and billing addresses are copied into immutable order snapshots so later profile changes do not alter historical orders. Address data supports Italian and international customers through country code, region, and postal code fields.

### Staff Roles

```text
SALES_ASSOCIATE
STORE_MANAGER
```

Staff accounts are seeded or manager-created. There is no public staff registration.

Staff accounts are simulated demo identities, not real shop staff. Both `SALES_ASSOCIATE` and `STORE_MANAGER` may create ordinary POS sales; exceptional operations may remain manager-only. The normal customer demo does not require real staff activity.

## Authentication and Security

- Customer and staff login routes are separate
- Passwords use Argon2id
- JWT access tokens are short-lived
- Refresh tokens use secure `HttpOnly` cookies
- The host-only refresh cookie is scoped to `/api/v1/auth`; the non-`HttpOnly` `half_moon_csrf` cookie uses `Path=/` so storefront JavaScript can read it and send it in `X-CSRF-Token`. Both cookies use `SameSite=Lax` and the configured Secure flag; logout deletes each cookie at its matching path.
- One simulated store till is supported: `POS_TILL`
- Staff role switching occurs through logout and login on that till
- MFA is not required initially
- Demo login profiles are deterministic, not random

Data protection:

- Passwords: Argon2id hashes
- Email lookup: HMAC fingerprint
- Email display and sending: encrypted email value
- Personal-data encryption uses the Fernet key in `PII_ENCRYPTION_KEY`.
- Email lookup uses the separate secret in `EMAIL_LOOKUP_HMAC_SECRET`.
- POS receipt-claim codes use a dedicated HMAC key in
  `POS_CLAIM_CODE_HMAC_SECRET`.
- Payment-card data: never stored
- Secrets: environment variables only
- Completed business records: retained for audit integrity

Before any public deployment, generate and configure fresh deployment-only
credentials and secrets; do not reuse local or test values. Rotate database
credentials, JWT signing secret, Stripe API and webhook secrets, Google OAuth
client secret and Gmail authorization, and demo staff passwords. Generate
deployment-specific personal-data encryption and email/POS HMAC keys only
with a reviewed data migration or a deliberate reset of non-production data:
changing the encryption key without re-encrypting stored values can make them
unreadable, and changing lookup HMAC keys without recomputing fingerprints can
break record lookup. Verify the deployment configuration without displaying
secret values, and keep all credentials out of source control and support
messages.

Local credential rotation checkpoint (2026-10-06; user-reported, values
intentionally omitted): the Gmail OAuth client secret was replaced, a new
local authorization completed, the former client secret was removed, and
local OAuth setup was disabled again; the Gmail delivery worker remains off.
The Stripe test API key was replaced, authenticated successfully, and the old
key was revoked. The local JWT and POS claim-code HMAC secrets were replaced.
The active manager and sales-associate password hashes in `half_moon_db` were
updated through DBeaver, and both Postman logins returned HTTP 200.

The PII encryption key and email-lookup HMAC key were not rotated; changing
either requires a deliberate data migration or reset plan. The Stripe CLI
listener signing secret was saved again locally but is stable across listener
restarts, so it was not independently rotated. It is only for local event
forwarding; configure a fresh Dashboard endpoint signing secret before any
deployment. Repeat the full deployment-specific credential rotation before
public launch.

Local demo authentication uses deterministic staff accounts. The current local/demo seed addresses are `store_manager@halfmoon.com` for `STORE_MANAGER` and `sales_associate@halfmoon.com` for `SALES_ASSOCIATE`. Their passwords come from ignored environment variables `DEMO_MANAGER_PASSWORD` and `DEMO_SALES_PASSWORD`; the seed never prints or emails those passwords. Customer registration accepts the tester's own email during private testing, while public demo staff addresses must be reviewed or replaced before deployment.

Customer deletion should anonymize personal information while preserving legally important order and payment history.

## Main Business Processes

### 1. Catalog and Inventory

The developer seeds products, variants, prices, EANs, image URLs, serial numbers, locations, and initial statuses.

### 2. Customer Identity

Guest, registered, e-commerce, and POS purchases connect to one customer CRM record.

### 3. Authentication

Customers and staff use separate authentication flows and permissions.

### 4. Checkout and Reservation

```text
Cart
  -> 3-minute serialized-unit reservation
  -> Payment
  -> Webhook confirmation
  -> Sale committed or reservation released
```

The reservation must be transaction-safe so e-commerce and POS cannot sell the same item.

Expired reservations can be released lazily during normal requests. This avoids requiring a separate paid worker or scheduler.

If Stripe reports successful payment after a reservation has expired, the backend must re-check the reservation and unit inside a transaction. It may complete the sale only if the unit is still safely available; otherwise it must not fulfill the order and must automatically refund the late payment.

### 5. Fulfillment and Returns

Order, payment, and shipment state are separate contracts. `order_status` is
the customer-facing lifecycle; `payment_status` is the payment/refund fact;
`Shipment.status` remains the operational shipment record. Successful payment
persists `PAID` before the fulfillment worker advances ECOM orders:

```text
ECOM shipping:     PENDING_PAYMENT -> PAID -> READY_TO_SHIP -> SHIPPED -> DELIVERED
ECOM store pickup: PENDING_PAYMENT -> PAID -> READY_FOR_PICKUP -> PICKED_UP
POS cash/card:     PENDING_PAYMENT -> COMPLETED
Unpaid expiry:     PENDING_PAYMENT -> EXPIRED
Payment failure:   PENDING_PAYMENT -> CANCELLED
Pickup expiry:     READY_FOR_PICKUP -> CANCELLED (payment_status=REFUNDED)
```

The payment webhook commits the ECOM order as `PAID`, creates its ready shipment, and commits inventory atomically. A later fulfillment-worker transaction advances `order_status` to `READY_TO_SHIP` or `READY_FOR_PICKUP`. If the existing global worker flag is disabled, the order remains `PAID` until the fulfillment worker runs. For store pickup, that transition sets `ready_for_pickup_at`, calculates `pickup_deadline_at` as seven calendar days later in `Europe/Rome`, and enqueues `ORDER_READY_FOR_PICKUP` atomically. The worker advances ready shipping to `SHIPPED` then `DELIVERED`, and pickup to `PICKED_UP`, updating `order_status` and `Shipment.status` together. The authenticated status endpoint is read-only and returns both states plus shipment timestamps. Failed fulfillment work quarantines only `Shipment.status` as `FAILED`; the last successfully committed order status remains intact.

The first version fulfills each ECOM order from one source location only. ECOM prefers `MAIN_WAREHOUSE` and falls back to `PHYSICAL_STORE`; POS prefers `PHYSICAL_STORE` and falls back to `MAIN_WAREHOUSE`. If no single source can fulfill the complete order, checkout cannot complete. Split shipments are out of scope initially. A paid ECOM store-pickup order must be sourced from `PHYSICAL_STORE`. Pickup progression uses the inclusive deadline; expiry occurs only after that deadline passes. Expiry sets both order and shipment status to `CANCELLED`, marks payment and order payment status `REFUNDED`, restores sold units to the original shipment source, appends one `PICKUP_EXPIRY` movement per unit, and enqueues `ORDER_PICKUP_EXPIRED` in the same transaction. The approved expiry email explicitly states that the refund is simulated and no payment-provider refund was issued. Pickup-order returns use the shared workflow with the inclusive 14-day window anchored at `picked_up_at`.

If a terminal pickup-expiry integrity check fails, the worker rolls back the
refund, inventory, and notification work, then marks only the shipment
`FAILED` and records one sanitized system audit event in a separate transaction.
Failed shipments are excluded from future polling; the order, payment, and
inventory remain unchanged for manual reconciliation.

A completed in-store POS sale has `order_status=COMPLETED` and `payment_status=PAID`; its serialized units become `SOLD`. The shared order model and configured schema include immutable UTC `paid_at`. Timestamp transitions use `datetime.now(timezone.utc)`. POS returns are allowed for 14 days from `paid_at`, inclusive through the deadline, and use the shared request/process endpoints described above. POS sales do not enter the later-collection pickup workflow; `PICKED_UP` is reserved for orders collected later, such as ECOM orders sent to a store for pickup.

The target return lifecycle is:

```text
Return requested
  -> Approved
  -> Received
  -> Inspected
  -> Refund to original payment method
```

Returns require a registered customer account with verified email and reference the exact purchased `order_line_units` allocation. ECOM shipping return requests are accepted only within 14 days of the shipment's `delivered_at` timestamp, inclusive through the deadline. ECOM store-pickup return requests use the same inclusive 14-day window anchored at `picked_up_at`. The current ECOM simulator combines approval, receipt, and inspection in one processing call and always restores the unit to `IN_STOCK`. Return requests permit only refunds to the original payment method. POS return processing and automatic pickup expiry/refund/restock are implemented; repair and damage outcomes are not implemented. Refunds are simulated and recorded against the original payment; no real refund API call is made.

Customers submit return requests. For this portfolio demo, they also trigger simulated receipt, inspection, and completion through the dedicated processing endpoint; it only processes the authenticated customer's own return.

Manual ECOM return acceptance (2026-10-19): the signed-in customer submitted a return for the Half Moon Necklace in delivered order `HM-2026-000022`. The UI reported the return completed and recorded a simulated refund of EUR 129.00; no real Stripe refund was sent. The order page then showed that all items already had return requests. That acceptance revealed that the required free-text reason was not obvious, so the customer return form now requires a category (damaged, wrong size, wrong colour, or other) and accepts optional details; details are required for "Other". The category and details are combined into the existing `reason` string, which remains capped at the backend's 500-character limit; no API or database contract changed. Manual browser acceptance on 2026-10-19 for order `HM-2026-000018` confirmed that a standard category permits blank details, "Other" requires details, and the submitted reason `Other: Test: Other reason details` appears in return activity. The return completed with a simulated EUR 189.00 refund; no real payment-provider refund was sent.

### 6. Customer and Order Records

The system stores customer, order, channel, product, serialized-unit, tax-free transaction, gift-recipient, return, refund, inventory-movement, and audit-event records for transactional workflows. No reporting views, reports, or reporting endpoints are in scope. Existing discount snapshot columns remain zero; discount and voucher functionality is deferred for both channels.

Orders retain their channel:

```text
ECOM
POS
```

Discount application and approval workflows, including the previously proposed EUR 100 threshold, are deferred and are not part of ECOM or POS v1.

## Automated Business Simulation

The project is self-running. Customers do not manually mark shipments, approve returns, inspect jewelry, or update inventory. Discount approval is not part of v1 because discounts are not applied.

The source implementation no longer issues store credit and permits only simulated refunds to the original payment method. The ledger and legacy constraints have been removed from both configured databases by the data-reconciling forward migration. Store-credit balance lookup and redemption were never implemented.

After a customer action, the backend simulates the business response.

Example:

```text
Customer payment succeeds
  -> Payment is confirmed
  -> Serialized unit becomes SOLD
  -> Mock shipment is created
  -> Shipment becomes SHIPPED
  -> Shipment becomes DELIVERED
  -> Gmail API notification is sent
```

Mock fulfillment and return scenarios remain deterministic. Payment successes and card declines are exercised through Stripe test mode rather than a client-controlled payment-result endpoint. Half Moon does not duplicate each declined card attempt in its own payment records; shipment and refund-failure scenarios remain developer-controlled demonstrations where applicable.

Shipping progression requires a persistent background worker to advance shipments automatically for signed-in and guest ECOM customers. A separate fulfillment poller checks once per second so Gmail calls cannot delay shipment progression. Both workers are gated by `GMAIL_DELIVERY_WORKER_ENABLED`, which defaults off and is forced off in tests. Enable it only for controlled local testing or live deployment, and use hosting that keeps the worker running.

For a controlled local run from the repository root, stop the existing backend and start it with `GMAIL_DELIVERY_WORKER_ENABLED=true .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload`. This process-scoped override starts both workers; stop and restart the backend without the override after testing to return to the default-off behavior.

For the Render backend service, explicitly set `GMAIL_DELIVERY_WORKER_ENABLED=true` in its Environment settings and provide the Gmail sender, OAuth client credentials, and authorized-user credentials JSON as server-side secrets. If this flag is omitted or false, neither the Gmail notification worker nor the fulfillment worker starts. Keep the service process persistent, inspect queued notifications and eligible paid shipments before enabling, and verify a controlled delivery and its `SENT` notification status before customer launch. OAuth credentials must remain valid; Gmail external apps left in Testing have refresh tokens that expire after seven days, so complete the documented Google OAuth production-readiness steps before launch.

The pytest harness uses the isolated `half_moon_test_db` on port `55432` and
replaces app secrets and integration credentials with test-only values before
importing application settings. It never connects to `half_moon_db`. In the
current plan, `half_moon_db` is the local primary database and intended hosted
development database after frontend completion; `half_moon_test_db` is the
isolated pytest database. Both currently run locally, and no credentials are
copied between them. Never point pytest at `half_moon_db`.

## Payments and Integrations

### E-commerce

Use Stripe test mode. Never use real card data in the test environment.

Use Stripe test PaymentMethods, such as `pm_card_visa`, rather than putting card details into server-side code.

ECOM uses Stripe-hosted Checkout Sessions. The redirect back to the application is not proof of payment; the signature-verified webhook is authoritative for payment confirmation and fulfillment.

Authenticated and guest ECOM order requests require a client-generated `Idempotency-Key`. The database stores its SHA-256 hash with the order and a fingerprint of the normalized request and identity; an identical retry reuses the order, while reuse with a changed request or identity is rejected. Stripe Checkout Session creation uses a stable versioned key derived from the persisted order ID. The pending Stripe payment stores the Checkout Session ID, URL, and expiration so a retry can recover a session after an ambiguous network failure. Before returning a saved Session, checkout rejects orders that are no longer `PENDING_PAYMENT`; Postman verified an expired-order retry returns `409` rather than the old Checkout URL. The Stripe request omits `payment_method_types`; Dashboard settings control eligible methods and must have only Cards enabled for the approved cards-only ECOM v1. The intended Session expiration is persisted before the Stripe network call and reused on retry. The database transaction is committed before the Stripe network call. If session creation fails, the reservation is not immediately released; the client retries with the same application key. Postman verified same-key order reuse, matching Checkout URLs across two exact retries, and rejection of a changed guest request.

Stripe Checkout Sessions cannot be configured to expire less than 30 minutes after creation, while inventory reservations last 3 minutes. Preserve the business reservation duration. On a late successful payment, the webhook must re-check order and inventory state transactionally; complete the sale only if the unit is still safely available, otherwise refund the payment. Checkout returns use the frontend `/checkout/success` and `/checkout/cancelled` routes, derived from `FRONTEND_PUBLIC_URL` unless `STRIPE_CHECKOUT_SUCCESS_URL` or `STRIPE_CHECKOUT_CANCEL_URL` is explicitly configured. These pages are informational only and must not change payment or order state; the signature-verified webhook remains authoritative. The success return preserves Stripe's Checkout Session ID for correlation and exposes `GET /api/v1/checkout/status`, which returns only the matching ECOM order/payment states and no customer or order details. The frontend removes the Session ID from the visible URL and checks the persisted state every two seconds for at most 30 seconds; it displays “Thank you for your order, your payment has been successful.” only when the order's persisted `payment_status` is `PAID`. If the webhook is later or the status cannot be checked, the page keeps a pending/error message instead of claiming success.

### POS

The POS supports:

```text
CARD
CASH
```

POS checkout is a simulated application flow with simulated staff identities; it does not depend on a real shop, real staff operation, or physical terminal. Both `SALES_ASSOCIATE` and `STORE_MANAGER` may create ordinary POS sales, and the checkout route has regression coverage for both roles. The checkout request schema rejects malformed variant UUIDs, non-positive quantities, client-supplied catalog prices, and all unapproved fields; route tests verify these rejections have no database side effects, and checkout snapshots preserve zero order/line discounts. A cross-channel regression test compares ECOM and POS financial snapshots at standard and rounding-edge prices and confirms later catalog-price changes do not alter placed orders.

The POS card frontend uses Stripe's Card Element with the shared Stripe account/mode and the public Vite setting `VITE_STRIPE_PUBLISHABLE_KEY`; the secret key remains backend-only. The card request contains variant IDs and quantities, never prices or card data. Exact retries reuse the same `Idempotency-Key`; after card confirmation, the frontend polls the staff status endpoint and displays the receipt only after it reports `order_status=COMPLETED` with a settled payment. While unpaid, the basket stays locked; a declined attempt can be retried on the same PaymentIntent until the existing three-minute reservation expires. If the reservation expires after card confirmation was submitted, expiry alone is not terminal for the UI: it keeps the checkout locked and polls with capped backoff until the backend reports payment completion or refund. If the result remains unresolved, staff must reconcile it before starting another sale. An order that expires before card submission, or is explicitly cancelled, remains terminal. No POS card-cancel endpoint or UI action is included in this increment. Manual Stripe test-mode acceptance completed on 2026-10-19 for order `HM-2026-000024` (EUR 69.00); the signed success webhook returned HTTP 200 and the receipt appeared. No real payment was taken.

POS `CASH` records payment as paid immediately in the backend and ends with `order_status=COMPLETED`. Route and service tests verify the order and payment become completed/paid during checkout, the reserved unit is allocated and sold once, and replay does not duplicate the payment or sale movement. Paid-time regression tests confirm a pending order starts with `paid_at=NULL`, completion sets an aware timestamp, and cash replay, duplicate success-webhook delivery, and POS refund processing preserve that timestamp. The migration adds `paid_at` as nullable without a default or backfill, so historical paid rows retain `NULL`; a legacy-order regression confirms the application does not infer a time and rejects a time-dependent return without creating return or movement records. POS `CARD` uses Stripe test mode and a PaymentIntent API flow separate from ECOM's hosted Checkout. A mocked regression confirms a server-calculated EUR 189.00 order is sent as 18,900 integer cents and `eur`; a route-level test exercises the same card checkout path. Card checkout responds with `PENDING_PAYMENT`/`UNPAID`; the staff order-status surface is read-only (`GET`), and its regression confirms the order remains pending before webhook processing. A client-side success indication cannot commit the sale: only the signed `payment_intent.succeeded` webhook is authoritative. An end-to-end regression posts the same validly signed POS success event twice and confirms `order_status=COMPLETED`, `payment_status=PAID`, the reservation and unit are committed/sold, and only one sale allocation, movement, and webhook-event record exist. The POS client uses Stripe's secure Card Element; card details are not sent to application code. Checkout, status polling, receipt retrieval/acknowledgement, receipt-copy cleanup, and claim/reset services and routes are implemented. A declined-attempt regression confirms the order/payment stay pending and the active reservation retains its full three-minute expiry; expiry cleanup marks the order/reservation expired, restores the unit to stock, and records one release movement. A decline-then-retry regression confirms the same pending order and local `Payment` row reuse the existing PaymentIntent, then complete successfully before the reservation expires. Stripe retains decline-attempt details; Half Moon does not create a separate local `Payment` row for each decline, matching ECOM. A signed `payment_intent.payment_failed` webhook replay returns success without changing the pending order/payment or active reservation deadline, creates one webhook-event record, and does not allocate or sell inventory. A signed `payment_intent.canceled` webhook replay returns success, cancels the order, fails the payment, releases the reservation and unit with one `RELEASE` movement, and creates one webhook-event record without a sale allocation or movement. Invalid-signature, mismatched-order, and mismatched-amount route regressions verify the request is rejected/rolled back, the pending order/payment/reservation remain unchanged, and no sale or webhook-event record is committed. When a signed success arrives after expiry and the reserved unit is unavailable, the backend uses Stripe's idempotent refund path; replay stores one refund/webhook record and leaves the order expired with no sale allocation or movement. POS uses the same ECOM `Idempotency-Key` behavior; replay tests verify one pending order/reservation, one cash-sale payment and sale movement, and reuse of the existing card PaymentIntent. The route rejects reuse of a key with a changed request as `409` without changing the completed order or its inventory/payment/receipt records. Server-side catalog pricing and VAT calculations are also shared. Discounts and vouchers are not available in ECOM or POS v1; order discount snapshot fields remain zero.

POS and ECOM share transaction-safe reservation and sale-commit rules, while fulfillment remains channel-specific. A completed POS sale is recorded with `order_status=COMPLETED`, `payment_status=PAID`, sold units as `SOLD`, and an immutable UTC `paid_at` timestamp after the simulated in-store handover. POS does not use the later-collection pickup workflow. `PICKED_UP` is reserved for an order collected later, such as an ECOM order sent to a store for pickup.

POS prefers `PHYSICAL_STORE` inventory and may fall back to `MAIN_WAREHOUSE`; one source location must fulfill the complete order. A customer link is optional. An anonymous sale may later be claimed using the order number and a one-time code shown on the simulated receipt. A POS-specific claim record tied to the order stores only the code's HMAC-SHA-256 digest using the dedicated claim-code secret, and a successful claim consumes it. The regression independently recomputes the keyed digest and confirms the claim table has no plaintext code column; the temporary delivery copy is separate and decrypts to the receipt code. The customer demo retrieves that copy from the public receipt route and acknowledges it automatically after ten seconds; an unacknowledged receipt remains available for retry until acknowledged or the inclusive 14-day deadline. A deadline regression confirms delivery remains available at the exact deadline, returns `410` immediately after, and cleanup removes the encrypted copy while retaining the permanent claim record. The receipt cleanup worker starts with the application when enabled and runs its first sweep immediately, then daily. If a customer loses the delivered receipt after acknowledgement, no replacement code is issued and the order cannot be claimed; a manager's failed-attempt reset does not replace the code. Five failed claim attempts start a 30-second lock; the next attempt after expiry clears the counter and proceeds, while a manager may reset early. Legacy locked records without a cooldown timestamp remain manager-reset-only. The claim record retains only the latest reset timestamp and manager UUID, not the full reset history. The code proves ownership for a refund claim, remains valid through the inclusive 14-day return deadline, and expires afterward. Claiming requires an active, email-verified customer account. POS returns are allowed for 14 days from `paid_at`, inclusive through the deadline, and the sole target outcome is a simulated refund to the original payment method. The customer-facing POS return route verifies the owner and exact unreturned units, records the customer request, then automatically performs the refund and restock under the `SYSTEM` actor. Returned-unit uniqueness prevents duplicate refunds. Staff retain their separate lookup and return route; customer E-Commerce return-request routes continue to reject POS orders.

The current backend includes POS schemas, models, pending-order and cash/card checkout services, signed PaymentIntent webhook processing, the separate public cash-only demo checkout and receipt routes, staff-protected status/receipt/return routes, receipt acknowledgement and cleanup, customer claims and POS history, manager claim-lock reset, and customer-owned automatic POS returns. ECOM shipping and store-pickup readiness, progression, notification handling, expiry/refund/restock, and pickup-order returns through the shared ECOM return flow are implemented and covered by regression tests.

POS source-selection regression tests verify store preference, whole-order
warehouse fallback, and rejection when no single source can fulfill the
complete order.

### Stripe Webhooks

The webhook handler currently:

- verifies the raw request body and signature
- uses the webhook signing secret
- ignores duplicate event IDs
- does not assume event order
- fulfills only after successful payment confirmation
- returns a fast `2xx` response
- keeps secrets in environment variables

For local development, run `stripe listen --events checkout.session.completed,checkout.session.async_payment_succeeded,refund.updated,payment_intent.succeeded,payment_intent.payment_failed,payment_intent.canceled --forward-to http://127.0.0.1:8000/api/v1/webhooks/stripe` in a separate terminal while the FastAPI server is running. These are the Checkout, refund, and POS PaymentIntent event types handled by the application. Keep the listener running during Checkout. Its signing secret must match the backend's configured webhook secret; never paste or share that secret. Manual test-mode validation on 2026-10-02 verified valid and invalid signatures, successful Checkout fulfillment, duplicate-event idempotency, and late-payment refund reconciliation. Automated tests now cover signature acceptance/rejection, paid fulfillment and replay, late-payment refunds, `refund.updated`, and isolated test-database safety. The focused POS checkout and return-policy run passed 49 tests. The full project suite passed 112 tests after POS expiry-cleanup and redirect-method changes, before the later pickup changes documented below. Controlled Stripe test-mode POS validation succeeded. User-run focused pickup changes passed the paid-checkout webhook cases (2); see the 2026-10-08 pickup update below.

### Transactional Email: Google Gmail API

The outbox helper now accepts an optional `order_id` so order-status notifications can be linked to their order; focused notification tests passed (`3 passed`) on 2026-10-06.

Order-status email formatting supports `ORDER_SHIPPED`, `ORDER_DELIVERED`, `ORDER_READY_FOR_PICKUP`, and `ORDER_PICKED_UP`. The shipped subject is `Good news! Your Half Moon order has been shipped.` and its mock shipment reference is not represented as carrier tracking. The delivered copy uses “piece” for a total quantity of one and “pieces” otherwise. The pickup-ready email asks the customer to collect the order within seven days; the pickup-completed email confirms collection. Pickup-ready enqueueing occurs in the successful pickup-payment transaction, and shipping, delivery, and pickup-completed notifications are enqueued with their corresponding fulfillment state changes. The authenticated order-status endpoint is read-only. The fulfillment poller and Gmail delivery worker are gated by `GMAIL_DELIVERY_WORKER_ENABLED`; tests force the setting off. The order-email formatters and indefinite retry behavior for temporary failures are implemented. Pickup email formatting has focused tests; live Gmail delivery of pickup emails has not been verified.

Controlled local Postman end-to-end test completed on 2026-10-06 for order `HM-2026-000018` using a Stripe test payment. The checkout success redirect initially did not update the application because no Stripe CLI listener was running. After starting the listener, the existing `checkout.session.completed` event was resent to the local webhook; the endpoint returned HTTP 200. The order reached `PAID` and `DELIVERED`; exactly one `ORDER_SHIPPED` and one `ORDER_DELIVERED` notification were recorded, each sent once (`attempt_count=1`), and the user confirmed both emails arrived. No live card or physical shipment was used.

When the workers were first enabled against the development database, they also progressed two older paid ECOM shipping orders, `HM-2026-000007` and `HM-2026-000013`. Each generated exactly one shipped and one delivered email; these were not duplicate retries. The initial preflight checked for queued notifications but did not check for due shipments. Before future controlled worker enablement, check both queued notifications and existing paid `SHIPPING` shipments in `READY_TO_SHIP` or `SHIPPED` state. An empty outbox alone does not mean enabling the fulfillment worker will have no effects.

Local email acceptance update (2026-10-19): the Google OAuth app was confirmed by the user to remain in Testing, and local Gmail OAuth authorization was repeated after a credential/configuration failure. The user confirmed receipt of one retried `ORDER_DELIVERED` email for `HM-2026-000022`; its notification status is `SENT` with `attempt_count=2`. The other three shipped/delivered notifications for orders `HM-2026-000021` and `HM-2026-000022` remain `FAILED` and were not retried. The local delivery/fulfillment worker was enabled temporarily for testing; the user was instructed to restart it with the flag false afterward, but the current running-process state has not been confirmed. The OAuth Testing refresh-token expiry and Google production-readiness requirements remain deployment risks.

Retry policy approved for the planned shipped/delivered messages: retry temporary failures indefinitely using the existing backoff (30 seconds, 1 minute, 2 minutes, 4 minutes, then capped at 5 minutes) until sent; permanent Gmail authorization/configuration failures remain `FAILED` and require attention. The processor now retries non-expiring notifications with the same backoff while still honoring verification-token expiry; the focused no-expiry retry test passed (`1 passed`) on 2026-10-06.

Half Moon will use the Gmail API with OAuth 2.0 to send transactional messages from a dedicated Gmail account. Request only the sensitive `https://www.googleapis.com/auth/gmail.send` scope, which permits sending mail on behalf of that account; do not request mailbox-read or broader Gmail scopes. The sender account owner authorizes the application; Half Moon customers do not authorize Google or need Google accounts. Gmail API messages must be RFC 2822 MIME messages encoded as base64URL in the `raw` field and sent through `users.messages.send`.

This is a free, low-volume project and will not buy a custom sending domain. The intended sender setup is an external OAuth app with only the dedicated sender account authorizing it. Google documents a 100-test-user limit for external apps in Testing and a 100-total-user cap for published, unverified apps requesting sensitive or restricted scopes; an unverified-app warning is shown. This is not Google verification or a guaranteed way to avoid future requirements. Gmail account sending limits and Gmail API quotas apply, so this setup is for project/demo use, not bulk email. Google's current quota guidance describes a daily threshold below which usage does not incur additional API charges and says billing details may change; recheck the current terms before deployment.

For this Gmail scope, an external OAuth app left in Testing has refresh tokens that expire after seven days. Publishing the app removes that specific Testing-mode expiry, but does not make tokens permanent. Google can invalidate or revoke tokens, so delivery must handle authorization failure and require the sender owner to authorize again. Before publishing or authorizing, check the current Google Auth Platform requirements, including any required app disclosures, privacy-policy URL, domain verification, or scope verification. If the console requires a domain, verification, or other requirement the project cannot meet, pause and discuss compliant options; do not misrepresent the app or bypass a required review. Not buying a custom sending domain does not waive OAuth or Google user-data policy requirements.

Keep OAuth client credentials and refresh tokens in server-side secret storage. Never expose them to React, commit them to source control, or write them to logs. `settings.gmail_credentials_file` maps local storage to the Git-ignored `.secrets/gmail-oauth-token.json` path; the OAuth callback writes only Fernet ciphertext using the application's `encrypt_pii` wrapper and the configured PII encryption key, retaining restrictive file permissions and atomic replacement. The loader uses the corresponding `decrypt_pii` wrapper for local-file reads. If it finds legacy plaintext JSON, it encrypts and atomically replaces that file on first load, then continues using the credentials without requiring reauthorization. File decryption failures (`InvalidToken` or `ValueError`) and invalid credential data are converted to `GmailCredentialsError`; the notification processor handles that error as a permanent credential/configuration failure rather than allowing it to escape the background worker. Deployed configuration continues to supply authorized-user credential JSON through `settings.gmail_credentials_json` as a hosting-platform secret; this environment value remains raw plaintext JSON and is parsed directly rather than treated as an encrypted local file. The local OAuth setup switch defaults to disabled, must be enabled only temporarily during local authorization, and must be disabled again after credentials are saved. Both setup routes require a loopback request and the enabled flag, verify OAuth state, and never return token contents. Disable HTTP access logging when running the local authorization callback so the one-time authorization code is not recorded. OAuthlib rejects HTTP OAuth requests by default; `OAUTHLIB_INSECURE_TRANSPORT=1` may be set only inline for the local Uvicorn setup process using the HTTP localhost callback, and must never be added to `.env` or set in production. See [OAuthlib security guidance](https://oauthlib.readthedocs.io/en/latest/oauth2/security.html). The intended use is limited to sending Half Moon transactional messages; follow Google's user-data policy and disclose that use accurately.

Official Google documentation was reviewed on 2026-10-05 for this integration: [Gmail API sending](https://developers.google.com/workspace/gmail/api/guides/sending), [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes), [OAuth consent-screen configuration](https://developers.google.com/workspace/guides/configure-oauth-consent), [OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server), [publishing statuses and verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview), [token expiration](https://developers.google.com/identity/protocols/oauth2#expiration), [OAuth security best practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices), [Gmail API quotas](https://developers.google.com/workspace/gmail/api/reference/quota), and the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy). Recheck these sources when setting up OAuth and before deployment because Google requirements, quotas, and billing terms can change.

The encrypted notification model, enqueue helper, and Alembic migration are implemented and applied to the isolated test and development databases. Registration now only enqueues the verification email; the earlier console preview has been removed. On 2026-10-05, a read-only Alembic check against the configured development database returned `3e64f262f21b`; the user then ran `.venv/bin/alembic upgrade b841c6ad9f20`, and Alembic reported the upgrade to `b841c6ad9f20`. The user created the Google Cloud project and Gmail API OAuth configuration with the sole sensitive scope `gmail.send`, an external audience in Testing, the dedicated sender Gmail as a test user, and a Web application client with callback URI `http://localhost:8000/api/v1/auth/gmail/callback`. The local start/callback routes and secure credential writer are implemented, and the dedicated sender completed OAuth authorization on 2026-10-05. The setup flag is disabled and the generated credentials remain local and private. Gmail credential loading, API sending, outbox processing, retry handling, and the default-off FastAPI poller are implemented. The first controlled live Gmail verification email was received on 2026-10-05, and the user confirmed the verification link through Postman with a successful response after correcting the API client to use `AuthorizedHttp`; the focused Gmail client tests passed (`3 passed`). On 2026-10-06, the user finalized the verification subject as `Please verify your Half Moon account` and approved body copy that thanks the customer, asks them to click the verification link, and says to ignore the email if they did not create the account. The worker remains disabled and Uvicorn stopped. The user does not want a second registration confirmation email; the verification result should be shown on the page opened by the email link. The current endpoint returns JSON; implement the designed customer-facing success page later during frontend work. The user wants to design the order-shipped and order-delivered emails next. Revisit the project roadmap together afterward; do not treat the documented POS flow/process-and-test checklist as the next project step until the user has reviewed the roadmap. While the OAuth app remains in Testing, refresh tokens for this scope expire after seven days; review publishing and token-lifecycle requirements before unattended deployment.

External integrations are not the system of record. Stripe webhook events are signature-verified, recorded by event ID, and processed idempotently without assuming event order. Email notifications are committed to the outbox independently of external delivery; worker failures do not roll back successful account, order, payment, inventory, or shipment transactions, and retries are tracked on notification rows. Mock fulfillment runs after confirmed payment and remains independent from email delivery.

Gift recipients attach to order lines and can link to verified customer records. Webhook events are stored by unique provider event ID; notification rows are enqueued for Gmail API delivery by the opt-in worker. Audit events are append-only, and seeded business settings hold the VAT rate, reservation duration, return windows, currency, and `Europe/Rome` timezone. Discount-threshold settings are deferred with discount functionality.

## GDPR Scope

The project demonstrates practical GDPR-aware behavior:

- data minimization
- email verification
- protected customer access
- privacy notice
- no card storage
- no sensitive logs
- anonymization for deletion requests
- controlled staff access
- transactional-only email
- no unnecessary marketing tracking

This is an educational implementation, not legal certification.

## Out of Scope

- Real physical card readers
- Real shipping carriers
- Real warehouse operations
- Product-management UI
- Stock-receiving UI
- Supplier integrations
- Italian government fiscal integrations
- Electronic invoicing certification
- Mandatory MFA
- Multiple physical shops
- Microservices
- Paid infrastructure
- Production tax compliance

Reporting functionality is out of scope. Transactional and audit records remain available to the workflows that create and protect them, but the application does not provide reporting views, reports, or reporting APIs.

## Planned Project Structure

```text
half-moon/
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── features/
│   │   │   ├── storefront/
│   │   │   ├── pos/
│   │   │   ├── auth/
│   │   │   ├── cart/
│   │   │   ├── checkout/
│   │   │   ├── account/
│   │   │   └── operations/
│   │   ├── services/
│   │   └── styles/
│   └── package.json
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/v1/
│   │   ├── core/
│   │   │   ├── authentication.py
│   │   │   ├── config.py
│   │   │   ├── data_protection.py
│   │   │   └── security.py
│   │   ├── db/
│   │   ├── models/
│   │   ├── schemas/
│   │   │   └── auth.py
│   │   ├── services/
│   │   │   └── auth.py
│   │   └── integrations/
│   │       ├── stripe/
│   │       └── gmail/
├── pyproject.toml
├── migrations/
├── seed/
│   ├── catalogue_preview.csv
│   ├── seed_catalogue.py
│   └── seed_staff.py
├── tests/
├── docs/
├── .env.example
├── .gitignore
└── README.md
```

This is a planned target structure, not a description of the current route layout. The backend currently defines business routes in `backend/app/main.py`; `backend/app/api/v1/` does not exist. The implemented POS checkout route follows the current `main.py` layout. A future move to an `api/v1/` package would require an explicit architecture decision.

## Implementation Sequence

1. Verify the local Python environment and PostgreSQL connection.
2. Create the project folders and dependency configuration.
3. Configure environment variables and database settings.
4. Initialize Alembic.
5. Define SQLAlchemy model metadata for the approved schema.
6. Generate and review the initial Alembic migration.
7. Run the migration against `half_moon_db`.
8. Seed products, variants, locations, inventory, customers, and demo staff.
9. Create a FastAPI health-check endpoint.
10. Build catalog and inventory APIs.
11. Authentication and authorization are implemented, including customer/staff models, password hashing, JWTs, encrypted PII, customer registration/login, staff login, bearer dependencies, deterministic staff seed, email verification, and protected staff inventory routes. Customer login rejects unverified email accounts before issuing a JWT. Customer and staff refresh-token cookie sessions are implemented with opaque database-backed tokens, strict device-scoped replay revocation, CSRF protection, fixed 30-day customer and 12-hour staff lifetimes, current-device logout, and expired-session cleanup. The migration is applied to both local databases at verified Alembic head `a41c7e92b6d3`; focused refresh-session tests passed (`6 passed`) and the full project suite passed (`144 passed`) on 2026-10-09. Customer password-reset request and confirmation routes use generic responses, a demo 30-second cooldown, single-use 30-minute tokens, and active-session revocation; the notification email now links to the frontend using `FRONTEND_PUBLIC_URL` while leaving token consumption to the existing confirmation request. The focused password-reset and related auth run passed (`14 passed`); the full project suite passed 148 tests at that implementation milestone on 2026-10-09.
12. Authenticated and guest ECOM orders, serialized-unit reservations, Stripe-hosted Checkout, signed/idempotent payment webhooks, and late-payment inventory reconciliation are implemented and tested. Authenticated `GET /api/v1/orders` returns only the current customer's ECOM order summaries, newest first; `GET /api/v1/orders/{order_id}` remains customer-scoped for order details.
13. Automated mock ECOM shipping, order-status notifications, ECOM return requests, and simulated return completion are implemented. Store credit and the unused exchange outcome have been retired from active code and configured schemas. POS cash/card checkout, sale completion, signed PaymentIntent webhook processing, status and receipt routes, receipt acknowledgement/cleanup, customer claims, manager claim-lock reset, and POS returns are implemented. ECOM store-pickup readiness, progression, deadline/expiry handling, notifications, simulated refund, and restock are implemented with focused regression coverage. Pickup returns were reauthorized on 2026-10-09 and added to the shared return workflow using the approved inclusive 14-day window anchored at `picked_up_at`; the full project suite subsequently passed 138 tests on 2026-10-09. Refunds remain simulated, not real payment-provider refunds.
14. Guest checkout and its customer/order APIs are implemented and verified.
15. Gmail API verification and shipped/delivered email delivery are implemented. The verification copy is finalized. The 2026-10-19 local OAuth refresh and one successful retried `ORDER_DELIVERED` email are recorded above. Three older `ORDER_SHIPPED`/`ORDER_DELIVERED` notifications remain `FAILED`; their recorded error is the worker's generic `Gmail authorization or configuration failed` classification, which is non-retryable and does not identify whether the cause was authorization or configuration. The Google OAuth app is still in Testing. No separate registration-confirmation email is intended; `/verify-email` presents verification success or failure and a sign-in link.
16. Controlled Stripe test-mode validation of a POS card sale is complete. ECOM store-pickup readiness, progression, deadline/expiry handling, notifications, simulated refund, and restock are implemented; pickup returns use the shared return workflow. Store-credit and exchange retirement is complete: outstanding configured-database credits were converted to simulated original-payment refunds, and the ledger and retired constraints were removed. POS checkout, status/receipt/claim behavior, manager claim-lock reset, and returns are implemented; see `docs/pos-process-and-test-checklist.md` for verified coverage and remaining acceptance criteria. The focused POS checkout and return-policy run passed 49 tests. The full project suite passed 138 tests on 2026-10-09 after reporting removal. Following refresh-session implementation, both local databases are at verified Alembic head `a41c7e92b6d3`; focused refresh-session service and route tests passed (`6 passed`), and the full suite passed (`144 passed`) on 2026-10-09. After password-reset implementation, the focused password-reset and related auth run passed (`14 passed`); the full suite passed 148 tests at that implementation milestone on 2026-10-09.
17. Reporting was removed from project scope on 2026-10-09. The approved deployment path uses separate Render frontend and FastAPI backend services under one unified domain; Render rewrites `/api/*` to FastAPI. Production therefore needs no FastAPI CORS middleware or origin/port allowlist. During local development, use the frontend dev-server proxy for `/api/*` requests. The backend's `FRONTEND_PUBLIC_URL` setting defaults to `http://localhost:5173` and must point to the public storefront URL in the Render backend service's Environment settings. Verification emails link to the frontend `/verify-email?token=...` route; password-reset emails link to `/reset-password?token=...`.
18. The React storefront, product-detail page, in-memory shopping bag, guest and authenticated checkout, checkout success/cancel pages, and customer registration, verification-result, and password-reset screens are implemented. The customer order center uses `/account` for E-Commerce order history and `/account/orders/:orderId` for order details, paid-order shipment progress, and inline return submission/status recovery. The customer POS claim page is available at `/account/claim-pos-order`; the later 2026-10-23 browser acceptance verified a successful customer claim, separate POS purchase history/detail, and eligible simulated return, superseding the earlier claim-form verification note below. Checkout uses backend-authoritative prices and inventory. Return refunds and restocking are simulated; no real payment-provider refund is sent.
19. Customer and staff sign-in, in-memory auth state, and staff route protection are implemented. The staff-protected `/pos` cash/card terminal and `/pos/orders` lookup, returns, and manager claim-reset page are implemented. The user's approved customer-only tester scope is recorded above; those existing staff pages are not the final customer-facing flow.
20. Complete remaining functional implementation and browser acceptance first. Then perform the final visual/mobile CSS review with the user, validate deployment readiness, and deploy to Render last.

## Final Conventions

- Internal IDs: UUIDs
- Human-facing order references: readable order codes
- Money: PostgreSQL exact numeric values and Python `Decimal`
- Timestamps: UTC in storage
- Display timezone: `Europe/Rome`
- Approved API prefix: `/api/v1` for business routes; `/health` remains unprefixed for health checks. Source routes, auth metadata, and local verification links are migrated. Read-only staff inventory summary and movement-history APIs were retired on 2026-10-09; the append-only movement records remain stored for transactional traceability. The prefixed manager transfer route was schema-checked with an invalid zero quantity and returned `422` before executing a transfer.
- Secrets: environment variables
- Database: `half_moon_db`

The schema blueprint below records the table and relationship design approved so far. No schema SQL should be written until the developer has reviewed and approved the complete document.

## Collaboration and Learning Rule

The developer writes the project code. The assistant provides architectural guidance, explains decisions step by step, proposes small exercises or edits only after approval, reviews implementation, and helps validate and debug the developer's work. Automated agents must not build the application without the developer's participation. Every major implementation step should be explained before the developer performs it.

The developer runs compilation, import, migration, and other validation commands personally after the assistant explains them. Postman is the standard tool for HTTP/API testing. Secrets in `.env` are checked only through safe presence and validity checks; their values must never be copied into source control, documentation, or chat.

Inventory movements are append-only history records. `inventory_units` stores the current snapshot, while `inventory_movements` records each receipt, transfer, reservation, release, sale, return, repair, damage, or loss. A movement stores the previous and new status, previous and new location, related order or return when applicable, actor type (`SYSTEM` or `STAFF`), optional staff actor, note, and timestamp. The inventory-unit update and its movement record must occur in the same database transaction.

## Data Modeling

The transactional tables will be normalized. Orders, customers, order lines, order-line unit allocations, payments, returns, inventory movements, fulfillment records, and audit events remain separate concepts connected by foreign keys. A separate discounts table and approval workflow are deferred; order discount snapshot fields remain zero in v1.

An order can have zero, one, or multiple related returns. The application discovers return status through those relationships rather than maintaining a manually duplicated `has_return` flag on the order.

The application should also:

- index foreign-key columns
- avoid N+1 queries
- store order-time snapshots of prices, VAT, product names, and addresses
- record external webhook IDs for idempotent event handling
- preserve completed financial and inventory history
- use audit events for sensitive automated or staff actions

This keeps the write model consistent while preserving transactional history.

Order lines store a variant quantity and financial snapshots. The separate `order_line_units` relationship maps each purchased quantity to the exact serialized inventory units. For example, buying two units of one EAN creates one order line with quantity 2 and two order-line-unit records. This keeps receipts concise while preserving unit-level returns and inventory traceability.

Reservations reference exact serialized inventory units and expire after three minutes. A successful payment commits the reservations and creates the completed order-line-unit allocations. Version one allows one successful payment tender per order; multiple failed attempts may be retained alongside it. Split tender is out of scope for this portfolio project. Card numbers and security codes are never stored. Stripe payment identifiers are unique.

Foundational database constraints include unique product slugs, SKUs, EANs, and serial numbers; required product, variant, and inventory relationships; non-negative exact prices; valid location types and inventory statuses; and location requirements for physical inventory states. Historical records use restricted deletion. Referenced customers are anonymized rather than deleted, and inventory-unit updates occur in the same transaction as their append-only movement records. Inventory availability is supported by an index on variant, status, and location.

## Schema Blueprint

This is the approved first-version table blueprint. Field names may be refined during implementation, but no relationship or business rule should change without updating this document.

### Catalog and Inventory

```text
products
  id, name, slug, description, category, image_url, created_at, updated_at

product_variants
  id, product_id, material, color, size_system, size_label,
  sku, ean, retail_price, image_url,
  created_at, updated_at

locations
  id, code, name, location_type, created_at

inventory_units
  id, variant_id, serial_number, location_id, status,
  created_at, updated_at

inventory_movements
  id, inventory_unit_id, movement_type, from_location_id,
  to_location_id, from_status, to_status, order_id, return_id,
  actor_type, staff_user_id, note, created_at
```

The initial `inventory_movements` migration keeps nullable `order_id` and `return_id` UUID fields without foreign-key constraints because the `orders` and `returns` tables did not exist yet. Those tables now exist, and the read-only orphan audit found zero mismatched references. A reviewed follow-up migration added named nullable foreign keys to both tables with `ON DELETE RESTRICT`, preserving existing movement history.

Products have no status column initially. A product is purchasable when a variant has available serialized units. EAN and SKU are globally unique. Ring sizes use Italian `size_label` values 10, 12, 14, 16, 18, and 20, with `size_system` unset; other product sizes may use labels such as `Standard`, `15 cm`, or `Adjustable`. Ring SKUs use the compact format `MRG-G-20` or `MRG-S-20`, with the material code and Italian size but no size-system marker. Customer-facing order pages translate legacy EU order snapshots to Italian size and SKU displays without rewriting historical snapshots. The development-only conversion of existing ring variants and serialized units is an explicit previewable, transactional operation; it is not an automatic production data migration.

### Customers and Authentication

```text
customers
  id, email_ciphertext, email_lookup_hmac, first_name_ciphertext,
  last_name_ciphertext, phone_ciphertext, created_at, updated_at,
  anonymized_at

customer_accounts
  id, customer_id, password_hash, email_verified_at, account_status,
  password_changed_at, last_login_at, created_at, updated_at

customer_addresses
  id, customer_id, label, recipient_name, address_line_1,
  address_line_2, city, region, postal_code, country_code, phone,
  is_default_shipping, is_default_billing, created_at, updated_at

staff_users
  id, email_ciphertext, email_lookup_hmac, first_name_ciphertext,
  last_name_ciphertext, password_hash, role, account_status,
  password_changed_at, last_login_at, created_at, updated_at

auth_sessions
  id, customer_account_id, staff_user_id, refresh_token_hash,
  client_type, expires_at, last_used_at, revoked_at, created_at

verification_tokens
  id, customer_account_id, token_hash, purpose, expires_at,
  used_at, created_at
```

Customer accounts are optional so guest customers can exist. Sessions belong to exactly one customer account or staff user. The only staff client is `POS_TILL`. Staff roles are `SALES_ASSOCIATE` and `STORE_MANAGER`.

### Orders, Checkout, and Payments

```text
orders
  id, order_number, customer_id, channel, fulfillment_method, order_status,
  payment_status, paid_at, currency, subtotal_gross,
  discount_total_gross, taxable_gross, net_total, vat_total,
  total_gross, standard_vat_rate, applied_vat_rate, tax_free,
  tax_free_reason, created_at, updated_at

order_lines
  id, order_id, variant_id, quantity, product_name_snapshot,
  variant_snapshot, sku_snapshot, ean_snapshot, retail_price_gross,
  discount_gross, taxable_gross, net_amount, vat_rate, vat_amount,
  total_gross, created_at

order_line_units
  id, order_line_id, inventory_unit_id, created_at

order_addresses
  id, order_id, address_type, recipient_name, address_line_1,
  address_line_2, city, region, postal_code, country_code, phone,
  created_at

reservations
  id, order_id, order_line_id, inventory_unit_id, status,
  expires_at, created_at, released_at

payments
  id, order_id, payment_method, provider, provider_payment_id,
  amount, currency, status, failure_reason, created_at, updated_at
```

Orders use `ECOM` or `POS`. Anonymous POS orders may have no customer. One order line can have quantity greater than one; `order_line_units` maps that quantity to exact serialized units. Version one permits one successful payment tender per order; split tender is out of scope. Stripe retains card-attempt details; Half Moon does not create a separate application payment record for every declined attempt.

The shared order model/schema includes nullable, immutable UTC `paid_at`, set on the first transition to paid for ECOM and POS and left null while unpaid. POS return eligibility uses this timestamp for a 14-day window inclusive through the deadline. Historical paid orders whose exact payment-completion time cannot be recovered remain null; do not backfill an approximation. The `ck_orders_order_status` constraint permits the unified payment, fulfillment, and completion states. `PICKED_UP` remains for later collection, not an ordinary POS sale.

Migration `c7f48d21a063` adds nullable `orders.fulfillment_method` and backfills
existing ECOM orders to `SHIPPING`. It adds nullable, timezone-aware
`shipments.ready_for_pickup_at` and `shipments.pickup_deadline_at`;
`shipments.picked_up_at` predates this migration. It extends
`ck_orders_fulfillment_status` and `ck_shipments_status` to permit
`READY_FOR_PICKUP`, adds `ck_orders_fulfillment_method` to require ECOM orders
to use `SHIPPING` or `STORE_PICKUP` while POS orders leave the field null, and
extends `ck_inventory_movements_movement_type` to permit `PICKUP_EXPIRY`.

Migration `c2f40e7a91bd` preflights legacy order/payment/fulfillment
combinations, backfills the existing fulfillment state into
`orders.order_status`, expands `ck_orders_order_status`, and drops
`orders.fulfillment_status`. Payment state remains in `payment_status`, while
`shipments.status` remains a separately constrained operational state. The
migration is a new forward revision; its downgrade reconstructs the legacy
fulfillment value from the unified order state and shipment record.
It was applied to the isolated test database first, then to the primary
database on 2026-10-23 after migration and test validation; both databases are
at this revision. Do not manually alter either database; future schema changes
must use reviewed Alembic migrations.

Active serialized-unit reservations are protected by the PostgreSQL partial
unique index `uq_reservations_active_inventory_unit` on
`reservations.inventory_unit_id` where `status = 'ACTIVE'`. Migration
`8d72372141d5` creates the index and was the direct parent of the POS/audit
checkpoint `c7f48d21a063`. Both local databases have since advanced to `d8c72a4f91be`; the index
remains in the schema at this revision.

`order_status` and `payment_status` describe separate dimensions. An order remains `PAID` after a return because the sale occurred. For original-payment refunds, the order-level `payment_status` is `PARTIALLY_REFUNDED` while some of the payment remains and `REFUNDED` after the full payment is returned. All return outcomes use this original-payment refund behavior.

`order_line_units.inventory_unit_id` is indexed but not unique. A serialized unit may be sold, returned, restocked, and sold again during its lifetime; each sale receives a new order-line-unit allocation. `return_items.order_line_unit_id` remains unique because one specific sale allocation can only be returned once.

### Tax, Fulfillment, and Returns

Discount and voucher functionality, including a separate discounts table and
approval workflow, is deferred for ECOM and POS. Do not add discount inputs to
POS checkout; existing order and line discount snapshot fields remain zero.

```text
tax_exemptions
  id, order_id, original_vat_rate, applied_vat_rate, reason,
  customer_residency, documentation_reference, actor_type,
  staff_user_id, created_at

shipments
  id, order_id, source_location_id, fulfillment_method, status,
  mock_tracking_number, shipping_address_snapshot, shipped_at,
  delivered_at, ready_for_pickup_at, pickup_deadline_at, picked_up_at,
  created_at, updated_at

returns
  id, order_id, customer_id, status, requested_outcome,
  completed_outcome, reason, requested_at, received_at,
  inspected_at, completed_at

return_items
  id, return_id, order_line_unit_id, inspection_result, created_at

refunds
  id, return_id, payment_id, amount, method, provider_ref, status,
  created_at, completed_at

gift_recipients
  id, order_line_id, recipient_customer_id,
  recipient_email_ciphertext, recipient_email_lookup_hmac,
  recipient_name_ciphertext, gift_message, verification_status,
  created_at

```

The target return outcome constraint permits only `REFUND`; `STORE_CREDIT` and the unused `EXCHANGE` outcome are removed from active models, APIs, and the live database schema. The request keeps `requested_outcome` for compatibility/audit purposes but restricts it to `REFUND`; requests for retired outcomes are rejected. Payment methods are limited to `CARD` and `CASH`, and refund methods to `ORIGINAL_PAYMENT`.

The active models and API permit only the refund-only policy. Both configured
database schemas have been migrated by `a6f90c12d435`: safely reconciled credit
balances were converted into simulated original-payment refunds, return
outcomes were normalized to `REFUND`, obsolete constraints were narrowed, and
the ledger was dropped. The migration aborts rather than discard an
unreconciled balance. Historical Alembic revisions remain unchanged.

Tax-free sales and returns require a registered customer account with verified email. Fulfillment uses one source location per order in the first version. Returns reference exact `order_line_units` and may restore, repair, or damage the serialized unit after inspection.

### Integrations, Audit, and Configuration

```text
stripe_webhook_events
  id, stripe_event_id, event_type, processed_at

notifications
  id, customer_id, order_id, notification_type,
  recipient_email_ciphertext, payload_ciphertext,
  status, provider_message_id, attempt_count,
  next_attempt_at, expires_at, locked_at, last_error,
  created_at, sent_at

```

`stripe_webhook_events.stripe_event_id` is unique for Stripe event idempotency. Notification recipients and payloads are encrypted at rest; registration enqueues its verification email in the same database transaction as the account and token. The notification worker consumes the outbox using its attempt, scheduling, and lock fields; `expires_at` prevents delivery of an expired verification link. Invalid notification record data and in-flight notifications with unexpected processing exceptions are marked `FAILED` before the latter exceptions propagate to the worker supervisor; recognized transient Gmail and network failures remain retryable. Provider sending and retry processing must never roll back account/order transactions.

The notification and fulfillment workers back off exponentially after database errors and run under supervisors that log unexpected task termination and restart with capped exponential delay. Record-specific notification failures are marked `FAILED` before unexpected processing errors propagate. Fulfillment processing rolls back and marks the still-current shipment `FAILED` after an unexpected non-database record-level exception; it re-locks the selected shipment and checks its state before quarantining it. `SQLAlchemyError` is rolled back and re-raised so transient database failures remain retryable. The regression `test_invalid_customer_ciphertext_quarantines_due_shipment` verifies invalid Fernet ciphertext is quarantined without changing the order state.

An append-only application-wide `audit_events` table records only high-risk
actions across customer, staff, system, and anonymous actors. Its fields are
`id`, `created_at` (UTC), `actor_type`, nullable `customer_id` and
`staff_user_id` foreign keys, `action_code`, `outcome`, nullable `target_type`
and `target_id`, nullable sanitized `error_code` and `correlation_id`, and
nullable allow-listed JSON `details`. `actor_type` is a constrained string
(`CUSTOMER`, `STAFF`, `SYSTEM`, or `ANONYMOUS`); `outcome` is a constrained
string (`SUCCEEDED` or `FAILED`). Target references are polymorphic and use
`target_type` plus `target_id`.

Version one records staff inventory transfers and manager POS claim resets;
customer POS claims and return requests/processing; and exceptional system
pickup-expiry and late-payment refund/restock actions. It also records POS
claim lockouts and terminal refund/restock integrity failures, but not
individual wrong codes, ordinary rejected requests, routine card declines,
generic checkout validation errors, rejected email-verification attempts, or
transient Gmail retries. Successful event writes share the business
transaction. A selected high-risk failure is recorded only after the failed
business transaction is rolled back, in a separate audit transaction. Failed
integrity events are deduplicated to one event per action and target, including
across retries. Details must not contain raw PII, tokens, credentials, or
provider payloads. No audit-management API is part of this scope.

The typed audit writer and v1 integrations are implemented for staff inventory
transfers and POS claim resets; customer POS claims and return requests and
processing; successful pickup expiry and late-payment refunds; POS claim
lockouts; and terminal return, pickup-expiry, and late-refund integrity errors.
Pickup-expiry integrity failures roll back business work before the shipment is
marked `FAILED` and audited in a separate transaction. The audit migration is
`e5a41b0c9d72`, followed by the failed-shipment status migration
`f31bd892a6e4`. At the audit-event checkpoint, the isolated `half_moon_test_db`
was upgraded through that revision and the full suite passed (`139 passed`).
Both local databases have since advanced to `d8c72a4f91be`; the user
upgraded the primary database after the test database migration.

Business configuration is currently supplied through application settings,
including the `standard_vat_rate` default; there is no `business_settings`
table or database-seeded settings record. No settings-management UI is
required.

Discount-threshold settings are deferred with discount functionality and are
not part of ECOM or POS v1.

## Architecture Document Maintenance

This file is the canonical source of truth for the Half Moon project.

Any approved change to the project must be reflected here before implementation continues. This includes changes to:

- business rules
- processes and workflows
- database decisions
- technologies and external services
- security and GDPR behavior
- project scope
- deployment assumptions

When a decision changes, update the relevant section and keep the implementation aligned with the updated decision. Do not silently introduce architectural changes in code. At the end of each major phase, review this document before starting the next phase.

## Implementation Timeline

The dated checkpoints below preserve the state and decisions at the time they
were written; they are historical records, not the current implementation
summary. When a checkpoint conflicts with the current sections above or the
latest implementation update below, use the current sections.

## Resume Checkpoint: 2026-10-01

Completed and locally verified:

- PostgreSQL schema and seed catalogue/inventory remain applied.
- Authentication tables exist through migrations `45a05eee96b2` and `068b92a6ccd3`.
- Customer registration stores encrypted personal data, an email HMAC lookup value, an Argon2id password hash, and a verification token in one transaction.
- `email-validator>=2.3,<3` is declared directly in `pyproject.toml` because customer and staff authentication request schemas use Pydantic `EmailStr`; `requirements-prod.lock` pins compatible `email-validator==2.3.0`.
- `pyproject.toml` now declares `[tool.setuptools.packages.find] include = ["backend*"]` because the repository root has multiple top-level directories (`backend`, `seed`, `migrations`); only `backend` is the installable application package. Verified: `./.venv/bin/python -m pip install --dry-run -e .` now builds editable metadata and resolves all dependencies without error.
- At this checkpoint, customer login returned a short-lived JWT without
  requiring verified email; the current login gate is recorded in the latest
  implementation update below.
- Staff seed creates `store_manager@halfmoon.com` and `sales_associate@halfmoon.com` from ignored environment passwords.
- Staff login returns a JWT with the staff role.
- Staff bearer dependencies reject inactive accounts and mismatched roles. Customer bearer dependencies now also require an existing `ACTIVE` customer account; email verification remains endpoint-specific.
- Customer-token account-status fix passed `py_compile`. Postman confirmed an `ACTIVE` customer's existing token receives `200`; the same token receives `401` when the account is `SUSPENDED` or `DISABLED`, then returned to `200` after restoring the account to `ACTIVE`.
- Manager authorization returns `200` for `STORE_MANAGER` and `403` for `SALES_ASSOCIATE`.
- At an earlier milestone, the local email simulation printed a verification URL and Postman verified the email flow and single-use token behavior. That simulation has since been removed; live Gmail delivery remains unverified.
- Staff inventory summary and manager-only generic transfers were tested through Postman in both directions at an earlier milestone. The transfer design remains `POST /api/v1/staff/inventory/transfer`, supporting quantity `1` and larger quantities in either direction. The summary and movement-history APIs were retired on 2026-10-09; persisted inventory movements remain intact.
- Order, order-line, exact order-line-unit allocation, and reservation models are implemented and applied through migration `ffff38014400`.
- The misleading temporary `/api/v1/products/{product_id}/variants/{variant_id}/reserve` endpoint was removed. Catalog stock routes report availability only; the persisted three-minute reservation is created with authenticated `POST /api/v1/orders`. `main.py` passed `py_compile`, and OpenAPI confirms `/reserve` is absent while `/api/v1/auth/register` remains present.
- Customer and staff JWT dependencies now use the shared `HTTPBearer` OpenAPI scheme `BearerAuth`; the JSON login endpoints are not advertised as OAuth2 password flows. Customer active-account checks and staff role/account checks remain separate. `authentication.py` passed `py_compile`, and OpenAPI confirmed `BearerAuth` is `type: http`, `scheme: bearer`, with no `tokenUrl`. Postman confirmed active customer `GET /api/v1/auth/me` and manager `GET /api/v1/staff/me` return `200`.
- Order constraints protect channel, lifecycle statuses, VAT rates, and non-negative financial amounts.
- The inventory-movement reference audit found zero non-null `order_id` or `return_id` values without matching rows. Migration `818ca3c44540` adds named nullable foreign keys to `orders` and `returns` with `ON DELETE RESTRICT`; it was reviewed, compiled, and applied successfully.
- Migration `f7582083fb05` changed the historical order-line-unit inventory index from unique to non-unique, allowing returned and restocked serialized units to be sold again.
- The store-credit ledger migration `0249b9c41a0c` and resale correction migration `f7582083fb05` were applied through `alembic upgrade head`.
- The global PostgreSQL order-number sequence `half_moon_order_number_seq` is applied through migration `39265b1a70b8`; the customer-facing format will be `HM-{YEAR}-{GLOBAL_SEQUENCE:06d}`.
- Authenticated ECOM pending-order creation and lazy reservation expiration were tested through Postman. Expired reservations return inventory to `IN_STOCK`, mark the order `EXPIRED`, and create a system `RELEASE` movement.
- `order_line_units` is intentionally created only after successful payment; pending reservations do not represent completed purchases.
- Approved refund-status behavior: `order_status` remains `PAID` after a return; original-payment refunds set order `payment_status` to `PARTIALLY_REFUNDED` or `REFUNDED`, while store-credit returns leave it `PAID`. The model and service updates pass `py_compile`. Manual migration `3af7864d66b0` updates the order payment-status constraint and guards downgrade while partial-refund orders exist; it compiled and was applied successfully. Payment for Postman test order `HM-2026-000009` succeeded (EUR 378.00), and fulfillment reached `DELIVERED`. Its two EUR 189 allocations were refunded separately: after the first refund, an order query confirmed `PAID` / `PARTIALLY_REFUNDED`; after the second, it confirmed `PAID` / `REFUNDED`.
- Successful payment now marks each reservation `COMMITTED` in the same transaction as the sale. Migration `6c42aeb1f903` backfills only `ACTIVE` reservations for `PAID` orders; its downgrade intentionally preserves this historical data correction. The migration applied successfully, and a read-only audit confirmed all three earlier paid-order reservations are `COMMITTED` while all expired-order reservations remain `EXPIRED`. Runtime Postman payment for order `HM-2026-000007` succeeded, and a read-only check confirmed `PAID` / `COMMITTED` / `SOLD` for the order, reservation, and unit.
- Simulated payment failure and success were tested through Postman. Successful payment created a `PAID` order, `PAID` payment, exact order-line-unit allocation, `SALE` movement, `SHIPPING` shipment, and `READY_TO_SHIP` fulfillment state. The pending-order guard now precedes both outcomes; `py_compile` passed, a failed-payment request against paid order `HM-2026-000007` returned `409` with no additional payment row, and a failed attempt on pending order `HM-2026-000008` returned `FAILED` while the order remained `PENDING_PAYMENT`. A read-only check confirmed its reservation remained `ACTIVE` and inventory remained `RESERVED`.
- Time-based mock fulfillment was tested through Postman: the shipment advanced to `SHIPPED` and then `DELIVERED` using the configured 30-second delays.
- ECOM refund-to-original-payment was tested through Postman; the return completed, payment became `REFUNDED`, and inventory returned to `IN_STOCK` with a `RETURN` movement.
- ECOM store-credit issuance was tested through Postman on order `HM-2026-000006`: the delivered order's return completed for EUR 189.00, payment remained `PAID`, and repeating the process request was rejected as already processed. A subsequent within-window request against the already-returned allocation was rejected with `409` and created no return.
- An earlier filtered staff movement-history request returned two distinct `RETURN` movements for serialized unit `HMN-G-STD-WH-000002`, corresponding to its separate refund-return and later store-credit-return lifecycles after resale. The read API was retired on 2026-10-09; this historical note does not indicate a currently available endpoint.
- Store-credit balance lookup/redemption, POS returns and pickup-window enforcement, exchange, and distinct repair/damage outcomes remain unimplemented.
- Secret values remain only in the ignored local `.env` and are not recorded here.
- Deferred, deliberately unaddressed for now: the dangling `[tool.pytest.ini_options] testpaths = ["tests"]` entry (no `tests/` directory exists yet) and the root `README.md` from the planned project structure. Revisit both once the remaining roadmap phases are done and decide what to do.

### Full Structural Audit: 2026-10-01

A full adversarial audit (tree mapping, rule/spec cross-check, documentation sync, and blind-spot review) was performed against the entire backend, migrations, and this document. One finding was approved and fixed; the rest are deliberately deferred and recorded here so they are not lost or silently reintroduced.

Fixed:

- `process_return` queried the order's `Payment` row without `with_for_update()` before summing prior refunds against `payment.amount`. Two concurrent partial-return requests against the same payment could each pass the "does not exceed original payment" guard before either committed, allowing combined refunds to exceed the original payment amount. The `Payment` query in `backend/app/services/returns.py` now takes the same row lock already used on `Order`, `ReturnRecord`, `ReturnItem`, and `InventoryUnit` in that function.

Deferred (not fixed, tracked for a future pass):

- `except ValueError, KeyError, TypeError:` (`authentication.py`) and `except VerifyMismatchError, VerificationError, InvalidHashError:` (`security.py`) are unparenthesized multi-exception clauses. Verified these compile and behave correctly as a tuple catch on this Python version, but the style is non-idiomatic and reads like invalid Python 2 syntax; parenthesize for clarity and linter compatibility.
- At the time of this audit, the "Planned Project Structure" diagram showed `backend/app/api/v1/` while routes lived flat in `main.py` behind an inline `APIRouter(prefix="/api/v1")`. The current tree remains unchanged; the POS route location is still an open architecture decision. The diagram remains a planned target.
- `.env.example` documents only the secrets with no default. It omits the optional settings that already have defaults in `config.py` (`JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `RESERVATION_DURATION_MINUTES`, `MOCK_SHIP_AFTER_SECONDS`, `MOCK_DELIVERY_AFTER_SECONDS`, `STANDARD_VAT_RATE`). Consider listing them as commented-out overrides for onboarding clarity.
- Registration (`POST /api/v1/auth/register`) discloses account existence via `409 "Customer already exists"`, while login always returns a uniform `"Invalid credentials"`. Common tradeoff, but worth a conscious decision rather than an implicit one.
- Historical audit note: CORS was listed as pending on 2026-10-01 because frontend and backend were then assumed to have separate origins. This was superseded on 2026-10-09 by the approved unified-domain Render rewrite plan; local frontend development will use a dev-server proxy. Do not add FastAPI CORS middleware or origin/port allowlists.
- No rate limiting/brute-force throttling exists on login or registration; acceptable for a portfolio demo, but worth an explicit out-of-scope note if that's the final decision.
- The pattern of database constraints intentionally allowing values no current code path can reach is now widespread, not just the documented `EXCHANGE` case: `orders.order_status` allows `CANCELLED`; `returns.status` allows `APPROVED/RECEIVED/INSPECTED/REJECTED`; `inventory_units.status` allows `RETURNED/REPAIR/DAMAGED/LOST`; `return_items.inspection_result` allows `REPAIR/DAMAGED`; `shipments.status`/`orders.fulfillment_status` allow `PICKED_UP/CANCELLED`. At the time of this audit, their future workflow ownership needed clarification. The 2026-10-06 POS design now reserves `PICKED_UP` for later collection and approves a new `COMPLETED` fulfillment status for ordinary POS handover; the latter is not yet in the model/database constraint and requires a migration.

Historical roadmap at the time of the 2026-10-01 audit: continue with guest-checkout design and APIs, followed by Stripe and transactional-email integrations, remaining POS and business APIs, backend tests, reporting, documentation, and API readiness. Reporting was subsequently removed from scope on 2026-10-09. Start the storefront and protected POS/operations frontend only after the backend work is complete, then deploy to Render. Preserve exact order-line-unit traceability and inventory status history.

## Resume Checkpoint: 2026-10-02

ECOM order creation now requests a Stripe-hosted Checkout Session inline for both authenticated and guest checkout. Both `POST /api/v1/orders` and `POST /api/v1/orders/guest` require a client-generated `Idempotency-Key`. The order stores a SHA-256 key hash and a request fingerprint bound to the authenticated customer UUID or guest email HMAC. An exact retry reuses the order; a reused key with a changed request or identity is rejected with `409`. Stripe receives a stable versioned idempotency key derived from the order ID, and the resulting Checkout Session ID, URL, and expiration are stored on the pending Stripe payment. The request omits `payment_method_types`, so Dashboard settings determine eligible payment methods; only Cards should be enabled for cards-only ECOM v1. The intended Session expiration is persisted before the Stripe network call and reused on retry. The order/payment state is committed before the Stripe network call; a Stripe failure returns `503` and does not immediately release the reservation. The response includes `checkout_url`.

User-run validation on 2026-10-02 passed `.venv/bin/python -m compileall backend/app migrations/versions`; `.venv/bin/python -m compileall backend/app/services/orders.py backend/app/services/checkout.py` also passed. Alembic applied migration `a5f3c9d2e701` from `818ca3c44540` successfully; this is the current migration head. Pylance reports no errors in the Checkout, order service, route, or payment model files. Postman guest tests returned `201`, then the same order ID/number for an exact retry, and `409` for the same key with a changed postal code. The user resent the exact request twice and confirmed both Checkout URLs matched. A read-only query confirmed the pending Stripe payment has a saved Session ID and URL, Session expiry `2026-10-02 15:57:47 +0200`, and one expired reservation. After adding the expired-order guard, retrying the same request returned `409` with `Order is no longer awaiting payment; start a new checkout with a new Idempotency-Key`. The user then expired the Stripe Session through Postman; the response confirmed `status=expired`, `payment_status=unpaid`, and `url=null`. Order `HM-2026-000014` remains `EXPIRED` because its three-minute reservation elapsed before the Checkout response. Late-payment inventory recheck/refund behavior is not implemented or verified. Browser return routes remain informational only and cannot confirm payment.

This checkpoint is historical and is superseded by the Stripe webhook validation checkpoint below.

## Resume Checkpoint: 2026-10-02 (Stripe Webhook Validation)

Stripe ECOM webhook implementation is present in `backend/app/main.py` and `backend/app/services/payments.py`. The handler verifies the raw request body and `Stripe-Signature`, acknowledges unsupported event types, processes `checkout.session.completed` and `checkout.session.async_payment_succeeded`, handles `refund.updated`, and records provider event IDs transactionally for idempotency. It rechecks and locks exact serialized inventory before fulfillment; if a paid late order's original unit has been reassigned, it creates an idempotent Stripe refund instead. The user fixed Stripe SDK metadata access in `validate_stripe_checkout_event` by converting the Stripe metadata object to a dictionary. User-run compileall passed for `backend/app/services/payments.py` after that fix.

Manual test-mode validation passed on 2026-10-02:

- A valid signed, unsupported `customer.created` event returned `200`; a deliberately invalid signature returned `400`.
- Checkout for `HM-2026-000015` returned webhook `200`; SQL confirmed order/payment `PAID`, fulfillment `READY_TO_SHIP`, one committed reservation, exactly one allocated unit, and no refund.
- Replaying the same event ID returned `200`; SQL confirmed the order remained paid with exactly one allocation and no duplicate refund.
- For the late-payment case, `HM-2026-000016` expired and its exact serialized unit was reserved by `HM-2026-000017`. Paying A returned `200` for both `checkout.session.completed` and `refund.updated`. SQL confirmed A remained `EXPIRED`, its payment/refund were `REFUNDED` for EUR 189.00, it had no allocated unit, and both events were recorded. B remained unpaid and held the unit at the time of the query. A subsequent product-detail request showed 997 Gold units available (497 at the warehouse and 500 at the store), consistent with the paid `HM-2026-000015` sale being the only completed sale in this test sequence and the expired B reservation being released.

These are manual integration checks, not automated tests. Order `HM-2026-000017`'s Stripe Checkout session was left unpaid; do not use its URL. Its local reservation was subsequently released by lazy expiration. The Stripe session may expire independently on Stripe's longer session lifetime.

Historical roadmap at this checkpoint: add database-backed automated tests for event deduplication, paid fulfillment, late-payment refund, and `refund.updated`. Signature rejection/acceptance is now covered by unit tests. The roadmap then proposed transactional-email and authentication enhancements, remaining POS/business APIs, reporting and CORS, followed by frontend work and deployment. Reporting was subsequently removed from scope on 2026-10-09, and the CORS proposal was superseded by the single-origin Render rewrite plan: separate frontend and FastAPI services share a unified domain, `/api/*` is rewritten to FastAPI, and local frontend development uses a dev-server proxy. This checkpoint remains historical and does not describe active work.

## Resume Checkpoint: 2026-10-02 (Automated Test Safety)

### Status Update: 2026-10-03

This update supersedes the earlier statements below that test-database isolation was still pending and that the fixture checked connectivity only.

The dedicated test PostgreSQL cluster was stopped when testing resumed. Its `postgresql.conf` had `port = 55432` commented out, so PostgreSQL attempted the default port `5432`, already used by another local server. The setting was uncommented, and the separate test cluster started successfully on `127.0.0.1:55432`. `.env.test` already targeted `half_moon_test_db` on that port and required no URL change for startup.

At this checkpoint, the test database was migrated to the then-current
Alembic head, `3e64f262f21b`, by first loading the validated
`tests/conftest.py` test URL and then invoking Alembic in the same process.
The application database was not targeted. The test database remains
unseeded.

`tests/conftest.py` now provides a per-test connection transaction and a SQLAlchemy session configured with `join_transaction_mode="create_savepoint"`. The FastAPI `api_client` fixture overrides `get_db` to use that same session. `tests/test_database_safety.py` verifies that an ORM session commit is visible inside the outer transaction and that rolling back the outer transaction removes the uniquely identified webhook-event row, confirmed through a separate connection. User-run `.venv/bin/python -m pytest -q tests/test_database_safety.py` passed with `8 passed` on 2026-10-03.

The Starlette TestClient deprecation warning was resolved by replacing the dev-extra dependency `httpx` with `httpx2>=2.13,<3`; the production dependency list is unchanged. The split production and dev locks resolve successfully. The dev lock was installed into the local `.venv`, after which the combined webhook and database-safety suites passed with `14 passed` and no warnings. The lock files were generated on macOS; validate the production lock on the Linux deployment target before deployment.

The six webhook tests cover signature validation, paid Checkout fulfillment and replay, late-payment refund while a competing order holds the unit, and `refund.updated` refund completion and replay. The paid-event test verifies order/payment become paid, reservation commits, the serialized unit is sold, and exactly one allocation, sale movement, shipment, and webhook-event record remain after replay. The late-payment test verifies the expired order is refunded without fulfillment while the competing reservation remains intact. The refund-update test verifies refund/payment state changes and event idempotency while order and fulfillment state remain unchanged. Stripe refund creation is mocked; these successful webhook tests make no outbound Stripe API requests. Test writes roll back after each test. During the late-payment test, committing the seeded order graph inside the test savepoint was necessary so the route's intentional rollback would not remove fixture data; the outer test transaction still rolls it all back. The combined webhook and database-safety run completed with `14 passed` and no warnings.

The approved automated test scope for Stripe webhook outcomes and isolated test-database safety is complete. This does not mean backend-wide test coverage is complete.

The authenticated `simulate-payment` endpoint, service, and request schema were removed on 2026-10-05 after reviewing parity with the Stripe flow. The real signed Checkout webhook continues to use the same `_complete_paid_ecom_order` helper that the simulator previously called. After removal, the user started the isolated test PostgreSQL cluster and ran `.venv/bin/python -m pytest -v tests/test_stripe_webhooks.py tests/test_database_safety.py`; all 14 tests passed with no warnings. The OpenAPI check also passed: the simulator route is absent while authenticated orders, guest orders, and the Stripe webhook remain registered. No database schema or data migration is required. The change affects the API in every deployment when that code is deployed; it does not connect to or modify the live database. Stripe handles declined-attempt details, and the application intentionally does not persist each decline as a separate Payment row. After a decline, Checkout may remain available for retry while the application payment/order stay pending until authoritative success or existing reservation expiry/late-payment handling.

Historical Gmail transactional-email checkpoint (earlier on 2026-10-05, before delivery implementation): the notification model and enqueue helper were in place, but registration still printed a local verification URL and the Gmail sender, worker, and retry processor had not yet been implemented. Migration `b841c6ad9f20` was applied to the isolated test database and then to the development database; the OAuth setup was completed later that day. This entry records that earlier state only. Current delivery status and remaining work are summarized above. Production-lock validation for the Linux deployment target remains deferred until deployment setup.

The following records the original test-safety baseline before the October 3 isolation and webhook tests; it is retained as history and superseded by the current status above. At that baseline, `tests/test_stripe_webhooks.py` had three unit tests for valid signatures, invalid signatures, and missing webhook-secret configuration. `tests/test_database_safety.py` had seven checks for the test database URL, rejecting unsafe targets, and connecting to the expected isolated PostgreSQL identity. `tests/conftest.py` loads `HALF_MOON_TEST_DATABASE_URL` from ignored `.env.test`, validates the PostgreSQL driver, exact test database and role, loopback host, and port `55432`, then redirects `DATABASE_URL` before application imports. Its session-scoped engine fixture confirms database name, role, server address, and port using the restricted test role. The separate PostgreSQL cluster is at `~/Library/Application Support/Half Moon/pg-test-cluster` and listens only on `127.0.0.1:55432`; it was initialized with SCRAM authentication. The dedicated `half_moon_test_db` database is owned by `half_moon_test_user`. That role has no superuser, database-creation, role-creation, or RLS-bypass privileges; `CONNECT` is revoked on `postgres` and `template1` and granted only on `half_moon_test_db`. The normal application `DATABASE_URL` uses an elevated development role and must never be used by tests. Passwords and URL credentials remain only in ignored local configuration and are not documented.

The user-run focused command `.venv/bin/python -m pytest -q tests/test_stripe_webhooks.py tests/test_database_safety.py` completed with `10 passed` on 2026-10-02. Diagnostics reported no errors in `tests/conftest.py`. The restricted role correctly cannot inspect PostgreSQL's `data_directory`; the identity fixture no longer requests that privileged setting. It uses `host(inet_server_addr())` so the loopback assertion compares `127.0.0.1`, not the CIDR-form `127.0.0.1/32`.

This was the next-session plan at the original October 2 checkpoint; it was completed by the October 3 test-isolation/webhook work and October 5 simulator retirement. Its POS documentation prerequisite was started on 2026-10-06: the checklist exists as a draft and requires developer review before implementation. Continue checking each POS process against the approved documentation and its tests throughout implementation.

## Resume Checkpoint: 2026-10-06 (POS Architecture Sync)

This is a historical snapshot. Its earlier open-policy notes are superseded by
the current POS schema and migration status in the latest checkpoint at the
end of this document and the active POS process-and-test checklist.

POS checkout is still unimplemented. The current repository has an `Order` model that accepts channel `POS` and a `Payment` model that accepts `CARD` and `CASH`, but no POS schema, service, or route. Business routes currently live in `backend/app/main.py`; the POS route location is still undecided. This checkpoint records approved target behavior, not implemented functionality.

The POS process-and-test checklist is drafted in `docs/pos-process-and-test-checklist.md` and remains pending developer review. Decisions recorded there include simulated staff authentication with both roles able to create ordinary sales; optional customer association and later anonymous-sale claim using the order number plus a one-time simulated-receipt code whose SHA-256 hash is stored in an order-linked POS-specific record; registered, email-verified accounts for refunds; a POS return window of 14 days from `paid_at`, inclusive through the deadline, with original-method refund or store-credit issuance; store-first inventory with warehouse fallback; same-key idempotent retries matching ECOM; shared pricing, VAT, and discount rules; immediate paid cash sales; and Stripe test-mode PaymentIntent card payments whose signed `payment_intent.succeeded` event is authoritative. The POS waits for webhook processing before showing a card sale complete, and declined attempts retain the reservation for retry until its three-minute expiry without creating a separate local `Payment` row per decline.

An ordinary POS handover is designed to leave the order and payment `PAID`, mark serialized units `SOLD`, set shared `paid_at`, and set `fulfillment_status=COMPLETED`. `PICKED_UP` is reserved for later collection, such as ECOM store pickup. The current order model/database does not contain `paid_at` or permit `COMPLETED`; the required migration has not been created or applied. ECOM store-pickup progression, windows, and notifications, POS return processing, store-credit balance/redemption, failed-claim handling, and PaymentIntent failure/cancellation event handling remain incomplete or open. POS return outcomes are original-method refund or store-credit issuance. The signed `payment_intent.succeeded` event is the authoritative POS card-success signal. The POS return window is 14 days from `paid_at`, inclusive through the deadline; the receipt claim code is for ownership verification during that window and expires afterward. Historical paid orders without an exact recoverable payment time remain null, with no approximate backfill. Do not substitute ECOM's delivered-at anchor.

## Resume Checkpoint: 2026-10-06 (Store-Credit Retirement Approved)

This is a snapshot from before implementation. Its pending-status statements
are historical and are superseded by the implementation update below.

This checkpoint supersedes the earlier POS return-outcome notes above: store credit is to be removed from both ECOM and POS, and all returns will use simulated refunds to the original payment method. The unused `EXCHANGE` outcome is also to be removed. Keep `requested_outcome` as an audit/API field restricted to `REFUND`; requests using retired outcomes must fail validation. This is an approved target, not an implemented change.

The current ECOM return service still issues store-credit ledger credits when requested. Active models and database constraints still permit store-credit payment/refund/return values, and the store-credit ledger model remains registered. There is no store-credit balance or redemption endpoint/service, and repository code does not create ledger `DEBIT` entries. No application code, schema, migration, or database data has yet been changed for this retirement.

Before implementation, perform a read-only preflight of every ledger entry and its links to returns, orders, payments, and prior refunds. The forward migration must convert reconciled outstanding credit balances into simulated `ORIGINAL_PAYMENT` refunds, normalize affected return outcomes to `REFUND`, and update related refund/payment/order statuses consistently. It must abort rather than discard unmatched balances, proceed with an unreconciled `DEBIT`, or create a refund that would exceed the original payment after prior refunds. Only after safe reconciliation may it drop the ledger and narrow the active outcome/payment/refund constraints. The latest recorded Alembic head is `b841c6ad9f20`; implement this as a new forward revision, leaving historical revisions and dated checkpoints unchanged.

The current return request schema, return processing service, payment/refund/return models, model registry, and generated packaging manifest are in scope for removal. Regenerate packaging metadata after source removal rather than editing it manually. Existing store-credit history in earlier checkpoints documents prior behavior and must remain as history; this checkpoint and the active architecture describe the approved target.

## Implementation Update: 2026-10-06 (Store-Credit Retirement Complete)

The active request schema now accepts only `REFUND`; return processing creates
only simulated original-payment refunds; payment, refund, and return models no
longer allow store-credit/exchange values; and the ledger model has been
removed from the model registry and source tree. Focused tests cover retired
outcome rejection and the tightened model constraints.

Forward migration `a6f90c12d435` was added after head `b841c6ad9f20`. It locks
the affected tables, checks ledger/payment/refund reconciliation and refund
limits, converts valid credit balances, updates affected payment/order statuses,
normalizes return outcomes, and removes the ledger. The user calls the primary
`half_moon_db` database “production” and the isolated `half_moon_test_db`
environment “development” (or test). Both were at `b841c6ad9f20` before
migration and are now at `a6f90c12d435`.

The read-only preflight of `half_moon_test_db` found an empty ledger. The
read-only preflight of `half_moon_db` found one `CREDIT` of EUR 189 and two
return rows to normalize; all reconciliation blocker counts were zero. After
separate approval, the migration created one simulated `ORIGINAL_PAYMENT`
refund for EUR 189, updated the related payment and order to `REFUNDED`,
normalized the two return rows, and dropped the ledger. The migrated schemas
and all three narrowed constraints were verified. No real payment-provider
refund was made.

The full test suite passed after migration (`58 passed`). Tests use
`half_moon_test_db` and test-only app credentials; they do not connect to
`half_moon_db`. The databases currently run locally on loopback: `half_moon_db`
on port `5432`, and `half_moon_test_db` on port `55432`. No separately hosted
application deployment has been created.

The preflight files
`scripts/preflight_store_credit_retirement.py` and
`scripts/preflight_store_credit_retirement.sql` are for an existing database
still at `b841c6ad9f20` with the ledger table present. Do not run them against
the two migrated databases. If another existing database at that revision is
introduced, run its read-only preflight and review the results before applying
the migration. Take a verified backup before applying this irreversible
migration to any additional database; downgrade intentionally refuses.

## Resume Checkpoint: POS Decisions Confirmed

This is a historical snapshot of the approved target behavior before the
initial POS schema/model and migration drafts were added. Current implementation
status is recorded in the latest checkpoint at the end of this document.

Three previously open POS policies are now confirmed:

- Five failed anonymous-order claim attempts lock further claims for that
  order until a manager resets the lock.
- POS returns reuse ECOM's two-step request/process workflow. A verified
  customer requests a return, then a separate process call simulates receipt,
  inspection, restocking to the original POS sale source location, and refund
  to the original payment method.
- `payment_intent.payment_failed` is retryable: payment and order remain
  pending and the reservation remains active until the three-minute expiry.
  An explicit `payment_intent.canceled` is terminal: the order becomes
  `CANCELLED`, payment and order payment status become `FAILED`, reservations
  become `RELEASED`, and units return to `IN_STOCK` with `RELEASE` movements
  and `released_at` set. Both event types are acknowledged idempotently and
  neither commits a sale.

Discounts and vouchers are explicitly out of scope for both ECOM and POS v1.
Checkout does not expose or apply discount/voucher inputs, and the existing
order and line discount snapshot columns remain zero. A future discount
feature requires a separate design decision.

For anonymous POS receipts, the permanent claim record stores only an
HMAC-SHA-256 digest keyed with a dedicated application secret, protecting the
short numeric code from offline guessing if the database alone is exposed. A
separate encrypted delivery copy is retained only until the
POS acknowledges receipt delivery, then deleted; an unacknowledged copy is
deleted after the inclusive 14-day claim deadline by startup and daily
cleanup sweeps. Expired codes cannot be delivered even before cleanup runs.
Lost receipts are not recoverable and managers cannot replace the code;
manager reset only clears the five-failure lock. The claim record retains
only the most recent reset timestamp and manager UUID; no reset history is
maintained.

These remain target behaviors, not completed POS functionality. See
`pos-process-and-test-checklist.md` for the current process, acceptance
criteria, and next implementation steps.

## Historical Checkpoint: POS Schema Draft and Credential Rotation (2026-10-06)

At this checkpoint, POS checkout, payment handling, returns, and claim
routes/services were not implemented. The tree contained
`backend/app/schemas/pos.py`, the
`PosOrderClaim` and `PosReceiptDelivery` model files, and the nullable
`Order.paid_at` ORM field. Migration `464acebca437` (parent
`a6f90c12d435`) was the Alembic head at this checkpoint. It had been applied
and verified on both isolated `half_moon_test_db` and primary
`half_moon_db`; both were at `464acebca437`. The primary migration was applied
without a backup after a live identity/revision preflight, and its resulting
schema and revision were verified. The ORM `ck_orders_fulfillment_status`
includes `COMPLETED` and matches the migration. No POS checkout integration
tests have been added.

The POS design decisions and field-level schema plan were approved, and the
schema migration is complete on both configured databases. This checkpoint is
superseded by the current POS implementation status below.

The active process-and-test checklist records the approved `HM-` plus eight
digit claim-code format, HMAC-SHA-256 digest using a dedicated key, encrypted
receipt delivery retention rules, staff polling after card confirmation,
discount-free v1, cancellation handling, original-source return restocking,
and the confirmed claim reset policy. Those remain target behavior until
implemented and tested.

## Resume Checkpoint: POS Cash Checkout Foundation (2026-10-07)

The POS-only service `backend/app/services/pos.py` creates pending POS orders
with server-calculated catalog prices and VAT, idempotency scoped to POS and
the initiating staff member, store-first whole-order inventory reservations,
order-line snapshots, and staff-attributed reservation movements. Its shared
POS sale-commit helper supports cash and card completion. Cash records
`CASH`/`NONE`; successful completion sets timezone-aware `paid_at`, commits
reserved units and sale movements, and stores the claim-code HMAC plus
encrypted receipt-delivery copy. Ten focused tests in
`tests/test_pos_checkout.py` pass, covering pending order creation,
stock-source selection, idempotency, cash-sale completion/replay, and mocked
PaymentIntent creation/reuse and configuration failure.

The PaymentIntent-creation function has been tested with a mocked Stripe
client; no live Stripe test-mode request has been made. The POS late-payment
reservation-safety helper has focused coverage for safe restoration, extension
of an active reservation, and refusal when a unit is unavailable. The POS
PaymentIntent-event validator is directly tested for all three supported event
statuses, mismatched status/metadata/amount/currency, and an unsupported event.
The POS PaymentIntent event processor is tested for retryable failure,
cancellation/release, successful sale/replay, and late-success refund when the
original unit is unavailable. The shared signed Stripe webhook route dispatches
POS PaymentIntent events to that processor; signed-route tests cover each
supported event type and ensure non-POS PaymentIntent events are not sent to the
POS path. The staff-protected `POST /api/v1/staff/pos/checkout` route handles
cash and card setup; authenticated route tests cover both branches. No live
Stripe test-mode request has been made. POS status polling, receipt
delivery/acknowledgement, claim handling, POS returns, and ECOM pickup behavior
remain incomplete. Next, perform a controlled Stripe test-mode integration
check, then add the remaining staff POS routes. Preserve the existing ECOM
Checkout Session and sale-completion path unchanged.

## Current Implementation Update: Transaction, Concurrency, and Auth Hardening

The transaction-safety, validate-before-mutate, row-locking, and customer-login
verification changes are complete (100%); the latest full suite before the
pickup-expiry change had **112 tests passed**, including the deterministic POS
multi-variant lock-order regression test, POS return coverage, expiry-cleanup
regression, and PaymentIntent parameter validation. The partial unique index
is present on both local databases. Both local databases are at verified
Alembic head `a41c7e92b6d3`.

- `release_expired_reservations` has no internal commit. It stages changes for
  its caller; read services conditionally commit when cleanup releases
  reservations.
- Service mutation flushes and commits use `flush_or_rollback` and
  `commit_or_rollback`; each helper rolls back and re-raises SQLAlchemy errors.
- Checkout and return processing validate before inventory mutation or
  append-only movement staging. Refund totals remain exact `Decimal` values.
- Locking flows follow Order -> Payment -> Reservation -> InventoryUnit.
  Multi-item ECOM checkout, POS allocation, and return-unit locks are acquired
  in deterministic UUID order. Expiry cleanup uses deterministic selection
  and `SKIP LOCKED` for row-lock acquisition.
- PostgreSQL has a partial unique index on
  `reservations.inventory_unit_id` where `status = 'ACTIVE'`. Migration
  `8d72372141d5` creates this index and is an ancestor of verified head
  `c7f48d21a063`; the index is present at current verified head
  `a41c7e92b6d3` in both local databases.
- Customer login rejects unverified email accounts before issuing an access
  token. The regression is covered by the test suite.

POS checkout (cash/card setup, sale commit, and signed PaymentIntent
processing), staff status and receipt routes, receipt acknowledgement and
cleanup, customer claims, manager claim-lock reset, and POS returns are
implemented. The focused POS checkout and return-policy run passed 49 tests.
The full project suite most recently passed 112 tests before the pickup-expiry
change; afterward, all 8 `test_fulfillment.py` tests passed, and the full-suite
rerun remains pending. The focused expiry-cleanup regression passed all 4
cases, and the focused POS PaymentIntent request-parameter test passed 1 case.
Live Stripe test-mode POS validation also succeeded for one controlled card
checkout. ECOM store-pickup readiness, progression, notifications, and
expiry/refund/restock are implemented; pickup returns remain unimplemented and
paused at the user's request.

### POS Stripe Test-Mode Validation and Expiry Cleanup (2026-10-08)

At this historical POS validation checkpoint, the isolated test database was
at the then-current Alembic revision immediately before the later
pickup-persistence migration. It has since advanced to verified head
`c7f48d21a063`. The database was seeded with 4 products, 18 variants, 18,000
serialized units, and 2 test staff accounts. The primary database was not
changed during this POS test.

A first POS card attempt created order `HM-2026-000721`
(`d11420f9-4d5c-4d5b-b55d-95927405330c`), but no sale completed. Its order
status is `EXPIRED`, payment status is `UNPAID`, fulfillment status is
`NOT_STARTED`, and its database payment record remains `PENDING`. Stripe's
test-mode confirm request log showed `automatic_payment_methods.enabled=true`
and `allow_redirects=always`, while the confirm request omitted `return_url`.
Stripe rejected confirmation with `invalid_request_error`, stating that a
return URL is required when redirect methods are allowed. The intent had no
reported `last_payment_error` or `next_action` and was subsequently canceled.
No `payment_intent.succeeded` webhook or successful payment was received.

Expiry cleanup was updated in POS status, catalog detail, variant stock, and
the then-existing staff inventory summary read path to commit when they release expired
reservations. The summary endpoint was retired on 2026-10-09. The focused
regression test passed 4 cases at that time. POS PaymentIntent
creation now sets `automatic_payment_methods.allow_redirects` to `never`;
the focused mocked request-parameter test passed 1 case. This change is
limited to POS PaymentIntents and does not alter ECOM. The full project suite
subsequently passed 112 tests after these changes.

The restarted backend's POS status request committed cleanup for the existing
order. Read-only database verification confirmed the reservation is
`EXPIRED` with `released_at` set, its inventory unit is `IN_STOCK`, the order
has zero active reservations, the variant has 1,000 units in stock and zero
reserved, and exactly one `RELEASE` movement is present. The primary database
was not changed by this POS test.

A fresh one-unit POS card checkout was then created as order
`HM-2026-000727`. Its test-mode PaymentIntent was verified with amount `18900`
EUR cents, `allow_redirects=never`, and no last-payment error. Confirmation
with test method `pm_card_visa` returned `succeeded`; the Stripe listener
received `payment_intent.succeeded` and returned HTTP 200.

The POS status API returned `PAID` / `PAID` / `COMPLETED`. Read-only database
verification confirmed payment record `PAID`, reservation `COMMITTED`,
inventory unit `SOLD`, zero active reservations, variant stock of 999
`IN_STOCK`, 0 `RESERVED`, and 1 `SOLD`, plus exactly one `SALE` movement. No
live charge was made. The controlled POS test-mode card checkout is complete.

The full project suite subsequently passed 112 tests after the expiry-cleanup
and redirect-method changes. ECOM pickup-window enforcement, progression, and
notifications remain separate pending work.

## ECOM Store-Pickup Progression and Notifications (2026-10-08)

Paid ECOM store-pickup orders validate that reserved inventory is sourced from
`PHYSICAL_STORE`, enter `READY_FOR_PICKUP`, and store `ready_for_pickup_at` plus
a seven-calendar-day pickup deadline calculated in `Europe/Rome`. The
`ORDER_READY_FOR_PICKUP` notification is enqueued in the payment transaction.
Its email copy asks the customer to collect the order within seven days.

The existing fulfillment worker progresses eligible store-pickup orders to
`PICKED_UP` only after the configured mock delay and through the inclusive
pickup deadline. It records `Shipment.picked_up_at`, updates the order
fulfillment status, and enqueues `ORDER_PICKED_UP`. The authenticated order
status endpoint exposes the pickup timestamps and remains read-only.

User-run focused tests passed: pickup deadline DST cases (2), pickup
progression and status timestamp cases (2), approved order-email formatting
cases (5), and paid-checkout webhook cases (2). The webhook regression verifies
one ready notification for pickup, none for shipping, and no duplicate on
replay. These results do not verify live Gmail delivery. At this earlier
checkpoint, the full-suite rerun was pending; it later passed 138 tests on
2026-10-09 after reporting removal.

Approved expiry policy: treat a pickup as expired only after the inclusive
deadline has passed. The implementation described in the 2026-10-08 follow-up
below sets shipment and order fulfillment status to `CANCELLED`, leaves
`order_status` as `PAID`, and marks the original payment and order payment
status `REFUNDED` using the project's simulated original-payment refund
behavior. It restores each sold unit to the shipment's original source location
and adds a `PICKUP_EXPIRY` movement in the same transaction as the unit, refund,
status, and notification changes. `ORDER_PICKUP_EXPIRED` uses the approved
copy explaining the refund is simulated and no payment-provider refund was
sent. Pickup-order returns using the approved `picked_up_at`-anchored 14-day
window remain pending. This update supersedes earlier current-state summaries
that described all ECOM pickup progression and notifications as future work.

## ECOM Pickup Expiry Implementation (2026-10-08)

The fulfillment worker now selects only paid ECOM store-pickup orders whose
pickup deadline has strictly passed. At the deadline itself,
pickup remains within its inclusive window. Expiry locks the paid payment
before inventory units, validates the paid amount and complete sold-unit
allocation, restores every unit to `Shipment.source_location_id`, appends
`PICKUP_EXPIRY` movements, records a simulated full original-payment refund,
sets shipment and order fulfillment to `CANCELLED` while preserving
`order_status=PAID`, and queues `ORDER_PICKUP_EXPIRED`. These database changes
and the outbox notification are committed together. No payment-provider refund
is issued.

On 2026-10-08, the six-case approved order-email formatting test passed, and
the focused expiry-worker regression passed one case. That regression checks
the exact-deadline boundary, full refund record, inventory restoration,
per-unit movements, notification linkage, and idempotency on a repeat worker
pass. The normal pickup-progression regression passed one case after this worker
change, and the full `test_fulfillment.py` module passed all 8 tests. The full
project suite has not yet been rerun. Pickup-order returns remain unimplemented
and use the approved inclusive 14-day window anchored at `picked_up_at`. The
planned pickup-return test has not been added or run; this work is paused at
the user's request as of 2026-10-08.

## ECOM Store-Pickup Returns Reauthorized (2026-10-09)

The previously paused pickup-return work is reauthorized. Pickup returns will
use the existing shared `POST /api/v1/orders/{order_id}/returns` and
`POST /api/v1/returns/{return_id}/process` endpoints; no pickup-only route is
introduced. A request is eligible only for a paid ECOM store-pickup order
whose shipment has been picked up, and the inclusive 14-day window is anchored
at `Shipment.picked_up_at`. Existing verified-customer, purchased-allocation,
one-return-per-allocation, simulated original-payment refund, and source-
location restock rules continue to apply. The existing models already contained
the required fields, so no schema or migration change was needed. The
shared return service now accepts eligible picked-up orders, preserves the
existing shipping and POS eligibility paths, and applies the existing source-
location restock and simulated refund process. Regression tests cover the
inclusive deadline, expired requests, uncollected orders, and successful
refund/restock processing. The first targeted run reported 5 passed and 4
setup errors because PostgreSQL at `127.0.0.1:55432` was stopped. After the
isolated test server was started, `tests/test_returns_policy.py` passed all 9
tests and the full project suite passed all 134 tests. Compilation of the
changed Python files succeeded.

## POS Staff-Assisted Returns and Order Details (2026-10-12)

Customers must claim an anonymous POS sale using the order number and one-time
receipt claim code before a return. Claiming requires an active, email-verified
customer account and consumes the code. Staff lookup after claim uses the order
number and does not reuse the claim code.

`GET /api/v1/orders/{order_id}` returns order details only to the owning
customer, including product/variant snapshots, exact serialized allocation
IDs, and whether each allocation has already been returned. Monetary values in
this response are strings. `GET /api/v1/staff/pos/orders/lookup?order_number=...`
returns the same details for staff and only resolves claimed POS orders. An
unclaimed lookup returns `409` with detail:
`This POS order must be claimed by a customer before staff can view its details`.

Five failed claim-code attempts lock a claim for 30 seconds. The customer may
retry after the cooldown; a `STORE_MANAGER` may also reset an active lock by
order number using `POST /api/v1/staff/pos/orders/claim/reset`. The response
contains reset status only and does not reveal the unclaimed order's details.
The existing manager reset route by order UUID remains available.

POS returns are completed by `POST
/api/v1/staff/pos/orders/{order_id}/returns`. Both `SALES_ASSOCIATE` and
`STORE_MANAGER` may use it. One staff action validates the claimed customer,
paid/completed sale, inclusive 14-day `paid_at` window, exact unreturned units,
and original payment balance, then atomically records the completed return,
simulated original-payment refund, source-location restock, and staff audit
event. The E-Commerce request and process routes remain ECOM-only and reject
POS orders; a separate customer POS route automatically completes eligible
returns for the verified owner. Existing tables and constraints support this
flow; no database migration is required for the customer-only POS increment.
All 14-day deadlines are calculated from
timezone-aware UTC instants so daylight-saving transitions do not shift the
inclusive 14-day duration. Regression coverage includes the DST boundary and
the legacy paid-order case. Pylance reported no errors in the modified service
files, and the focused POS checkout and return suites passed all 82 tests on
2026-10-12.

A controlled manual API smoke test against local `half_moon_db` also passed on
2026-10-12: a cash POS sale was claimed by a verified customer, then returned
by a `SALES_ASSOCIATE`. The API reported a completed simulated refund, and
read-only database verification confirmed the completed return, one return
inventory movement, one unit restocked at `PHYSICAL_STORE`, and one successful
staff audit event. No card payment or real refund was made. The test records
were intentionally left in the database with user approval. This manual
smoke test does not change the pytest database restriction.

Manual browser acceptance on 2026-10-21 verified staff lookup and exact-unit
return for `HM-2026-000029`, then manager reset of a five-attempt claim lock
for `HM-2026-000030`. The customer successfully claimed the second order after
reset through the authenticated claim API (the customer claim form was not
yet implemented at that time), and the manager completed its simulated return.
Both EUR 79.00 cash test sales were fully returned and their units restocked;
no real payment or refund was made. These acceptance records remain in the
local primary database.

After adding the customer claim form, the frontend production build and lint
passed, and the full backend suite passed 189 tests. Browser validation in an
authenticated customer session submitted a syntactically valid dummy claim
for a nonexistent order; the real API returned the expected `POS order not
found` error and the page displayed it. This confirmed the protected route,
API integration, and error rendering without changing database state. The
later 2026-10-23 browser acceptance documented in
`docs/pos-process-and-test-checklist.md` verified a successful customer claim,
POS purchase history/detail, and eligible return; it supersedes the earlier
statement that only the API success path had been exercised.

### Manual API and POS verification (2026-10-21)

Individual local Postman and terminal checks verified customer login
(`200`), refresh (`200`), logout (`204`), staff login/profile and manager
authorization (`200`), and cross-role access rejection (`401` in both
directions). Product list/detail returned `200`; the selected variant had
`997` available units before the POS checks.

Customer demo order `HM-2026-000031` was created as a one-unit cash sale,
claimed, and returned through the customer POS route. The return completed as
a simulated EUR 189.00 refund, the order payment status became `REFUNDED`, and
the unit was marked returned/restocked; available stock returned to `997`.
Staff cash order `HM-2026-000032` was created, its receipt was retrieved and
acknowledged, and its status was `PAID` / `COMPLETED`. It remains unclaimed;
staff lookup correctly returned `409` without order details. Its sale left
available stock at `996`. No real payment or payment-provider refund occurred.

The active API database identity was not independently verified, so these
manual order records must not be attributed to `half_moon_test_db`. No claim
codes, access tokens, or credentials are recorded. The lookup message was
corrected after the manual `409` check; the updated exact message is covered by
the passing POS test module. The full backend suite, card checkout, Gmail OAuth,
and manager reset of a newly locked claim were not run in this session.
