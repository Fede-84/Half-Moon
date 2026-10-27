import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.data_protection import (
    create_email_lookup_hmac,
    decrypt_pii,
    encrypt_pii,
)
from backend.app.core.security import verify_password
from backend.app.models.customer import Customer
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.notification import Notification
from backend.app.models.refresh_session import RefreshSession
from backend.app.models.verification_token import VerificationToken
from backend.app.services.notification_worker import _notification_email
from backend.app.services.verification import hash_verification_token

AUTH_PATH = "/api/v1/auth"


def _register_customer(
    api_client: TestClient,
    test_db_session: Session,
    *,
    verify_email: bool,
) -> tuple[str, str, UUID]:
    email = f"password-reset-{uuid4().hex}@example.com"
    password = "test-password-123"

    response = api_client.post(
        f"{AUTH_PATH}/register",
        json={
            "email": email,
            "password": password,
            "first_name": "Password",
            "last_name": "Reset",
        },
    )
    assert response.status_code == 201

    customer_id = UUID(response.json()["id"])
    if verify_email:
        account = (
            test_db_session.query(CustomerAccount)
            .filter(CustomerAccount.customer_id == customer_id)
            .one()
        )
        account.email_verified_at = datetime.now(timezone.utc)
        test_db_session.flush()

    return email, password, customer_id


def _get_reset_token(
    test_db_session: Session,
    customer_id: UUID,
) -> tuple[str, VerificationToken, Notification]:
    notification = (
        test_db_session.query(Notification)
        .filter(
            Notification.customer_id == customer_id,
            Notification.notification_type == "PASSWORD_RESET",
        )
        .one()
    )
    payload = json.loads(decrypt_pii(notification.payload_ciphertext))
    reset_token = payload["password_reset_token"]

    account = (
        test_db_session.query(CustomerAccount)
        .filter(CustomerAccount.customer_id == customer_id)
        .one()
    )
    verification_token = (
        test_db_session.query(VerificationToken)
        .filter(
            VerificationToken.customer_account_id == account.id,
            VerificationToken.purpose == "PASSWORD_RESET",
        )
        .one()
    )
    return reset_token, verification_token, notification


def test_reset_request_is_generic_and_obeys_cooldown(
    api_client: TestClient,
    test_db_session: Session,
) -> None:
    unknown_response = api_client.post(
        f"{AUTH_PATH}/password-reset/request",
        json={"email": f"unknown-{uuid4().hex}@example.com"},
    )
    email, _, customer_id = _register_customer(
        api_client,
        test_db_session,
        verify_email=True,
    )

    request_response = api_client.post(
        f"{AUTH_PATH}/password-reset/request",
        json={"email": email},
    )
    repeat_response = api_client.post(
        f"{AUTH_PATH}/password-reset/request",
        json={"email": email},
    )

    assert unknown_response.status_code == 202
    assert request_response.status_code == 202
    assert repeat_response.status_code == 202
    assert unknown_response.json() == request_response.json()
    assert request_response.json() == repeat_response.json()

    reset_notification_count = (
        test_db_session.query(Notification)
        .filter(
            Notification.customer_id == customer_id,
            Notification.notification_type == "PASSWORD_RESET",
        )
        .count()
    )
    assert reset_notification_count == 1

    reset_token, verification_token, notification = _get_reset_token(
        test_db_session,
        customer_id,
    )
    assert hash_verification_token(reset_token) == verification_token.token_hash
    assert reset_token not in notification.payload_ciphertext
    assert verification_token.used_at is None
    now = datetime.now(timezone.utc)
    assert verification_token.expires_at > now + timedelta(minutes=29)
    assert verification_token.expires_at <= now + timedelta(minutes=30)
    assert (
        decrypt_pii(notification.recipient_email_ciphertext)
        == email.lower()
    )


def test_unverified_customer_does_not_receive_reset_notification(
    api_client: TestClient,
    test_db_session: Session,
) -> None:
    email, _, customer_id = _register_customer(
        api_client,
        test_db_session,
        verify_email=False,
    )

    response = api_client.post(
        f"{AUTH_PATH}/password-reset/request",
        json={"email": email},
    )

    assert response.status_code == 202
    assert response.json()["message"] == (
        "If an eligible customer account exists, password reset instructions "
        "will be sent."
    )
    assert (
        test_db_session.query(Notification)
        .filter(
            Notification.customer_id == customer_id,
            Notification.notification_type == "PASSWORD_RESET",
        )
        .count()
        == 0
    )
    assert (
        test_db_session.query(VerificationToken)
        .join(
            CustomerAccount,
            CustomerAccount.id == VerificationToken.customer_account_id,
        )
        .filter(
            CustomerAccount.customer_id == customer_id,
            VerificationToken.purpose == "PASSWORD_RESET",
        )
        .count()
        == 0
    )


def test_password_reset_changes_password_and_revokes_sessions(
    api_client: TestClient,
    test_db_session: Session,
) -> None:
    email, old_password, customer_id = _register_customer(
        api_client,
        test_db_session,
        verify_email=True,
    )

    for _ in range(2):
        login_response = api_client.post(
            f"{AUTH_PATH}/login",
            json={"email": email, "password": old_password},
        )
        assert login_response.status_code == 200

    assert (
        test_db_session.query(RefreshSession)
        .filter(
            RefreshSession.customer_id == customer_id,
            RefreshSession.revoked_at.is_(None),
        )
        .count()
        == 2
    )

    reset_request_response = api_client.post(
        f"{AUTH_PATH}/password-reset/request",
        json={"email": email},
    )
    assert reset_request_response.status_code == 202

    reset_token, verification_token, _ = _get_reset_token(
        test_db_session,
        customer_id,
    )
    new_password = "new-test-password-456"
    confirm_response = api_client.post(
        f"{AUTH_PATH}/password-reset/confirm",
        json={
            "token": reset_token,
            "new_password": new_password,
        },
    )

    assert confirm_response.status_code == 200
    assert confirm_response.json()["message"] == (
        "Password reset successfully. Please sign in again."
    )

    account = (
        test_db_session.query(CustomerAccount)
        .filter(CustomerAccount.customer_id == customer_id)
        .one()
    )
    assert verify_password(new_password, account.password_hash)
    assert account.password_changed_at is not None

    test_db_session.refresh(verification_token)
    assert verification_token.used_at is not None
    assert (
        test_db_session.query(RefreshSession)
        .filter(
            RefreshSession.customer_id == customer_id,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.expires_at > datetime.now(timezone.utc),
        )
        .count()
        == 0
    )

    csrf_token = api_client.cookies.get("half_moon_csrf")
    assert csrf_token is not None
    refresh_response = api_client.post(
        f"{AUTH_PATH}/refresh",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert refresh_response.status_code == 401

    replay_response = api_client.post(
        f"{AUTH_PATH}/password-reset/confirm",
        json={
            "token": reset_token,
            "new_password": "another-test-password-789",
        },
    )
    assert replay_response.status_code == 400

    old_password_response = api_client.post(
        f"{AUTH_PATH}/login",
        json={"email": email, "password": old_password},
    )
    assert old_password_response.status_code == 401

    new_password_response = api_client.post(
        f"{AUTH_PATH}/login",
        json={"email": email, "password": new_password},
    )
    assert new_password_response.status_code == 200


def test_password_reset_notification_contains_clickable_reset_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_token = "reset demo-token+/&"
    monkeypatch.setattr(
        settings,
        "frontend_public_url",
        "https://store.example.test/",
    )
    notification = Notification(
        customer_id=uuid4(),
        notification_type="PASSWORD_RESET",
        recipient_email_ciphertext=encrypt_pii("customer@example.com"),
        payload_ciphertext=encrypt_pii(
            json.dumps({"password_reset_token": reset_token})
        ),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )

    recipient_email, subject, body = _notification_email(notification)
    reset_link = (
        "https://store.example.test/reset-password?"
        f"{urlencode({'token': reset_token})}"
    )

    assert recipient_email == "customer@example.com"
    assert subject == "Reset your Half Moon password"
    assert reset_link in body
    assert reset_token not in body
    assert "30 minutes" in body
