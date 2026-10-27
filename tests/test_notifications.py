import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import func

from backend.app.core.data_protection import (
    create_email_lookup_hmac,
    decrypt_pii,
)
from backend.app.models.customer import Customer
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.notification import Notification
from backend.app.models.verification_token import VerificationToken
from backend.app.services.notifications import enqueue_notification
from backend.app.services.verification import hash_verification_token


def test_registration_enqueues_encrypted_verification_notification(
    api_client,
    test_db_session,
):
    email = f"outbox-{uuid4().hex}@example.com"

    response = api_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "test-password-123",
            "first_name": "Outbox",
            "last_name": "Test",
        },
    )

    assert response.status_code == 201

    customer = (
        test_db_session.query(Customer)
        .filter(
            Customer.email_lookup_hmac == create_email_lookup_hmac(email),
        )
        .one()
    )
    account = (
        test_db_session.query(CustomerAccount)
        .filter(CustomerAccount.customer_id == customer.id)
        .one()
    )
    verification_token = (
        test_db_session.query(VerificationToken)
        .filter(
            VerificationToken.customer_account_id == account.id,
            VerificationToken.purpose == "EMAIL_VERIFICATION",
        )
        .one()
    )
    notification = (
        test_db_session.query(Notification)
        .filter(Notification.customer_id == customer.id)
        .one()
    )
    payload = json.loads(decrypt_pii(notification.payload_ciphertext))
    raw_token = payload["verification_token"]

    assert notification.notification_type == "EMAIL_VERIFICATION"
    assert notification.status == "PENDING"
    assert notification.attempt_count == 0
    assert notification.expires_at == verification_token.expires_at
    assert notification.recipient_email_ciphertext != email
    assert decrypt_pii(notification.recipient_email_ciphertext) == email
    assert raw_token not in notification.payload_ciphertext
    assert hash_verification_token(raw_token) == verification_token.token_hash


def test_customer_login_requires_email_verification(api_client):
    email = f"unverified-login-{uuid4().hex}@example.com"
    password = "test-password-123"

    registration_response = api_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "first_name": "Unverified",
            "last_name": "Customer",
        },
    )
    assert registration_response.status_code == 201

    login_response = api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_response.status_code == 401
    assert login_response.json()["detail"] == "Email address has not been verified"


def test_registration_rolls_back_when_notification_cannot_be_enqueued(
    api_client,
    test_db_session,
    monkeypatch,
):
    email = f"outbox-failure-{uuid4().hex}@example.com"

    def fail_enqueue(*args, **kwargs):
        raise RuntimeError("notification enqueue failed")

    monkeypatch.setattr(
        "backend.app.services.auth.enqueue_notification",
        fail_enqueue,
    )

    with pytest.raises(RuntimeError, match="notification enqueue failed"):
        api_client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "test-password-123",
                "first_name": "Outbox",
                "last_name": "Failure",
            },
        )

    test_db_session.rollback()

    customer_count = (
        test_db_session.query(func.count(Customer.id))
        .filter(
            Customer.email_lookup_hmac == create_email_lookup_hmac(email),
        )
        .scalar()
    )

    assert customer_count == 0


def test_enqueue_notification_links_order() -> None:
    db = Mock()
    customer_id = uuid4()
    order_id = uuid4()
    recipient_email = "order@example.com"
    payload = {"order_number": "HM-2026-000001"}

    notification = enqueue_notification(
        db,
        customer_id=customer_id,
        notification_type="ORDER_SHIPPED",
        recipient_email=recipient_email,
        payload=payload,
        order_id=order_id,
    )

    assert notification.customer_id == customer_id
    assert notification.order_id == order_id
    assert decrypt_pii(notification.recipient_email_ciphertext) == recipient_email
    assert json.loads(decrypt_pii(notification.payload_ciphertext)) == payload
    db.add.assert_called_once_with(notification)
    db.flush.assert_called_once_with()
