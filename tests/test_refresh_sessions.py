import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from backend.app.db.database import commit_or_rollback
from backend.app.models.customer import Customer
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.refresh_session import RefreshSession, RefreshSessionToken
from backend.app.services.refresh_sessions import (
    InvalidCsrfTokenError,
    TokenReplayBreachError,
    create_refresh_session,
    delete_expired_refresh_sessions,
    rotate_refresh_session,
)


def _create_customer(db: Session) -> Customer:
    customer = Customer(
        email_ciphertext="encrypted@example.invalid",
        email_lookup_hmac=uuid4().hex * 2,
    )
    db.add(customer)
    db.flush()

    db.add(
        CustomerAccount(
            customer_id=customer.id,
            password_hash="unused-test-hash",
            email_verified_at=datetime.now(timezone.utc),
            account_status="ACTIVE",
        )
    )
    db.flush()
    return customer


def test_rotation_replaces_token_and_preserves_fixed_expiration(
    test_db_session: Session,
) -> None:
    customer = _create_customer(test_db_session)
    credentials = create_refresh_session(
        test_db_session,
        customer_id=customer.id,
    )
    commit_or_rollback(test_db_session)

    original_session = (
        test_db_session.query(RefreshSession)
        .filter(RefreshSession.customer_id == customer.id)
        .one()
    )
    original_expiration = original_session.expires_at

    refreshed = rotate_refresh_session(
        test_db_session,
        refresh_token=credentials.refresh_token,
        csrf_cookie=credentials.csrf_token,
        csrf_header=credentials.csrf_token,
    )

    assert refreshed.cookies.refresh_token != credentials.refresh_token
    assert refreshed.cookies.expires_at == original_expiration
    assert original_session.current_generation == 1

    token_states = (
        test_db_session.query(RefreshSessionToken.state)
        .filter(RefreshSessionToken.session_id == original_session.id)
        .order_by(RefreshSessionToken.generation)
        .all()
    )
    assert token_states == [("ROTATED",), ("ACTIVE",)]


def test_replayed_token_revokes_only_its_device_session(
    test_db_session: Session,
) -> None:
    customer = _create_customer(test_db_session)
    first_device = create_refresh_session(
        test_db_session,
        customer_id=customer.id,
    )
    second_device = create_refresh_session(
        test_db_session,
        customer_id=customer.id,
    )
    commit_or_rollback(test_db_session)

    first_session = (
        test_db_session.query(RefreshSession)
        .filter(
            RefreshSession.csrf_token_hash
            == hashlib.sha256(first_device.csrf_token.encode("utf-8")).hexdigest()
        )
        .one()
    )
    second_session = (
        test_db_session.query(RefreshSession)
        .filter(
            RefreshSession.csrf_token_hash
            == hashlib.sha256(second_device.csrf_token.encode("utf-8")).hexdigest()
        )
        .one()
    )

    rotate_refresh_session(
        test_db_session,
        refresh_token=first_device.refresh_token,
        csrf_cookie=first_device.csrf_token,
        csrf_header=first_device.csrf_token,
    )

    with pytest.raises(TokenReplayBreachError):
        rotate_refresh_session(
            test_db_session,
            refresh_token=first_device.refresh_token,
            csrf_cookie=first_device.csrf_token,
            csrf_header=first_device.csrf_token,
        )

    test_db_session.refresh(first_session)
    test_db_session.refresh(second_session)
    assert first_session.revoked_at is not None
    assert second_session.revoked_at is None


def test_csrf_mismatch_does_not_revoke_session(
    test_db_session: Session,
) -> None:
    customer = _create_customer(test_db_session)
    credentials = create_refresh_session(
        test_db_session,
        customer_id=customer.id,
    )
    commit_or_rollback(test_db_session)

    refresh_session = (
        test_db_session.query(RefreshSession)
        .filter(RefreshSession.customer_id == customer.id)
        .one()
    )

    with pytest.raises(InvalidCsrfTokenError):
        rotate_refresh_session(
            test_db_session,
            refresh_token=credentials.refresh_token,
            csrf_cookie=credentials.csrf_token,
            csrf_header="different-csrf-value",
        )

    test_db_session.refresh(refresh_session)
    assert refresh_session.revoked_at is None
    assert refresh_session.current_generation == 0


def test_expired_session_cleanup_removes_token_history(
    test_db_session: Session,
) -> None:
    customer = _create_customer(test_db_session)
    create_refresh_session(test_db_session, customer_id=customer.id)
    commit_or_rollback(test_db_session)

    refresh_session = (
        test_db_session.query(RefreshSession)
        .filter(RefreshSession.customer_id == customer.id)
        .one()
    )
    session_id = refresh_session.id
    refresh_session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    commit_or_rollback(test_db_session)

    expected_deleted_count = (
        test_db_session.query(RefreshSession)
        .filter(RefreshSession.expires_at <= datetime.now(timezone.utc))
        .count()
    )
    assert (
        delete_expired_refresh_sessions(test_db_session) == expected_deleted_count
    )
    assert (
        test_db_session.query(RefreshSession)
        .filter(RefreshSession.id == session_id)
        .one_or_none()
        is None
    )
    assert (
        test_db_session.query(RefreshSessionToken)
        .filter(RefreshSessionToken.session_id == session_id)
        .count()
        == 0
    )
