import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NoReturn
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.security import create_access_token
from backend.app.db.database import commit_or_rollback, flush_or_rollback
from backend.app.models.customer import Customer
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.refresh_session import RefreshSession, RefreshSessionToken
from backend.app.models.staff_user import StaffUser
from backend.app.schemas.auth import TokenResponse

logger = logging.getLogger(__name__)


class RefreshSessionError(RuntimeError):
    pass


class InvalidRefreshTokenError(RefreshSessionError):
    pass


class InvalidCsrfTokenError(RefreshSessionError):
    pass


class TokenReplayBreachError(RefreshSessionError):
    pass


@dataclass(frozen=True)
class RefreshCookieCredentials:
    refresh_token: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True)
class RefreshedAuthentication:
    response: TokenResponse
    role: str | None
    cookies: RefreshCookieCredentials


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _session_expiration(
    now: datetime,
    *,
    customer_id: UUID | None,
    staff_user_id: UUID | None,
) -> datetime:
    if (customer_id is None) == (staff_user_id is None):
        raise ValueError("Exactly one session owner must be specified")

    if customer_id is not None:
        return now + timedelta(days=settings.customer_refresh_session_days)

    return now + timedelta(hours=settings.staff_refresh_session_hours)


def create_refresh_session(
    db: Session,
    *,
    customer_id: UUID | None = None,
    staff_user_id: UUID | None = None,
) -> RefreshCookieCredentials:
    now = datetime.now(timezone.utc)
    refresh_token = _new_token()
    csrf_token = _new_token()
    expires_at = _session_expiration(
        now,
        customer_id=customer_id,
        staff_user_id=staff_user_id,
    )

    refresh_session = RefreshSession(
        customer_id=customer_id,
        staff_user_id=staff_user_id,
        csrf_token_hash=_hash_token(csrf_token),
        current_generation=0,
        expires_at=expires_at,
    )
    db.add(refresh_session)
    flush_or_rollback(db)

    db.add(
        RefreshSessionToken(
            session_id=refresh_session.id,
            token_hash=_hash_token(refresh_token),
            generation=0,
            state="ACTIVE",
        )
    )
    flush_or_rollback(db)

    return RefreshCookieCredentials(
        refresh_token=refresh_token,
        csrf_token=csrf_token,
        expires_at=expires_at,
    )


def _validate_csrf_token(
    refresh_session: RefreshSession,
    csrf_cookie: str | None,
    csrf_header: str | None,
) -> str:
    if not csrf_cookie or not csrf_header:
        raise InvalidCsrfTokenError("CSRF token is missing")

    if not hmac.compare_digest(csrf_cookie, csrf_header):
        raise InvalidCsrfTokenError("CSRF token does not match")

    if not hmac.compare_digest(
        _hash_token(csrf_cookie),
        refresh_session.csrf_token_hash,
    ):
        raise InvalidCsrfTokenError("CSRF token is invalid")

    return csrf_cookie


def _revoke_after_replay(
    db: Session,
    refresh_session: RefreshSession,
    now: datetime,
) -> NoReturn:
    refresh_session.revoked_at = now
    commit_or_rollback(db)
    logger.warning(
        "Refresh-token replay detected; revoked session %s",
        refresh_session.id,
    )
    raise TokenReplayBreachError("Refresh-token replay detected")


def _revoke_unavailable_session(
    db: Session,
    refresh_session: RefreshSession,
    now: datetime,
) -> NoReturn:
    refresh_session.revoked_at = now
    commit_or_rollback(db)
    raise InvalidRefreshTokenError("Refresh session is no longer available")


def _get_session_token_and_locked_session(
    db: Session,
    refresh_token: str,
) -> tuple[RefreshSessionToken, RefreshSession]:
    token_hash = _hash_token(refresh_token)
    token_record = (
        db.query(RefreshSessionToken)
        .filter(RefreshSessionToken.token_hash == token_hash)
        .one_or_none()
    )

    if token_record is None:
        raise InvalidRefreshTokenError("Refresh token is invalid")

    refresh_session = (
        db.query(RefreshSession)
        .filter(RefreshSession.id == token_record.session_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )

    if refresh_session is None:
        raise InvalidRefreshTokenError("Refresh session is invalid")

    # Re-read after locking the session so concurrent refreshes see the
    # generation committed by the request that acquired the lock first.
    token_record = (
        db.query(RefreshSessionToken)
        .filter(RefreshSessionToken.token_hash == token_hash)
        .populate_existing()
        .one_or_none()
    )

    if token_record is None:
        raise InvalidRefreshTokenError("Refresh token is invalid")

    return token_record, refresh_session


def rotate_refresh_session(
    db: Session,
    *,
    refresh_token: str | None,
    csrf_cookie: str | None,
    csrf_header: str | None,
) -> RefreshedAuthentication:
    if not refresh_token:
        raise InvalidRefreshTokenError("Refresh token is missing")

    token_record, refresh_session = _get_session_token_and_locked_session(
        db,
        refresh_token,
    )
    now = datetime.now(timezone.utc)

    if refresh_session.revoked_at is not None:
        raise InvalidRefreshTokenError("Refresh session is revoked")

    if refresh_session.expires_at <= now:
        raise InvalidRefreshTokenError("Refresh session has expired")

    if (
        token_record.state != "ACTIVE"
        or token_record.generation != refresh_session.current_generation
    ):
        _revoke_after_replay(db, refresh_session, now)

    validated_csrf_token = _validate_csrf_token(
        refresh_session,
        csrf_cookie,
        csrf_header,
    )

    role: str | None = None

    if refresh_session.customer_id is not None:
        customer = (
            db.query(Customer)
            .join(
                CustomerAccount,
                CustomerAccount.customer_id == Customer.id,
            )
            .filter(
                Customer.id == refresh_session.customer_id,
                CustomerAccount.account_status == "ACTIVE",
                CustomerAccount.email_verified_at.is_not(None),
            )
            .one_or_none()
        )

        if customer is None:
            _revoke_unavailable_session(db, refresh_session, now)

        subject = str(customer.id)
    else:
        staff_user = (
            db.query(StaffUser)
            .filter(
                StaffUser.id == refresh_session.staff_user_id,
                StaffUser.account_status == "ACTIVE",
            )
            .one_or_none()
        )

        if staff_user is None:
            _revoke_unavailable_session(db, refresh_session, now)

        subject = str(staff_user.id)
        role = staff_user.role

    next_refresh_token = _new_token()
    response = TokenResponse(
        access_token=create_access_token(subject, role=role),
        expires_in=settings.access_token_expire_minutes * 60,
    )
    token_record.state = "ROTATED"
    token_record.rotated_at = now
    refresh_session.current_generation += 1
    refresh_session.last_used_at = now

    flush_or_rollback(db)

    db.add(
        RefreshSessionToken(
            session_id=refresh_session.id,
            token_hash=_hash_token(next_refresh_token),
            generation=refresh_session.current_generation,
            state="ACTIVE",
        )
    )
    flush_or_rollback(db)

    commit_or_rollback(db)

    credentials = RefreshCookieCredentials(
        refresh_token=next_refresh_token,
        csrf_token=validated_csrf_token,
        expires_at=refresh_session.expires_at,
    )

    return RefreshedAuthentication(
        response=response,
        role=role,
        cookies=credentials,
    )


def logout_refresh_session(
    db: Session,
    *,
    refresh_token: str,
    csrf_cookie: str | None,
    csrf_header: str | None,
) -> bool:
    token_record = (
        db.query(RefreshSessionToken)
        .filter(
            RefreshSessionToken.token_hash == _hash_token(refresh_token),
        )
        .one_or_none()
    )

    if token_record is None:
        return False

    refresh_session = (
        db.query(RefreshSession)
        .filter(RefreshSession.id == token_record.session_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )

    if refresh_session is None or refresh_session.revoked_at is not None:
        return False

    _validate_csrf_token(
        refresh_session,
        csrf_cookie,
        csrf_header,
    )

    refresh_session.revoked_at = datetime.now(timezone.utc)
    commit_or_rollback(db)
    return True


def delete_expired_refresh_sessions(db: Session) -> int:
    deleted_count = (
        db.query(RefreshSession)
        .filter(
            RefreshSession.expires_at <= datetime.now(timezone.utc),
        )
        .delete(synchronize_session=False)
    )
    commit_or_rollback(db)
    return deleted_count
