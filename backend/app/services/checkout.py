import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session
from stripe import StripeError
from stripe.params.checkout import SessionCreateParams, SessionCreateParamsLineItem

from backend.app.core.config import settings
from backend.app.core.data_protection import create_email_lookup_hmac, decrypt_pii
from backend.app.db.database import commit_or_rollback, flush_or_rollback
from backend.app.integrations.stripe.client import get_stripe_client
from backend.app.models.order import Order
from backend.app.models.order_line import OrderLine
from backend.app.models.payment import Payment
from backend.app.schemas.orders import (
    CheckoutPaymentStatusResponse,
    CreateOrderRequest,
    GuestCreateOrderRequest,
)
from backend.app.services.customers import normalize_email
from backend.app.services.orders import (
    PendingOrderData,
    create_guest_pending_order,
    create_pending_order,
)


class CheckoutUnavailableError(Exception):
    pass


def _create_key_hash(idempotency_key: str) -> str:
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


def _create_request_fingerprint(
    request: CreateOrderRequest | GuestCreateOrderRequest,
    identity_scope: str,
) -> str:
    request_data = request.model_dump(mode="json")

    if isinstance(request, GuestCreateOrderRequest):
        request_data["email"] = normalize_email(str(request.email))

    canonical_request = json.dumps(
        {"identity_scope": identity_scope, "request": request_data},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()


def create_customer_checkout(
    db: Session,
    customer_id: UUID,
    customer_email_ciphertext: str,
    request: CreateOrderRequest,
    idempotency_key: str,
) -> dict[str, object]:
    sorted_request = request.model_copy(
        update={
            "lines": sorted(
                request.lines,
                key=lambda line: str(line.variant_id),
            )
        }
    )
    pending_order: PendingOrderData = create_pending_order(
        db,
        customer_id,
        sorted_request,
        _create_key_hash(idempotency_key),
        _create_request_fingerprint(request, str(customer_id)),
    )
    checkout_url = _ensure_checkout_session(
        db,
        pending_order["id"],
        decrypt_pii(customer_email_ciphertext),
    )

    return {**pending_order, "checkout_url": checkout_url}


def create_guest_checkout(
    db: Session,
    request: GuestCreateOrderRequest,
    idempotency_key: str,
) -> dict[str, object]:
    email = normalize_email(str(request.email))
    sorted_request = request.model_copy(
        update={
            "lines": sorted(
                request.lines,
                key=lambda line: str(line.variant_id),
            )
        }
    )
    pending_order: PendingOrderData = create_guest_pending_order(
        db,
        sorted_request,
        _create_key_hash(idempotency_key),
        _create_request_fingerprint(
            request,
            create_email_lookup_hmac(email),
        ),
    )
    checkout_url = _ensure_checkout_session(
        db,
        pending_order["id"],
        email,
    )

    return {**pending_order, "checkout_url": checkout_url}


def get_checkout_payment_status(
    db: Session,
    checkout_session_id: str,
) -> CheckoutPaymentStatusResponse:
    order = (
        db.query(Order)
        .join(Payment, Payment.order_id == Order.id)
        .filter(
            Order.channel == "ECOM",
            Payment.provider == "STRIPE",
            Payment.payment_method == "CARD",
            Payment.provider_checkout_session_id == checkout_session_id,
        )
        .one_or_none()
    )
    if order is None:
        raise ValueError("Checkout status was not found")

    return CheckoutPaymentStatusResponse(
        order_status=order.order_status,
        payment_status=order.payment_status,
    )


def _ensure_checkout_session(
    db: Session,
    order_id: UUID,
    customer_email: str,
) -> str:
    order = db.query(Order).filter(Order.id == order_id).with_for_update().one()
    payment = (
        db.query(Payment)
        .filter(
            Payment.order_id == order.id,
            Payment.provider == "STRIPE",
            Payment.payment_method == "CARD",
        )
        .with_for_update()
        .one_or_none()
    )
    if order.order_status != "PENDING_PAYMENT":
        raise ValueError(
            "Order is no longer awaiting payment; start a new checkout "
            "with a new Idempotency-Key"
        )

    if payment is None:
        payment = Payment(
            order_id=order.id,
            payment_method="CARD",
            provider="STRIPE",
            amount=order.total_gross,
            currency=order.currency,
            status="PENDING",
        )
        db.add(payment)
        flush_or_rollback(db)

    order_id = order.id
    order_number = order.order_number
    payment_id = payment.id
    if payment.provider_checkout_session_id and payment.checkout_url:
        if (
            payment.checkout_session_expires_at is not None
            and payment.checkout_session_expires_at <= datetime.now(timezone.utc)
        ):
            raise ValueError(
                "Checkout session expired; start a new checkout with a new "
                "Idempotency-Key"
            )

        checkout_url = payment.checkout_url
        commit_or_rollback(db)
        return checkout_url

    now = datetime.now(timezone.utc)
    order_lines = (
        db.query(OrderLine)
        .filter(OrderLine.order_id == order.id)
        .order_by(OrderLine.created_at)
        .all()
    )
    if payment.checkout_session_expires_at is None:
        expiration_timestamp = int((now + timedelta(minutes=31)).timestamp())
        payment.checkout_session_expires_at = datetime.fromtimestamp(
            expiration_timestamp,
            timezone.utc,
        )
        commit_or_rollback(db)
    else:
        expiration_timestamp = int(payment.checkout_session_expires_at.timestamp())

    success_url = settings.stripe_checkout_success_url
    success_url = (
        f"{success_url}{'&' if '?' in success_url else '?'}"
        "session_id={CHECKOUT_SESSION_ID}"
    )
    line_items: list[SessionCreateParamsLineItem] = [
        {
            "price_data": {
                "currency": order.currency.lower(),
                "product_data": {"name": line.product_name_snapshot},
                "unit_amount": int(line.retail_price_gross * Decimal("100")),
            },
            "quantity": line.quantity,
        }
        for line in order_lines
    ]
    session_params: SessionCreateParams = {
        "mode": "payment",
        "line_items": line_items,
        "customer_email": customer_email,
        "client_reference_id": str(order_id),
        "metadata": {
            "order_id": str(order_id),
            "order_number": order_number,
        },
        "success_url": success_url,
        "cancel_url": settings.stripe_checkout_cancel_url,
        "expires_at": expiration_timestamp,
    }

    commit_or_rollback(db)

    try:
        session = get_stripe_client().v1.checkout.sessions.create(
            session_params,
            {"idempotency_key": f"half-moon-checkout-v2-{order_id}"},
        )
    except (StripeError, RuntimeError) as error:
        raise CheckoutUnavailableError from error

    if not session.url:
        raise CheckoutUnavailableError

    payment = db.query(Payment).filter(Payment.id == payment_id).one()
    payment.provider_checkout_session_id = session.id
    payment.checkout_url = session.url
    payment.checkout_session_expires_at = datetime.fromtimestamp(
        session.expires_at,
        timezone.utc,
    )
    commit_or_rollback(db)

    return session.url
