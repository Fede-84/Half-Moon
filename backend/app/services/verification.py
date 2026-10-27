import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.db.database import commit_or_rollback, flush_or_rollback
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.verification_token import VerificationToken


def hash_verification_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_verification_token(
    db: Session,
    customer_account_id: UUID,
    purpose: str = "EMAIL_VERIFICATION",
    expires_minutes: int = 30,
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


def find_valid_verification_token(
    db: Session,
    raw_token: str,
    purpose: str = "EMAIL_VERIFICATION",
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
        purpose="EMAIL_VERIFICATION",
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
