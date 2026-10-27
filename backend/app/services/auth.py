from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.data_protection import (
    create_email_lookup_hmac,
)
from backend.app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from backend.app.db.database import commit_or_rollback, flush_or_rollback
from backend.app.models.customer import Customer
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.staff_user import StaffUser
from backend.app.schemas.auth import (
    CustomerLoginRequest,
    CustomerRegisterRequest,
    StaffLoginRequest,
    StaffTokenResponse,
    TokenResponse,
)
from backend.app.services.customers import (
    get_or_create_customer,
    normalize_email,
    update_customer_profile,
)
from backend.app.services.notifications import enqueue_notification
from backend.app.services.refresh_sessions import (
    RefreshCookieCredentials,
    create_refresh_session,
)
from backend.app.services.verification import create_verification_token


@dataclass(frozen=True)
class LoginTokenBundle:
    response: TokenResponse | StaffTokenResponse
    cookies: RefreshCookieCredentials


def _normalize_email(email: str) -> str:
    return normalize_email(email)


def _get_email_lookup_hmac(email: str) -> str:
    return create_email_lookup_hmac(_normalize_email(email))


def _verify_active_password(
    password: str,
    password_hash: str,
    account_status: str,
) -> None:
    if account_status != "ACTIVE":
        raise ValueError("Account is not active")

    if not verify_password(password, password_hash):
        raise ValueError("Invalid credentials")


def register_customer(
    db: Session,
    request: CustomerRegisterRequest,
) -> Customer:
    normalized_email = normalize_email(str(request.email))
    customer = get_or_create_customer(db, normalized_email)

    existing_account = (
        db.query(CustomerAccount)
        .filter(CustomerAccount.customer_id == customer.id)
        .first()
    )

    if existing_account is not None:
        raise ValueError("Customer already exists")

    update_customer_profile(
        customer,
        first_name=request.first_name,
        last_name=request.last_name,
    )

    customer_account = CustomerAccount(
        customer_id=customer.id,
        password_hash=hash_password(request.password),
    )

    db.add(customer_account)
    flush_or_rollback(db)

    verification_token, verification_expires_at = create_verification_token(
        db,
        customer_account.id,
    )
    enqueue_notification(
        db,
        customer_id=customer.id,
        notification_type="EMAIL_VERIFICATION",
        recipient_email=normalized_email,
        payload={"verification_token": verification_token},
        expires_at=verification_expires_at,
    )

    commit_or_rollback(db)
    db.refresh(customer)

    return customer


def login_customer(
    db: Session,
    request: CustomerLoginRequest,
) -> LoginTokenBundle:
    normalized_email = _normalize_email(str(request.email))
    email_lookup_hmac = _get_email_lookup_hmac(normalized_email)

    customer = (
        db.query(Customer)
        .filter(Customer.email_lookup_hmac == email_lookup_hmac)
        .first()
    )

    if customer is None:
        raise ValueError("Invalid credentials")

    account = (
        db.query(CustomerAccount)
        .filter(CustomerAccount.customer_id == customer.id)
        .first()
    )

    if account is None:
        raise ValueError("Invalid credentials")

    _verify_active_password(
        request.password,
        account.password_hash,
        account.account_status,
    )

    if account.email_verified_at is None:
        raise ValueError("Email address has not been verified")

    account.last_login_at = datetime.now(timezone.utc)
    access_token = create_access_token(str(customer.id))
    cookies = create_refresh_session(db, customer_id=customer.id)
    commit_or_rollback(db)

    return LoginTokenBundle(
        response=TokenResponse(
            access_token=access_token,
            expires_in=settings.access_token_expire_minutes * 60,
        ),
        cookies=cookies,
    )


def login_staff(
    db: Session,
    request: StaffLoginRequest,
) -> LoginTokenBundle:
    normalized_email = _normalize_email(str(request.email))
    email_lookup_hmac = _get_email_lookup_hmac(normalized_email)

    staff_user = (
        db.query(StaffUser)
        .filter(StaffUser.email_lookup_hmac == email_lookup_hmac)
        .first()
    )

    if staff_user is None:
        raise ValueError("Invalid credentials")

    _verify_active_password(
        request.password,
        staff_user.password_hash,
        staff_user.account_status,
    )

    staff_user.last_login_at = datetime.now(timezone.utc)
    access_token = create_access_token(
        str(staff_user.id),
        role=staff_user.role,
    )
    cookies = create_refresh_session(db, staff_user_id=staff_user.id)
    commit_or_rollback(db)

    return LoginTokenBundle(
        response=StaffTokenResponse(
            access_token=access_token,
            expires_in=settings.access_token_expire_minutes * 60,
            role=staff_user.role,
        ),
        cookies=cookies,
    )
