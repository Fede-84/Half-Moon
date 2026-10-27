# Half-Moon — Omnichannel Jewelry Portfolio

Half-Moon is a full-stack jewelry retail portfolio project that brings together an online storefront and staff point-of-sale (POS) experience. It demonstrates shared catalog, inventory, order, and payment workflows for a small omnichannel retailer.

## Project overview

The monorepo includes a customer-facing React storefront, staff POS and operations interfaces, and a FastAPI backend. PostgreSQL persistence and database migrations support the application’s business workflows.

## Tech stack

| Area | Technologies |
|---|---|
| Backend | Python 3.14, FastAPI, SQLAlchemy 2.0, Pydantic v2 |
| Frontend | React 19, Vite 8, TypeScript 6 |
| Database | PostgreSQL, Alembic migrations |
| Payments | Stripe integration |

## Production architecture

The application is designed around a **same-origin proxy rewrite**. The browser sends API requests to `/api/*` on the frontend’s origin, and the hosting platform routes those requests to the FastAPI backend.

```text
Browser
  ├── storefront and POS → frontend origin
  └── /api/*             → hosting rewrite → FastAPI backend
```

Because frontend and API requests use the same origin, backend `CORSMiddleware` is not required. The `/api/*` rewrite must be configured and verified in the hosting environment.

During local frontend development, Vite proxies `/api` requests to the backend.

## Repository layout

| Path | Purpose |
|---|---|
| `backend/app/` | FastAPI application, integrations, models, schemas, and services |
| `frontend/src/` | React application, API client, storefront, account, checkout, and POS features |
| `migrations/` | Alembic database migrations |
| `seed/` | Database seed and migration-support resources |
| `tests/` | Backend test suite |
| `requirements-prod.lock` | Locked production Python dependencies |
| `requirements-dev.lock` | Locked development Python dependencies |
| `pyproject.toml` | Python project configuration |

## API orientation

Frontend API traffic uses the same-origin `/api/*` path and is routed to the backend by the hosting rewrite.

The Stripe webhook endpoint is:

- `POST /api/v1/webhooks/stripe` — receives Stripe webhook events and verifies the request signature using the raw request body.

Other authentication, catalog, checkout, order, and POS route definitions are maintained in the backend application; consult those definitions for the exact paths.

## Tester access and credentials

This repository does **not** publish staff POS credentials. Personal staff credentials are private and must not be committed to GitHub or included in public documentation.

Access to the protected POS requires an account provisioned by the project owner. Do not assume a production demo account exists merely because seed resources are present in `seed/`.

## Tester verification guide

1. Explore `frontend/` for the customer storefront and staff-facing interface.
2. Browse `frontend/src/features/` for account, authentication, checkout, and POS features.
3. Explore `backend/app/` for API integration and business logic. The `services/`, `models/`, and `schemas/` directories organize backend workflows and data.
4. Review `migrations/` for database schema history and `seed/` for seed resources.
5. Use the backend route definitions to confirm exact API paths; the Stripe webhook route is documented above.
6. Project validation notes record **201 passing backend tests on October 27, 2026**. This is a historical result, not a claim that tests were rerun for this README or deployment.
