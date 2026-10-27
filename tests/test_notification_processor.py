import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httplib2
import pytest
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.data_protection import (
    create_email_lookup_hmac,
    encrypt_pii,
)
from backend.app.integrations.gmail.credentials import GmailCredentialsError
from backend.app.models.customer import Customer
from backend.app.models.notification import Notification
from backend.app.services.notification_worker import (
    _notification_email,
    _record_failure,
    process_one_notification,
)
from backend.app.services.notifications import enqueue_notification


def _enqueue_verification_notification(
    db: Session,
    expires_at: datetime | None = None,
) -> tuple[str, Notification]:
    recipient_email = f"worker-{uuid4().hex}@example.com"
    customer = Customer(
        email_ciphertext=encrypt_pii(recipient_email),
        email_lookup_hmac=create_email_lookup_hmac(recipient_email),
    )
    db.add(customer)
    db.flush()

    notification = enqueue_notification(
        db,
        customer_id=customer.id,
        notification_type="EMAIL_VERIFICATION",
        recipient_email=recipient_email,
        payload={"verification_token": "test-verification-token"},
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    notification.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.flush()

    return recipient_email, notification


def test_process_one_notification_sends_and_marks_sent(
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recipient_email, notification = _enqueue_verification_notification(test_db_session)
    sent_messages: list[tuple[str, str, str]] = []

    def fake_send(
        recipient: str,
        subject: str,
        body: str,
    ) -> str:
        sent_messages.append((recipient, subject, body))
        return "test-gmail-message-id"

    monkeypatch.setattr(
        settings,
        "frontend_public_url",
        "https://shop.example.test/",
    )
    monkeypatch.setattr(
        "backend.app.services.notification_worker.send_gmail_message",
        fake_send,
    )

    assert process_one_notification(test_db_session) is True
    test_db_session.refresh(notification)

    assert notification.status == "SENT"
    assert notification.attempt_count == 1
    assert notification.provider_message_id == "test-gmail-message-id"
    assert notification.sent_at is not None
    assert notification.locked_at is None
    assert len(sent_messages) == 1
    assert sent_messages[0][0] == recipient_email
    assert sent_messages[0][1] == "Please verify your Half Moon account"
    assert sent_messages[0][2] == (
        "Thank you for creating a Half Moon account.\n\n"
        "To verify your account, please click on the following link:\n"
        "https://shop.example.test/verify-email"
        "?token=test-verification-token\n\n"
        "If you did not create this account, please ignore this email."
    )


@pytest.mark.parametrize(
    ("status", "content"),
    [
        (503, b'{"error":{"message":"temporary failure"}}'),
        (
            403,
            b'{"error":{"errors":[{"reason":"userRateLimitExceeded"}]}}',
        ),
    ],
)
def test_process_one_notification_retries_transient_gmail_errors(
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    content: bytes,
) -> None:
    _, notification = _enqueue_verification_notification(test_db_session)

    def fail_send(recipient: str, subject: str, body: str) -> str:
        response = httplib2.Response({"status": status})
        raise HttpError(response, content)

    monkeypatch.setattr(
        "backend.app.services.notification_worker.send_gmail_message",
        fail_send,
    )
    before = datetime.now(timezone.utc)

    assert process_one_notification(test_db_session) is True
    test_db_session.refresh(notification)

    assert notification.status == "PENDING"
    assert notification.attempt_count == 1
    assert notification.next_attempt_at >= before + timedelta(seconds=25)
    assert notification.next_attempt_at <= before + timedelta(seconds=40)
    assert notification.last_error is not None


def test_process_one_notification_does_not_retry_gmail_auth_failure(
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, notification = _enqueue_verification_notification(test_db_session)

    def fail_send(recipient: str, subject: str, body: str) -> str:
        raise GmailCredentialsError("test credentials unavailable")

    monkeypatch.setattr(
        "backend.app.services.notification_worker.send_gmail_message",
        fail_send,
    )

    assert process_one_notification(test_db_session) is True
    test_db_session.refresh(notification)

    assert notification.status == "FAILED"
    assert notification.attempt_count == 1
    assert notification.next_attempt_at is not None
    assert notification.locked_at is None


def test_process_one_notification_quarantines_invalid_record_data(
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, notification = _enqueue_verification_notification(test_db_session)

    def reject_invalid_record(_notification: Notification) -> tuple[str, str, str]:
        raise ValueError("malformed notification content")

    monkeypatch.setattr(
        "backend.app.services.notification_worker._notification_email",
        reject_invalid_record,
    )

    assert process_one_notification(test_db_session) is True
    test_db_session.refresh(notification)

    assert notification.status == "FAILED"
    assert notification.attempt_count == 1
    assert notification.locked_at is None
    assert notification.last_error == "Notification data is invalid"


def test_unexpected_notification_error_marks_failed_before_propagating(
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, notification = _enqueue_verification_notification(test_db_session)

    def fail_send(recipient: str, subject: str, body: str) -> str:
        raise RuntimeError("unexpected sender failure")

    monkeypatch.setattr(
        "backend.app.services.notification_worker.send_gmail_message",
        fail_send,
    )

    with pytest.raises(RuntimeError, match="unexpected sender failure"):
        process_one_notification(test_db_session)

    test_db_session.refresh(notification)
    assert notification.status == "FAILED"
    assert notification.locked_at is None
    assert notification.last_error == "Unexpected notification processing failure"


def test_process_one_notification_marks_expired_notification_failed(
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, notification = _enqueue_verification_notification(
        test_db_session,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    def fail_if_called(recipient: str, subject: str, body: str) -> str:
        raise AssertionError("Expired notification must not be sent")

    monkeypatch.setattr(
        "backend.app.services.notification_worker.send_gmail_message",
        fail_if_called,
    )

    assert process_one_notification(test_db_session) is True
    test_db_session.refresh(notification)

    assert notification.status == "FAILED"
    assert notification.attempt_count == 0
    assert notification.last_error == "Notification expired before delivery"


def test_record_failure_retries_notification_without_expiry() -> None:
    notification = Notification(
        status="PROCESSING",
        attempt_count=1,
        expires_at=None,
        locked_at=datetime.now(timezone.utc),
    )

    _record_failure(
        notification,
        "Temporary network failure",
        retryable=True,
    )

    assert notification.status == "PENDING"
    assert notification.next_attempt_at is not None
    assert notification.next_attempt_at > datetime.now(timezone.utc)
    assert notification.locked_at is None
    assert notification.last_error == "Temporary network failure"


@pytest.mark.parametrize(
    ("notification_type", "payload", "expected_subject", "expected_body"),
    [
        (
            "ORDER_SHIPPED",
            {
                "order_number": "HM-2026-000042",
                "shipment_reference": "HM-SHP-2026-000042",
            },
            "Good news! Your Half Moon order has been shipped.",
            (
                "Your order HM-2026-000042 has been shipped.\n\n"
                "Half Moon shipment reference: HM-SHP-2026-000042\n\n"
                "Thank you for choosing Half Moon!"
            ),
        ),
        (
            "ORDER_DELIVERED",
            {"order_number": "HM-2026-000042", "item_count": "1"},
            "Your Half Moon order has been delivered",
            (
                "Your order HM-2026-000042 has been delivered.\n\n"
                "We hope you love your Half Moon piece.\n\n"
                "We hope to see you again!"
            ),
        ),
        (
            "ORDER_DELIVERED",
            {"order_number": "HM-2026-000042", "item_count": "2"},
            "Your Half Moon order has been delivered",
            (
                "Your order HM-2026-000042 has been delivered.\n\n"
                "We hope you love your Half Moon pieces.\n\n"
                "We hope to see you again!"
            ),
        ),
        (
            "ORDER_PICKED_UP",
            {"order_number": "HM-2026-000042"},
            "Your Half Moon order has been picked up",
            (
                "Your order HM-2026-000042 has been picked up.\n\n"
                "Thank you for choosing Half Moon!"
            ),
        ),
        (
            "ORDER_READY_FOR_PICKUP",
            {"order_number": "HM-2026-000042"},
            "Your Half Moon order is ready for pickup",
            (
                "Your order HM-2026-000042 is ready for pickup at our store.\n\n"
                "Please collect it within seven days.\n\n"
                "Thank you for choosing Half Moon!"
            ),
        ),
        (
            "ORDER_PICKUP_EXPIRED",
            {"order_number": "HM-2026-000042"},
            "Your Half Moon pickup window has expired",
            (
                "Your pickup window for order HM-2026-000042 has expired. "
                "Your order has been cancelled and a refund to your original "
                "payment method has been recorded.\n\n"
                "Please note: refunds are simulated in this demo; no "
                "payment-provider refund has been sent."
            ),
        ),
    ],
)
def test_order_notification_email_formats_approved_copy(
    notification_type: str,
    payload: dict[str, str],
    expected_subject: str,
    expected_body: str,
) -> None:
    recipient_email = "order@example.com"
    notification = Notification(
        order_id=uuid4(),
        notification_type=notification_type,
        recipient_email_ciphertext=encrypt_pii(recipient_email),
        payload_ciphertext=encrypt_pii(json.dumps(payload)),
    )

    assert _notification_email(notification) == (
        recipient_email,
        expected_subject,
        expected_body,
    )
