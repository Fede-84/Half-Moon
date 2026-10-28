import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.core.data_protection import decrypt_pii
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.notification import Notification
from backend.app.models.verification_token import VerificationToken
from backend.app.services.verification import hash_verification_token

AUTH_PATH = "/api/v1/auth"


def _register_unverified_customer(
    api_client: TestClient,
) -> tuple[str, UUID]:
    email = f"verification-resend-{uuid4().hex}@example.com"
    response = api_client.post(
        f"{AUTH_PATH}/register",
        json={
            "email": email,
            "password": "test-password-123",
            "first_name": "Verification",
            "last_name": "Resend",
        },
    )

    assert response.status_code == 201
    return email, UUID(response.json()["id"])


def test_resend_is_generic_rate_limited_and_replaces_unused_token(
    api_client: TestClient,
    test_db_session: Session,
) -> None:
    unknown_response = api_client.post(
        f"{AUTH_PATH}/verify-email/resend",
        json={"email": f"unknown-{uuid4().hex}@example.com"},
    )
    email, customer_id = _register_unverified_customer(api_client)
    account = (
        test_db_session.query(CustomerAccount)
        .filter(CustomerAccount.customer_id == customer_id)
        .one()
    )
    initial_token = (
        test_db_session.query(VerificationToken)
        .filter(
            VerificationToken.customer_account_id == account.id,
            VerificationToken.purpose == "EMAIL_VERIFICATION",
        )
        .one()
    )
    initial_token.created_at = datetime.now(timezone.utc) - timedelta(
        seconds=31,
    )
    test_db_session.flush()

    resend_response = api_client.post(
        f"{AUTH_PATH}/verify-email/resend",
        json={"email": email},
    )
    repeat_response = api_client.post(
        f"{AUTH_PATH}/verify-email/resend",
        json={"email": email},
    )

    assert unknown_response.status_code == 202
    assert resend_response.status_code == 202
    assert repeat_response.status_code == 202
    assert unknown_response.json() == resend_response.json()
    assert resend_response.json() == repeat_response.json()

    test_db_session.expire_all()
    notifications = (
        test_db_session.query(Notification)
        .filter(
            Notification.customer_id == customer_id,
            Notification.notification_type == "EMAIL_VERIFICATION",
        )
        .all()
    )
    assert len(notifications) == 2

    tokens = (
        test_db_session.query(VerificationToken)
        .filter(
            VerificationToken.customer_account_id == account.id,
            VerificationToken.purpose == "EMAIL_VERIFICATION",
        )
        .all()
    )
    assert len(tokens) == 2
    assert sum(token.used_at is not None for token in tokens) == 1

    active_token = next(token for token in tokens if token.used_at is None)
    notification_tokens = [
        json.loads(decrypt_pii(notification.payload_ciphertext))[
            "verification_token"
        ]
        for notification in notifications
    ]
    assert any(
        hash_verification_token(raw_token) == active_token.token_hash
        for raw_token in notification_tokens
    )
    assert datetime.now(timezone.utc) < active_token.expires_at


def test_resend_does_not_queue_for_verified_customer(
    api_client: TestClient,
    test_db_session: Session,
) -> None:
    email, customer_id = _register_unverified_customer(api_client)
    account = (
        test_db_session.query(CustomerAccount)
        .filter(
            CustomerAccount.customer_id == customer_id,
        )
        .one()
    )
    account.email_verified_at = datetime.now(timezone.utc)
    test_db_session.flush()

    response = api_client.post(
        f"{AUTH_PATH}/verify-email/resend",
        json={"email": email},
    )

    assert response.status_code == 202
    assert (
        test_db_session.query(Notification)
        .filter(
            Notification.customer_id == customer_id,
            Notification.notification_type == "EMAIL_VERIFICATION",
        )
        .count()
        == 1
    )
