import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from ipaddress import ip_address
from urllib.parse import urlencode
from uuid import UUID

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import JSONResponse, RedirectResponse
from oauthlib.oauth2 import OAuth2Error
from requests.exceptions import RequestException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from stripe import StripeError

from backend.app.core.authentication import (
    get_current_customer,
    get_current_staff,
    require_staff_role,
)
from backend.app.core.config import settings
from backend.app.core.data_protection import decrypt_pii
from backend.app.db.database import SessionLocal, commit_or_rollback, get_db
from backend.app.models.customer import Customer
from backend.app.models.return_record import ReturnRecord
from backend.app.models.staff_user import StaffUser
from backend.app.schemas.auth import (
    CustomerLoginRequest,
    CustomerRegisterRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RefreshTokenResponse,
    StaffLoginRequest,
    StaffTokenResponse,
    TokenResponse,
)
from backend.app.schemas.inventory import InventoryTransferRequest
from backend.app.schemas.orders import (
    CheckoutPaymentStatusResponse,
    CreateOrderRequest,
    CustomerOrderShipmentStatusResponse,
    CustomerOrderSummaryResponse,
    GuestCreateOrderRequest,
    OrderDetailsResponse,
    PendingOrderResponse,
)
from backend.app.schemas.pos import (
    CreatePOSCheckoutRequest,
    POSCashCheckoutResponse,
    POSCheckoutResponse,
    POSClaimResetByOrderNumberRequest,
    POSClaimResetResponse,
    POSDemoCheckoutRequest,
    POSOrderClaimRequest,
    POSOrderClaimResponse,
    POSOrderStatusResponse,
    POSReceiptDeliveryAcknowledgementResponse,
    POSReceiptDeliveryResponse,
)
from backend.app.schemas.returns import (
    CreateReturnRequest,
    CustomerOrderReturnStatusResponse,
    POSReturnProcessedResponse,
)
from backend.app.services.auth import login_customer as login_customer_service
from backend.app.services.auth import login_staff as login_staff_service
from backend.app.services.auth import register_customer as register_customer_service
from backend.app.services.password_reset import (
    InvalidPasswordResetTokenError,
    confirm_customer_password_reset as confirm_customer_password_reset_service,
    request_customer_password_reset as request_customer_password_reset_service,
)
from backend.app.services.audit import record_audit_event
from backend.app.services.catalog import (
    get_product_detail_payload,
    get_product_payload,
    get_product_variants_payload,
    get_variant_stock_payload,
    list_catalog_products,
)
from backend.app.services.checkout import (
    CheckoutUnavailableError,
    create_customer_checkout,
    create_guest_checkout,
    get_checkout_payment_status,
)
from backend.app.services.fulfillment import (
    advance_one_due_mock_shipment,
    get_order_fulfillment_status,
)
from backend.app.services.gmail_oauth import (
    GmailOAuthSetupError,
    create_gmail_oauth_flow,
    save_gmail_credentials,
)
from backend.app.services.notification_worker import process_one_notification
from backend.app.services.order_details import (
    OrderDetailsIntegrityError,
    POSOrderNotClaimedError,
    get_customer_order_details,
    get_staff_pos_order_details,
    list_customer_ecom_orders,
    list_customer_pos_orders,
)
from backend.app.services.payments import (
    StripeLatePaymentRefundRequired,
    process_late_paid_stripe_checkout_event,
    process_paid_stripe_checkout_event,
    process_pos_payment_intent_event,
    process_stripe_refund_updated_event,
    verify_stripe_webhook_event,
)
from backend.app.services.pos import (
    POSClaimConflictError,
    POSClaimExpiredError,
    POSClaimInvalidCodeError,
    POSClaimLockedError,
    POSClaimVerificationRequiredError,
    POSDataIntegrityError,
    POSPaymentUnavailableError,
    POSReceiptGoneError,
    POSReceiptNotReadyError,
    acknowledge_pos_receipt_delivery,
    claim_anonymous_pos_order,
    complete_pos_cash_sale,
    create_pending_pos_order,
    create_pos_card_payment_intent,
    delete_expired_pos_receipt_deliveries,
    get_pos_order_status,
    get_pos_receipt_delivery,
    reset_pos_order_claim_lock,
    reset_pos_order_claim_lock_by_order_number,
)
from backend.app.services.returns import (
    POSReturnOrderNotFoundError,
    ReturnIntegrityError,
    create_return_request,
    list_customer_order_returns,
    process_pos_return_by_customer,
    process_pos_return_by_staff,
    process_return,
)
from backend.app.services.refresh_sessions import (
    InvalidCsrfTokenError,
    InvalidRefreshTokenError,
    TokenReplayBreachError,
    delete_expired_refresh_sessions,
    logout_refresh_session,
    rotate_refresh_session,
)
from backend.app.services.staff_operations import (
    transfer_inventory_units,
)
from backend.app.services.verification import verify_customer_email

NOTIFICATION_POLL_INTERVAL_SECONDS = 5
FULFILLMENT_POLL_INTERVAL_SECONDS = 1
WORKER_RESTART_INITIAL_BACKOFF_SECONDS = 1
WORKER_RESTART_MAX_BACKOFF_SECONDS = 60
REFRESH_SESSION_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60
REFRESH_COOKIE_NAME = "half_moon_refresh"
CSRF_COOKIE_NAME = "half_moon_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
AUTH_COOKIE_PATH = "/api/v1/auth"
CSRF_COOKIE_PATH = "/"
logger = logging.getLogger(__name__)


def _set_auth_cookies(
    response: Response,
    refresh_token: str,
    csrf_token: str,
    expires_at: datetime,
) -> None:
    max_age = max(
        0,
        int((expires_at - datetime.now(timezone.utc)).total_seconds()),
    )
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=max_age,
        path=AUTH_COOKIE_PATH,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=max_age,
        path=CSRF_COOKIE_PATH,
        secure=settings.refresh_cookie_secure,
        httponly=False,
        samesite="lax",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=AUTH_COOKIE_PATH,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path=CSRF_COOKIE_PATH,
        secure=settings.refresh_cookie_secure,
        httponly=False,
        samesite="lax",
    )


def _process_one_notification_with_session() -> bool:
    with SessionLocal() as db:
        return process_one_notification(db)


def _process_one_due_shipment_with_session() -> bool:
    with SessionLocal() as db:
        return advance_one_due_mock_shipment(db)


POS_RECEIPT_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60


def _cleanup_pos_receipts_with_session() -> int:
    with SessionLocal() as db:
        return delete_expired_pos_receipt_deliveries(db)


def _cleanup_refresh_sessions_with_session() -> int:
    with SessionLocal() as db:
        return delete_expired_refresh_sessions(db)


async def _run_refresh_session_cleanup_worker(
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(_cleanup_refresh_sessions_with_session)
        except SQLAlchemyError:
            logger.exception("Refresh-session cleanup database operation failed")

        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=REFRESH_SESSION_CLEANUP_INTERVAL_SECONDS,
            )
        except TimeoutError:
            pass


async def _run_pos_receipt_cleanup_worker(
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(_cleanup_pos_receipts_with_session)
        except SQLAlchemyError:
            logger.exception("POS receipt cleanup database operation failed")
        except POSDataIntegrityError:
            logger.exception("POS receipt cleanup found inconsistent data")

        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=POS_RECEIPT_CLEANUP_INTERVAL_SECONDS,
            )
        except TimeoutError:
            pass


async def _wait_for_worker_stop(
    stop_event: asyncio.Event,
    timeout_seconds: float,
) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout_seconds)
    except TimeoutError:
        pass


async def _run_notification_worker(stop_event: asyncio.Event) -> None:
    database_error_backoff = WORKER_RESTART_INITIAL_BACKOFF_SECONDS

    while not stop_event.is_set():
        try:
            processed = await asyncio.to_thread(_process_one_notification_with_session)
        except SQLAlchemyError:
            logger.exception("Notification worker database operation failed")
            await _wait_for_worker_stop(stop_event, database_error_backoff)
            database_error_backoff = min(
                database_error_backoff * 2,
                WORKER_RESTART_MAX_BACKOFF_SECONDS,
            )
            continue

        database_error_backoff = WORKER_RESTART_INITIAL_BACKOFF_SECONDS
        if not processed:
            await _wait_for_worker_stop(
                stop_event,
                NOTIFICATION_POLL_INTERVAL_SECONDS,
            )


async def _run_fulfillment_worker(stop_event: asyncio.Event) -> None:
    database_error_backoff = WORKER_RESTART_INITIAL_BACKOFF_SECONDS

    while not stop_event.is_set():
        try:
            await asyncio.to_thread(_process_one_due_shipment_with_session)
        except SQLAlchemyError:
            logger.exception("Fulfillment worker database operation failed")
            await _wait_for_worker_stop(stop_event, database_error_backoff)
            database_error_backoff = min(
                database_error_backoff * 2,
                WORKER_RESTART_MAX_BACKOFF_SECONDS,
            )
            continue

        database_error_backoff = WORKER_RESTART_INITIAL_BACKOFF_SECONDS
        await _wait_for_worker_stop(
            stop_event,
            FULFILLMENT_POLL_INTERVAL_SECONDS,
        )


async def _supervise_background_worker(
    worker: Callable[[asyncio.Event], Awaitable[None]],
    stop_event: asyncio.Event,
    worker_name: str,
) -> None:
    restart_backoff = WORKER_RESTART_INITIAL_BACKOFF_SECONDS

    while not stop_event.is_set():
        worker_task = asyncio.create_task(worker(stop_event))
        await asyncio.wait(
            (worker_task,),
            return_when=asyncio.FIRST_COMPLETED,
        )

        if stop_event.is_set():
            if not worker_task.cancelled():
                worker_error = worker_task.exception()
                if worker_error is not None:
                    logger.error(
                        "%s worker failed during shutdown",
                        worker_name,
                        exc_info=(
                            type(worker_error),
                            worker_error,
                            worker_error.__traceback__,
                        ),
                    )
            return

        if worker_task.cancelled():
            logger.error("%s worker was cancelled unexpectedly", worker_name)
        else:
            worker_error = worker_task.exception()
            if worker_error is None:
                logger.error("%s worker stopped unexpectedly", worker_name)
            else:
                logger.error(
                    "%s worker stopped unexpectedly",
                    worker_name,
                    exc_info=(
                        type(worker_error),
                        worker_error,
                        worker_error.__traceback__,
                    ),
                )

        await _wait_for_worker_stop(stop_event, restart_backoff)
        restart_backoff = min(
            restart_backoff * 2,
            WORKER_RESTART_MAX_BACKOFF_SECONDS,
        )


def _log_background_worker_failure(
    task: asyncio.Task[None],
) -> None:
    if task.cancelled():
        return

    error = task.exception()
    if error is not None:
        logger.error(
            "Background worker stopped unexpectedly",
            exc_info=(type(error), error, error.__traceback__),
        )


@asynccontextmanager
async def notification_worker_lifespan(
    _app: FastAPI,
) -> AsyncIterator[None]:
    stop_event = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    if settings.pos_receipt_cleanup_worker_enabled:
        receipt_cleanup_task = asyncio.create_task(
            _run_pos_receipt_cleanup_worker(stop_event)
        )
        receipt_cleanup_task.add_done_callback(_log_background_worker_failure)
        tasks.append(receipt_cleanup_task)

    if settings.refresh_session_cleanup_worker_enabled:
        refresh_cleanup_task = asyncio.create_task(
            _run_refresh_session_cleanup_worker(stop_event)
        )
        refresh_cleanup_task.add_done_callback(_log_background_worker_failure)
        tasks.append(refresh_cleanup_task)

    if settings.gmail_delivery_worker_enabled:
        notification_task = asyncio.create_task(
            _supervise_background_worker(
                _run_notification_worker,
                stop_event,
                "Notification",
            )
        )
        fulfillment_task = asyncio.create_task(
            _supervise_background_worker(
                _run_fulfillment_worker,
                stop_event,
                "Fulfillment",
            )
        )

        notification_task.add_done_callback(_log_background_worker_failure)
        fulfillment_task.add_done_callback(_log_background_worker_failure)
        tasks.extend([notification_task, fulfillment_task])

    try:
        yield
    finally:
        stop_event.set()
        if tasks:
            await asyncio.gather(*tasks)


app = FastAPI(
    title="Half Moon API",
    version="0.1.0",
    description="Backend for the Half Moon storefront and POS",
    lifespan=notification_worker_lifespan,
)


@app.exception_handler(SQLAlchemyError)
async def handle_database_operation_error(
    _request: Request,
    error: SQLAlchemyError,
) -> JSONResponse:
    logger.error(
        "Database operation failed",
        exc_info=(type(error), error, error.__traceback__),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "A database operation failed."},
    )


api_router = APIRouter(prefix="/api/v1")


@app.get("/health")
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "half-moon-api",
    }


@api_router.get("/products")
def list_products(db: Session = Depends(get_db)):  # noqa: B008
    return list_catalog_products(db)


@api_router.get("/products/{product_id}")
def get_product(product_id: UUID, db: Session = Depends(get_db)):  # noqa: B008
    try:
        return get_product_payload(db, product_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@api_router.get("/products/{product_id}/variants")
def get_product_variants(product_id: UUID, db: Session = Depends(get_db)):  # noqa: B008
    try:
        return get_product_variants_payload(db, product_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@api_router.get("/products/{product_id}/detail")
def get_product_detail(product_id: UUID, db: Session = Depends(get_db)):  # noqa: B008
    try:
        return get_product_detail_payload(db, product_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@api_router.get("/products/{product_id}/variants/{variant_id}/stock")
def get_variant_stock(
    product_id: UUID,
    variant_id: UUID,
    db: Session = Depends(get_db),  # noqa: B008
):
    try:
        return get_variant_stock_payload(db, product_id, variant_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@api_router.post("/auth/register", status_code=201)
def register_customer_route(
    request: CustomerRegisterRequest,
    db: Session = Depends(get_db),  # noqa: B008
):
    try:
        customer = register_customer_service(db, request)
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    return {
        "id": str(customer.id),
        "message": "Customer registered successfully",
    }


@api_router.post("/auth/login", response_model=TokenResponse)
def login_customer_route(
    request: CustomerLoginRequest,
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
):
    try:
        login_result = login_customer_service(db, request)
    except ValueError as error:
        raise HTTPException(
            status_code=401,
            detail=str(error),
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    _set_auth_cookies(
        response,
        login_result.cookies.refresh_token,
        login_result.cookies.csrf_token,
        login_result.cookies.expires_at,
    )
    return login_result.response


@api_router.post("/auth/password-reset/request", status_code=202)
def request_customer_password_reset_route(
    request: PasswordResetRequest,
    db: Session = Depends(get_db),  # noqa: B008
):
    request_customer_password_reset_service(
        db,
        email=str(request.email),
    )
    return {
        "message": (
            "If an eligible customer account exists, password reset "
            "instructions will be sent."
        )
    }


@api_router.post("/auth/password-reset/confirm")
def confirm_customer_password_reset_route(
    request: PasswordResetConfirmRequest,
    db: Session = Depends(get_db),  # noqa: B008
):
    try:
        confirm_customer_password_reset_service(
            db,
            reset_token=request.token,
            new_password=request.new_password,
        )
    except InvalidPasswordResetTokenError as error:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired password reset token",
        ) from error

    return {"message": "Password reset successfully. Please sign in again."}


@api_router.post(
    "/auth/refresh",
    response_model=RefreshTokenResponse,
    response_model_exclude_none=True,
)
def refresh_auth_route(
    response: Response,
    refresh_cookie: str | None = Cookie(
        default=None,
        alias=REFRESH_COOKIE_NAME,
    ),  # noqa: B008
    csrf_cookie: str | None = Cookie(
        default=None,
        alias=CSRF_COOKIE_NAME,
    ),  # noqa: B008
    csrf_header: str | None = Header(
        default=None,
        alias=CSRF_HEADER_NAME,
    ),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    try:
        refreshed = rotate_refresh_session(
            db,
            refresh_token=refresh_cookie,
            csrf_cookie=csrf_cookie,
            csrf_header=csrf_header,
        )
    except TokenReplayBreachError as error:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication credentials",
        ) from error
    except InvalidRefreshTokenError as error:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication credentials",
        ) from error
    except InvalidCsrfTokenError as error:
        raise HTTPException(
            status_code=403,
            detail="Invalid CSRF token",
        ) from error

    _set_auth_cookies(
        response,
        refreshed.cookies.refresh_token,
        refreshed.cookies.csrf_token,
        refreshed.cookies.expires_at,
    )
    return RefreshTokenResponse(
        access_token=refreshed.response.access_token,
        expires_in=refreshed.response.expires_in,
        role=refreshed.role,
    )


@api_router.post("/auth/logout", status_code=204)
def logout_auth_route(
    response: Response,
    refresh_cookie: str | None = Cookie(
        default=None,
        alias=REFRESH_COOKIE_NAME,
    ),  # noqa: B008
    csrf_cookie: str | None = Cookie(
        default=None,
        alias=CSRF_COOKIE_NAME,
    ),  # noqa: B008
    csrf_header: str | None = Header(
        default=None,
        alias=CSRF_HEADER_NAME,
    ),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    if refresh_cookie:
        try:
            logout_refresh_session(
                db,
                refresh_token=refresh_cookie,
                csrf_cookie=csrf_cookie,
                csrf_header=csrf_header,
            )
        except InvalidCsrfTokenError as error:
            raise HTTPException(
                status_code=403,
                detail="Invalid CSRF token",
            ) from error

    _clear_auth_cookies(response)
    response.status_code = 204
    return response


@api_router.get("/auth/me")
def get_current_customer_profile(
    current_customer: Customer = Depends(get_current_customer),  # noqa: B008
):
    return {
        "id": str(current_customer.id),
        "email": decrypt_pii(current_customer.email_ciphertext),
        "first_name": (
            decrypt_pii(current_customer.first_name_ciphertext)
            if current_customer.first_name_ciphertext is not None
            else None
        ),
        "last_name": (
            decrypt_pii(current_customer.last_name_ciphertext)
            if current_customer.last_name_ciphertext is not None
            else None
        ),
    }


def _require_local_gmail_oauth_setup(request: Request) -> None:
    if not settings.gmail_oauth_setup_enabled:
        raise HTTPException(status_code=404, detail="Not found")

    if request.client is None:
        raise HTTPException(
            status_code=403,
            detail="Gmail OAuth setup is restricted to this computer",
        )

    try:
        is_loopback = ip_address(request.client.host).is_loopback
    except ValueError:
        is_loopback = False

    if not is_loopback:
        raise HTTPException(
            status_code=403,
            detail="Gmail OAuth setup is restricted to this computer",
        )


@api_router.get("/auth/gmail/start", include_in_schema=False)
def start_gmail_oauth_setup(request: Request):
    _require_local_gmail_oauth_setup(request)

    try:
        flow = create_gmail_oauth_flow()
    except GmailOAuthSetupError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )

    code_verifier = flow.code_verifier
    if code_verifier is None:
        raise HTTPException(
            status_code=500,
            detail="Could not generate OAuth PKCE verifier",
        )

    response = RedirectResponse(authorization_url, status_code=302)
    response.set_cookie(
        key="gmail_oauth_state",
        value=state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/api/v1/auth/gmail",
    )
    response.set_cookie(
        key="gmail_oauth_code_verifier",
        value=code_verifier,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/api/v1/auth/gmail",
    )
    return response


@api_router.get("/auth/gmail/callback", include_in_schema=False)
def complete_gmail_oauth_setup(
    request: Request,
    response: Response,
) -> dict[str, str]:
    _require_local_gmail_oauth_setup(request)

    if request.query_params.get("error"):
        raise HTTPException(
            status_code=400,
            detail="Google authorization was cancelled or rejected",
        )

    state = request.cookies.get("gmail_oauth_state")
    code_verifier = request.cookies.get("gmail_oauth_code_verifier")
    if not state or not code_verifier:
        raise HTTPException(
            status_code=400,
            detail="Gmail OAuth state or PKCE verifier is missing; restart the setup",
        )

    try:
        flow = create_gmail_oauth_flow(
            state=state,
            code_verifier=code_verifier,
        )
    except GmailOAuthSetupError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    try:
        flow.fetch_token(authorization_response=str(request.url))
    except OAuth2Error as error:
        error_code = error.error
        if (
            not isinstance(error_code, str)
            or not error_code.isascii()
            or len(error_code) > 64
            or not error_code.replace("_", "").isalnum()
        ):
            error_code = type(error).__name__

        raise HTTPException(
            status_code=400,
            detail=f"Google OAuth failed ({error_code}); restart the setup",
        ) from error
    except RequestException as error:
        raise HTTPException(
            status_code=502,
            detail="Could not exchange the OAuth code with Google",
        ) from error

    try:
        credentials_path = save_gmail_credentials(flow)
    except GmailOAuthSetupError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail="Could not securely save Gmail credentials",
        ) from error

    response.delete_cookie(
        key="gmail_oauth_state",
        path="/api/v1/auth/gmail",
        httponly=True,
        samesite="lax",
        secure=False,
    )
    response.delete_cookie(
        key="gmail_oauth_code_verifier",
        path="/api/v1/auth/gmail",
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return {
        "authorized": "true",
        "message": "Gmail credentials saved. Disable local OAuth setup now.",
        "credentials_file": str(credentials_path),
    }


@api_router.post(
    "/orders",
    response_model=PendingOrderResponse,
    status_code=201,
)
def create_order_route(
    request: CreateOrderRequest,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=16,
        max_length=255,
    ),
    current_customer: Customer = Depends(get_current_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    try:
        return create_customer_checkout(
            db,
            current_customer.id,
            current_customer.email_ciphertext,
            request,
            idempotency_key,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error
    except CheckoutUnavailableError as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "Checkout could not be started. Retry with the same Idempotency-Key."
            ),
        ) from error


@api_router.post(
    "/orders/guest",
    response_model=PendingOrderResponse,
    status_code=201,
)
def create_guest_order_route(
    request: GuestCreateOrderRequest,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=16,
        max_length=255,
    ),
    db: Session = Depends(get_db),  # noqa: B008
):
    try:
        return create_guest_checkout(db, request, idempotency_key)
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error
    except CheckoutUnavailableError as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "Checkout could not be started. Retry with the same Idempotency-Key."
            ),
        ) from error


@api_router.get("/checkout/success")
def checkout_success_route(
    session_id: str | None = Query(default=None, min_length=1, max_length=255),
) -> RedirectResponse:
    frontend_url = (
        f"{settings.frontend_public_url.rstrip('/')}/checkout/success"
    )
    if session_id is not None:
        frontend_url = f"{frontend_url}?{urlencode({'session_id': session_id})}"

    return RedirectResponse(url=frontend_url, status_code=303)


@api_router.get(
    "/checkout/status",
    response_model=CheckoutPaymentStatusResponse,
)
def checkout_payment_status_route(
    session_id: str = Query(min_length=1, max_length=255),
    db: Session = Depends(get_db),  # noqa: B008
) -> CheckoutPaymentStatusResponse:
    try:
        return get_checkout_payment_status(db, session_id)
    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail="Checkout status was not found",
        ) from error


@api_router.get("/checkout/cancelled")
def checkout_cancelled_route() -> RedirectResponse:
    frontend_url = (
        f"{settings.frontend_public_url.rstrip('/')}/checkout/cancelled"
    )
    return RedirectResponse(url=frontend_url, status_code=303)


@api_router.post("/webhooks/stripe")
async def stripe_webhook_route(
    request: Request,
    stripe_signature: str | None = Header(
        default=None,
        alias="Stripe-Signature",
    ),
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, bool]:
    if not stripe_signature:
        raise HTTPException(
            status_code=400,
            detail="Missing Stripe signature",
        )

    try:
        event = verify_stripe_webhook_event(
            await request.body(),
            stripe_signature,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail="Invalid Stripe webhook signature or payload",
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail="Stripe webhook is not configured",
        ) from error

    pos_payment_intent_event_types = {
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "payment_intent.canceled",
    }
    if event.type in pos_payment_intent_event_types:
        payment_intent = event.data.object
        metadata = (
            payment_intent.metadata.to_dict()
            if payment_intent.metadata is not None
            else {}
        )
        if metadata.get("channel") != "POS":
            return {"received": True}

        try:
            await run_in_threadpool(process_pos_payment_intent_event, db, event)
        except (ValueError, RuntimeError, StripeError) as error:
            await run_in_threadpool(db.rollback)
            raise HTTPException(
                status_code=500,
                detail="Stripe POS payment event could not be processed",
            ) from error
        except SQLAlchemyError:
            await run_in_threadpool(db.rollback)
            raise
        return {"received": True}

    handled_event_types = {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "refund.updated",
    }
    if event.type not in handled_event_types:
        return {"received": True}

    if event.type == "refund.updated":
        try:
            await run_in_threadpool(process_stripe_refund_updated_event, db, event)
        except (ValueError, RuntimeError, StripeError) as error:
            await run_in_threadpool(db.rollback)
            raise HTTPException(
                status_code=500,
                detail="Stripe refund event could not be processed",
            ) from error
        except SQLAlchemyError:
            await run_in_threadpool(db.rollback)
            raise
        return {"received": True}

    checkout_session = event.data.object
    if (
        event.type == "checkout.session.completed"
        and checkout_session.payment_status != "paid"
    ):
        return {"received": True}

    try:
        await run_in_threadpool(process_paid_stripe_checkout_event, db, event)
    except StripeLatePaymentRefundRequired:
        await run_in_threadpool(db.rollback)
        try:
            await run_in_threadpool(
                process_late_paid_stripe_checkout_event,
                db,
                event,
            )
        except (ValueError, RuntimeError, StripeError) as error:
            await run_in_threadpool(db.rollback)
            raise HTTPException(
                status_code=500,
                detail="Stripe payment refund could not be processed",
            ) from error
        except SQLAlchemyError:
            await run_in_threadpool(db.rollback)
            raise
    except (ValueError, RuntimeError, StripeError) as error:
        await run_in_threadpool(db.rollback)
        raise HTTPException(
            status_code=500,
            detail="Stripe payment event could not be processed",
        ) from error
    except SQLAlchemyError:
        await run_in_threadpool(db.rollback)
        raise

    return {"received": True}


@api_router.post(
    "/orders/pos-claim",
    response_model=POSOrderClaimResponse,
)
def claim_pos_order_route(
    request: POSOrderClaimRequest,
    current_customer: Customer = Depends(get_current_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> POSOrderClaimResponse:
    try:
        return claim_anonymous_pos_order(
            db,
            current_customer.id,
            request,
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except POSClaimVerificationRequiredError as error:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except POSClaimInvalidCodeError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except POSClaimLockedError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except POSClaimExpiredError as error:
        db.rollback()
        raise HTTPException(status_code=410, detail=str(error)) from error
    except POSClaimConflictError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except POSDataIntegrityError as error:
        db.rollback()
        logger.exception("POS claim data is inconsistent")
        raise HTTPException(
            status_code=500,
            detail="POS order claim could not be completed",
        ) from error
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("POS claim database operation failed")
        raise HTTPException(
            status_code=500,
            detail="POS order claim could not be completed",
        ) from error


@api_router.post(
    "/orders/pos-demo",
    response_model=POSCashCheckoutResponse,
    status_code=201,
)
def create_customer_pos_demo_checkout_route(
    request: POSDemoCheckoutRequest,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=16,
        max_length=255,
    ),
    db: Session = Depends(get_db),  # noqa: B008
) -> POSCashCheckoutResponse:
    checkout_request = CreatePOSCheckoutRequest(
        lines=request.lines,
        payment_method="CASH",
    )
    try:
        order = create_pending_pos_order(
            db,
            checkout_request,
            staff_user_id=None,
            idempotency_key=idempotency_key,
        )
        completed_order = complete_pos_cash_sale(
            db,
            order.id,
            staff_user_id=None,
        )
        return POSCashCheckoutResponse(
            order_id=completed_order.id,
            order_number=completed_order.order_number,
            payment_method="CASH",
            order_status="COMPLETED",
            payment_status="PAID",
            total_gross=completed_order.total_gross,
            currency=completed_order.currency,
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Customer POS demo checkout failed")
        raise HTTPException(
            status_code=500,
            detail="POS demo checkout could not be completed",
        ) from error


@api_router.get(
    "/orders/pos-demo/{order_id}/receipt",
    response_model=POSReceiptDeliveryResponse,
)
def get_customer_pos_demo_receipt_route(
    order_id: UUID,
    db: Session = Depends(get_db),  # noqa: B008
) -> POSReceiptDeliveryResponse:
    try:
        return get_pos_receipt_delivery(db, order_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except POSReceiptNotReadyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except POSReceiptGoneError as error:
        raise HTTPException(status_code=410, detail=str(error)) from error
    except POSDataIntegrityError as error:
        db.rollback()
        logger.exception("Customer POS demo receipt data is inconsistent")
        raise HTTPException(
            status_code=500,
            detail="POS receipt could not be delivered",
        ) from error
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Customer POS demo receipt lookup failed")
        raise HTTPException(
            status_code=500,
            detail="POS receipt could not be delivered",
        ) from error


@api_router.post(
    "/orders/pos-demo/{order_id}/receipt/acknowledgement",
    response_model=POSReceiptDeliveryAcknowledgementResponse,
)
def acknowledge_customer_pos_demo_receipt_route(
    order_id: UUID,
    db: Session = Depends(get_db),  # noqa: B008
) -> POSReceiptDeliveryAcknowledgementResponse:
    try:
        return acknowledge_pos_receipt_delivery(db, order_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except POSReceiptNotReadyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except POSReceiptGoneError as error:
        raise HTTPException(status_code=410, detail=str(error)) from error
    except POSDataIntegrityError as error:
        db.rollback()
        logger.exception("Customer POS demo receipt acknowledgement is inconsistent")
        raise HTTPException(
            status_code=500,
            detail="POS receipt acknowledgement could not be recorded",
        ) from error
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Customer POS demo receipt acknowledgement failed")
        raise HTTPException(
            status_code=500,
            detail="POS receipt acknowledgement could not be recorded",
        ) from error


@api_router.get(
    "/orders/{order_id}/status",
    response_model=CustomerOrderShipmentStatusResponse,
)
def get_order_status(
    order_id: UUID,
    current_customer: Customer = Depends(get_current_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    try:
        return get_order_fulfillment_status(
            db,
            order_id,
            current_customer.id,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error


@api_router.get(
    "/orders",
    response_model=list[CustomerOrderSummaryResponse],
)
def list_customer_ecom_orders_route(
    current_customer: Customer = Depends(get_current_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[CustomerOrderSummaryResponse]:
    return list_customer_ecom_orders(db, current_customer.id)


@api_router.get(
    "/orders/pos",
    response_model=list[CustomerOrderSummaryResponse],
)
def list_customer_pos_orders_route(
    current_customer: Customer = Depends(get_current_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[CustomerOrderSummaryResponse]:
    return list_customer_pos_orders(db, current_customer.id)


@api_router.get(
    "/orders/{order_id}",
    response_model=OrderDetailsResponse,
)
def get_customer_order_details_route(
    order_id: UUID,
    current_customer: Customer = Depends(get_current_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> OrderDetailsResponse:
    try:
        return get_customer_order_details(
            db,
            order_id,
            current_customer.id,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except OrderDetailsIntegrityError as error:
        logger.exception("Customer order details are inconsistent")
        raise HTTPException(
            status_code=500,
            detail="Order details could not be loaded",
        ) from error


@api_router.get(
    "/orders/{order_id}/returns",
    response_model=list[CustomerOrderReturnStatusResponse],
)
def list_customer_order_returns_route(
    order_id: UUID,
    current_customer: Customer = Depends(get_current_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[CustomerOrderReturnStatusResponse]:
    try:
        return list_customer_order_returns(
            db,
            current_customer.id,
            order_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@api_router.post("/orders/{order_id}/returns", status_code=201)
def create_return_route(
    order_id: UUID,
    request: CreateReturnRequest,
    current_customer: Customer = Depends(get_current_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    try:
        return create_return_request(
            db,
            current_customer.id,
            order_id,
            request,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error


@api_router.post(
    "/orders/pos/{order_id}/returns",
    response_model=POSReturnProcessedResponse,
)
def create_customer_pos_return_route(
    order_id: UUID,
    request: CreateReturnRequest,
    current_customer: Customer = Depends(get_current_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> POSReturnProcessedResponse:
    try:
        return process_pos_return_by_customer(
            db,
            current_customer.id,
            order_id,
            request,
        )
    except POSReturnOrderNotFoundError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ReturnIntegrityError as error:
        db.rollback()
        logger.exception("Customer POS return data is inconsistent")
        record_audit_event(
            db,
            actor_type="SYSTEM",
            action_code="RETURN_PROCESSING_INTEGRITY_FAILURE",
            outcome="FAILED",
            target_type="ORDER",
            target_id=order_id,
            error_code="RETURN_INTEGRITY_FAILURE",
        )
        commit_or_rollback(db)
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Customer POS return operation failed")
        raise HTTPException(
            status_code=500,
            detail="POS return could not be completed",
        ) from error


@api_router.post("/returns/{return_id}/process")
def process_return_route(
    return_id: UUID,
    current_customer: Customer = Depends(get_current_customer),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    try:
        return process_return(
            db,
            current_customer.id,
            return_id,
        )
    except ReturnIntegrityError as error:
        db.rollback()
        db.query(ReturnRecord.id).filter(
            ReturnRecord.id == return_id
        ).with_for_update().one_or_none()
        record_audit_event(
            db,
            actor_type="CUSTOMER",
            customer_id=current_customer.id,
            action_code="RETURN_PROCESSING_INTEGRITY_FAILURE",
            outcome="FAILED",
            target_type="RETURN",
            target_id=return_id,
            error_code="RETURN_INTEGRITY_FAILURE",
        )
        commit_or_rollback(db)
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error


@api_router.post("/auth/staff/login", response_model=StaffTokenResponse)
def login_staff_route(
    request: StaffLoginRequest,
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
):
    try:
        login_result = login_staff_service(db, request)
    except ValueError as error:
        raise HTTPException(
            status_code=401,
            detail=str(error),
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    _set_auth_cookies(
        response,
        login_result.cookies.refresh_token,
        login_result.cookies.csrf_token,
        login_result.cookies.expires_at,
    )
    return login_result.response


@api_router.get("/staff/me")
def get_current_staff_profile(
    current_staff: StaffUser = Depends(get_current_staff),  # noqa: B008
):
    return {
        "id": str(current_staff.id),
        "email": decrypt_pii(current_staff.email_ciphertext),
        "first_name": decrypt_pii(current_staff.first_name_ciphertext),
        "last_name": decrypt_pii(current_staff.last_name_ciphertext),
        "role": current_staff.role,
    }


@api_router.get(
    "/staff/pos/orders/lookup",
    response_model=OrderDetailsResponse,
)
def get_staff_pos_order_details_route(
    order_number: str = Query(..., min_length=1, max_length=30),  # noqa: B008
    current_staff: StaffUser = Depends(get_current_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> OrderDetailsResponse:
    try:
        return get_staff_pos_order_details(db, order_number)
    except POSOrderNotClaimedError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except OrderDetailsIntegrityError as error:
        logger.exception("Staff POS order details are inconsistent")
        raise HTTPException(
            status_code=500,
            detail="POS order details could not be loaded",
        ) from error


@api_router.post(
    "/staff/pos/checkout",
    response_model=POSCheckoutResponse,
    status_code=201,
)
def create_pos_checkout_route(
    request: CreatePOSCheckoutRequest,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=16,
        max_length=255,
    ),
    current_staff: StaffUser = Depends(get_current_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> POSCheckoutResponse:
    try:
        order = create_pending_pos_order(
            db,
            request,
            current_staff.id,
            idempotency_key,
        )

        if request.payment_method == "CASH":
            completed_order = complete_pos_cash_sale(
                db,
                order.id,
                current_staff.id,
            )
            return POSCashCheckoutResponse(
                order_id=completed_order.id,
                order_number=completed_order.order_number,
                payment_method="CASH",
                order_status="COMPLETED",
                payment_status="PAID",
                total_gross=completed_order.total_gross,
                currency=completed_order.currency,
            )

        return create_pos_card_payment_intent(db, order.id)
    except POSPaymentUnavailableError as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "Card payment could not be started. "
                "Retry with the same Idempotency-Key."
            ),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error


@api_router.get(
    "/staff/pos/orders/{order_id}/status",
    response_model=POSOrderStatusResponse,
)
def get_staff_pos_order_status_route(
    order_id: UUID,
    current_staff: StaffUser = Depends(get_current_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> POSOrderStatusResponse:
    try:
        return get_pos_order_status(db, order_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except POSDataIntegrityError as error:
        db.rollback()
        logger.exception("POS status data is inconsistent")
        raise HTTPException(
            status_code=500,
            detail="POS order status could not be loaded",
        ) from error
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("POS status database operation failed")
        raise HTTPException(
            status_code=500,
            detail="POS order status could not be loaded",
        ) from error


@api_router.post(
    "/staff/pos/orders/{order_id}/returns",
    response_model=POSReturnProcessedResponse,
)
def process_staff_pos_return_route(
    order_id: UUID,
    request: CreateReturnRequest,
    current_staff: StaffUser = Depends(get_current_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> POSReturnProcessedResponse:
    try:
        return process_pos_return_by_staff(
            db,
            order_id,
            request,
            current_staff.id,
        )
    except POSReturnOrderNotFoundError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ReturnIntegrityError as error:
        db.rollback()
        logger.exception("Staff POS return data is inconsistent")
        record_audit_event(
            db,
            actor_type="STAFF",
            staff_user_id=current_staff.id,
            action_code="RETURN_PROCESSING_INTEGRITY_FAILURE",
            outcome="FAILED",
            target_type="ORDER",
            target_id=order_id,
            error_code="RETURN_INTEGRITY_FAILURE",
        )
        commit_or_rollback(db)
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Staff POS return database operation failed")
        raise HTTPException(
            status_code=500,
            detail="POS return could not be completed",
        ) from error


@api_router.get(
    "/staff/pos/orders/{order_id}/receipt",
    response_model=POSReceiptDeliveryResponse,
)
def get_staff_pos_receipt_route(
    order_id: UUID,
    current_staff: StaffUser = Depends(get_current_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> POSReceiptDeliveryResponse:
    try:
        return get_pos_receipt_delivery(db, order_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except POSReceiptNotReadyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except POSReceiptGoneError as error:
        raise HTTPException(status_code=410, detail=str(error)) from error
    except POSDataIntegrityError as error:
        db.rollback()
        logger.exception("POS receipt data is inconsistent")
        raise HTTPException(
            status_code=500,
            detail="POS receipt could not be delivered",
        ) from error
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("POS receipt database operation failed")
        raise HTTPException(
            status_code=500,
            detail="POS receipt could not be delivered",
        ) from error


@api_router.post(
    "/staff/pos/orders/{order_id}/receipt/acknowledgement",
    response_model=POSReceiptDeliveryAcknowledgementResponse,
)
def acknowledge_staff_pos_receipt_route(
    order_id: UUID,
    current_staff: StaffUser = Depends(get_current_staff),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> POSReceiptDeliveryAcknowledgementResponse:
    try:
        return acknowledge_pos_receipt_delivery(db, order_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except POSReceiptNotReadyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except POSReceiptGoneError as error:
        raise HTTPException(status_code=410, detail=str(error)) from error
    except POSDataIntegrityError as error:
        db.rollback()
        logger.exception("POS receipt acknowledgement data is inconsistent")
        raise HTTPException(
            status_code=500,
            detail="POS receipt acknowledgement could not be recorded",
        ) from error
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("POS receipt acknowledgement database operation failed")
        raise HTTPException(
            status_code=500,
            detail="POS receipt acknowledgement could not be recorded",
        ) from error


@api_router.post(
    "/staff/pos/orders/{order_id}/claim/reset",
    response_model=POSClaimResetResponse,
)
def reset_staff_pos_order_claim_route(
    order_id: UUID,
    current_staff: StaffUser = Depends(require_staff_role("STORE_MANAGER")),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> POSClaimResetResponse:
    try:
        return reset_pos_order_claim_lock(
            db,
            order_id,
            current_staff.id,
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except POSClaimExpiredError as error:
        db.rollback()
        raise HTTPException(status_code=410, detail=str(error)) from error
    except POSClaimConflictError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except POSDataIntegrityError as error:
        db.rollback()
        logger.exception("POS claim reset data is inconsistent")
        raise HTTPException(
            status_code=500,
            detail="POS claim lock could not be reset",
        ) from error
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("POS claim reset database operation failed")
        raise HTTPException(
            status_code=500,
            detail="POS claim lock could not be reset",
        ) from error


@api_router.post(
    "/staff/pos/orders/claim/reset",
    response_model=POSClaimResetResponse,
)
def reset_staff_pos_order_claim_by_number_route(
    request: POSClaimResetByOrderNumberRequest,
    current_staff: StaffUser = Depends(require_staff_role("STORE_MANAGER")),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> POSClaimResetResponse:
    try:
        return reset_pos_order_claim_lock_by_order_number(
            db,
            request.order_number,
            current_staff.id,
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except POSClaimExpiredError as error:
        db.rollback()
        raise HTTPException(status_code=410, detail=str(error)) from error
    except POSClaimConflictError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except POSDataIntegrityError as error:
        db.rollback()
        logger.exception("POS claim reset data is inconsistent")
        raise HTTPException(
            status_code=500,
            detail="POS claim lock could not be reset",
        ) from error
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("POS claim reset database operation failed")
        raise HTTPException(
            status_code=500,
            detail="POS claim lock could not be reset",
        ) from error


@api_router.get("/staff/manager-check")
def manager_check(
    current_staff: StaffUser = Depends(require_staff_role("STORE_MANAGER")),  # noqa: B008
):
    return {
        "authorized": True,
        "role": current_staff.role,
        "message": "Manager access granted",
    }


@api_router.get("/auth/verify-email")
def verify_email(
    token: str,
    db: Session = Depends(get_db),  # noqa: B008
):
    try:
        verify_customer_email(db, token)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    return {
        "verified": True,
        "message": "Email verified successfully",
    }


@api_router.post("/staff/inventory/transfer")
def transfer_staff_inventory(
    request: InventoryTransferRequest,
    current_staff: StaffUser = Depends(require_staff_role("STORE_MANAGER")),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    try:
        return transfer_inventory_units(
            db,
            request,
            current_staff.id,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error


app.include_router(api_router)
