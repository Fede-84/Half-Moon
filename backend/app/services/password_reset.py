from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.app.core.data_protection import create_email_lookup_hmac
from backend.app.core.security import hash_password
from backend.app.db.database import commit_or_rollback
from backend.app.models.customer import Customer
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.refresh_session import RefreshSession
from backend.app.models.verification_token import VerificationToken
from backend.app.services.customers import normalize_email
from backend.app.services.notifications import enqueue_notification
from backend.app.services.verification import (
    create_verification_token,
    hash_verification_token,
)

PASSWORD_RESET_PURPOSE = "PASSWORD_RESET"
PASSWORD_RESET_EXPIRES_MINUTES = 30
PASSWORD_RESET_COOLDOWN_SECONDS = 30


class InvalidPasswordResetTokenError(ValueError):
    pass


def request_customer_password_reset(
    db: Session,
    *,
    email: str,
) -> None:
    normalized_email = normalize_email(email)
    email_lookup_hmac = create_email_lookup_hmac(normalized_email)
    now = datetime.now(timezone.utc)

    customer_account = (
        db.query(CustomerAccount)
        .join(Customer, Customer.id == CustomerAccount.customer_id)
        .filter(
            Customer.email_lookup_hmac == email_lookup_hmac,
            CustomerAccount.account_status == "ACTIVE",
            CustomerAccount.email_verified_at.is_not(None),
        )
        .with_for_update(of=CustomerAccount)
        .populate_existing()
        .one_or_none()
    )

    if customer_account is None:
        return

    cooldown_started_at = now - timedelta(
        seconds=PASSWORD_RESET_COOLDOWN_SECONDS,
    )
    recent_reset_token = (
        db.query(VerificationToken.id)
        .filter(
            VerificationToken.customer_account_id == customer_account.id,
            VerificationToken.purpose == PASSWORD_RESET_PURPOSE,
            VerificationToken.created_at >= cooldown_started_at,
        )
        .first()
    )

    if recent_reset_token is not None:
        return

    db.query(VerificationToken).filter(
        VerificationToken.customer_account_id == customer_account.id,
        VerificationToken.purpose == PASSWORD_RESET_PURPOSE,
        VerificationToken.used_at.is_(None),
    ).update(
        {VerificationToken.used_at: now},
        synchronize_session=False,
    )

    reset_token, expires_at = create_verification_token(
        db,
        customer_account.id,
        purpose=PASSWORD_RESET_PURPOSE,
        expires_minutes=PASSWORD_RESET_EXPIRES_MINUTES,
    )
    enqueue_notification(
        db,
        customer_id=customer_account.customer_id,
        notification_type=PASSWORD_RESET_PURPOSE,
        recipient_email=normalized_email,
        payload={"password_reset_token": reset_token},
        expires_at=expires_at,
    )
    commit_or_rollback(db)


def confirm_customer_password_reset(
    db: Session,
    *,
    reset_token: str,
    new_password: str,
) -> None:
    token_hash = hash_verification_token(reset_token)
    token_candidate = (
        db.query(VerificationToken)
        .filter(
            VerificationToken.token_hash == token_hash,
            VerificationToken.purpose == PASSWORD_RESET_PURPOSE,
        )
        .one_or_none()
    )

    if token_candidate is None:
        raise InvalidPasswordResetTokenError(
            "Invalid or expired password reset token"
        )

    customer_account = (
        db.query(CustomerAccount)
        .filter(
            CustomerAccount.id == token_candidate.customer_account_id,
        )
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )

    if (
        customer_account is None
        or customer_account.account_status != "ACTIVE"
        or customer_account.email_verified_at is None
    ):
        raise InvalidPasswordResetTokenError(
            "Invalid or expired password reset token"
        )

    now = datetime.now(timezone.utc)
    verification_token = (
        db.query(VerificationToken)
        .filter(
            VerificationToken.id == token_candidate.id,
            VerificationToken.customer_account_id == customer_account.id,
            VerificationToken.purpose == PASSWORD_RESET_PURPOSE,
            VerificationToken.used_at.is_(None),
        )
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )

    if verification_token is None or verification_token.expires_at <= now:
        raise InvalidPasswordResetTokenError(
            "Invalid or expired password reset token"
        )

    customer_account.password_hash = hash_password(new_password)
    customer_account.password_changed_at = now

    db.query(VerificationToken).filter(
        VerificationToken.customer_account_id == customer_account.id,
        VerificationToken.purpose == PASSWORD_RESET_PURPOSE,
        VerificationToken.used_at.is_(None),
    ).update(
        {VerificationToken.used_at: now},
        synchronize_session=False,
    )

    db.query(RefreshSession).filter(
        RefreshSession.customer_id == customer_account.customer_id,
        RefreshSession.revoked_at.is_(None),
        RefreshSession.expires_at > now,
    ).update(
        {RefreshSession.revoked_at: now},
        synchronize_session=False,
    )

    commit_or_rollback(db)
