import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.data_protection import create_email_lookup_hmac
from backend.app.db.database import commit_or_rollback, flush_or_rollback
from backend.app.models.customer import Customer
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.verification_token import VerificationToken
from backend.app.services.customers import normalize_email
from backend.app.services.notifications import enqueue_notification

EMAIL_VERIFICATION_PURPOSE = "EMAIL_VERIFICATION"
EMAIL_VERIFICATION_EXPIRES_MINUTES = 30
EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS = 30

def hash_verification_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_verification_token(
    db: Session,
    customer_account_id: UUID,
    purpose: str = EMAIL_VERIFICATION_PURPOSE,
    expires_minutes: int = EMAIL_VERIFICATION_EXPIRES_MINUTES,
) -> tuple[str, datetime]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_verification_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)

    verification_token = VerificationToken(
        customer_account_id=customer_account_id,
        token_hash=token_hash,
        purpose=purpose,
        expires_at=expires_at,
    )

    db.add(verification_token)
    flush_or_rollback(db)

    return raw_token, expires_at


def request_customer_email_verification(
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
            CustomerAccount.email_verified_at.is_(None),
        )
        .with_for_update(of=CustomerAccount)
        .populate_existing()
        .one_or_none()
    )

    if customer_account is None:
        return

    cooldown_started_at = now - timedelta(
        seconds=EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS,
    )
    recent_verification_token = (
        db.query(VerificationToken.id)
        .filter(
            VerificationToken.customer_account_id == customer_account.id,
            VerificationToken.purpose == EMAIL_VERIFICATION_PURPOSE,
            VerificationToken.created_at >= cooldown_started_at,
        )
        .first()
    )

    if recent_verification_token is not None:
        return

    db.query(VerificationToken).filter(
        VerificationToken.customer_account_id == customer_account.id,
        VerificationToken.purpose == EMAIL_VERIFICATION_PURPOSE,
        VerificationToken.used_at.is_(None),
    ).update(
        {VerificationToken.used_at: now},
        synchronize_session=False,
    )

    verification_token, expires_at = create_verification_token(
        db,
        customer_account.id,
        purpose=EMAIL_VERIFICATION_PURPOSE,
        expires_minutes=EMAIL_VERIFICATION_EXPIRES_MINUTES,
    )
    enqueue_notification(
        db,
        customer_id=customer_account.customer_id,
        notification_type=EMAIL_VERIFICATION_PURPOSE,
        recipient_email=normalized_email,
        payload={"verification_token": verification_token},
        expires_at=expires_at,
    )
    commit_or_rollback(db)


def find_valid_verification_token(
    db: Session,
    raw_token: str,
    purpose: str = EMAIL_VERIFICATION_PURPOSE,
) -> VerificationToken | None:
    token_hash = hash_verification_token(raw_token)

    verification_token = (
        db.query(VerificationToken)
        .filter(
            VerificationToken.token_hash == token_hash,
            VerificationToken.purpose == purpose,
            VerificationToken.used_at.is_(None),
        )
        .first()
    )

    if verification_token is None:
        return None

    if verification_token.expires_at <= datetime.now(timezone.utc):
        return None

    return verification_token


def verify_customer_email(
    db: Session,
    raw_token: str,
) -> None:
    verification_token = find_valid_verification_token(
        db,
        raw_token,
        purpose=EMAIL_VERIFICATION_PURPOSE,
    )

    if verification_token is None:
        raise ValueError("Invalid or expired verification token")

    customer_account = (
        db.query(CustomerAccount)
        .filter(CustomerAccount.id == verification_token.customer_account_id)
        .first()
    )

    if customer_account is None:
        raise ValueError("Customer account not found")

    now = datetime.now(timezone.utc)
    customer_account.email_verified_at = now
    verification_token.used_at = now

    commit_or_rollback(db)
