import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httplib2
from cryptography.fernet import InvalidToken
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.data_protection import decrypt_pii
from backend.app.db.database import commit_or_rollback
from backend.app.integrations.gmail.client import (
    GmailDeliveryError,
    send_gmail_message,
)
from backend.app.integrations.gmail.credentials import GmailCredentialsError
from backend.app.models.notification import Notification

logger = logging.getLogger(__name__)


class PermanentNotificationError(RuntimeError):
    pass


def notification_retry_delay(attempt_count: int) -> timedelta:
    if attempt_count < 1:
        raise ValueError("attempt_count must be at least 1")

    backoff_step = min(attempt_count - 1, 4)
    delay_seconds = min(30 * (2**backoff_step), 300)
    return timedelta(seconds=delay_seconds)


def _notification_content(
    notification: Notification,
) -> tuple[str, dict[str, object]]:
    try:
        recipient_email = decrypt_pii(notification.recipient_email_ciphertext)
        payload = json.loads(decrypt_pii(notification.payload_ciphertext))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PermanentNotificationError(
            "Notification data could not be decrypted"
        ) from error

    if not recipient_email.strip() or not isinstance(payload, dict):
        raise PermanentNotificationError("Notification data is invalid")

    return recipient_email, payload


def _verification_email(
    notification: Notification,
) -> tuple[str, str, str]:
    if notification.notification_type != "EMAIL_VERIFICATION":
        raise PermanentNotificationError("Unsupported notification type")

    if notification.expires_at is None:
        raise PermanentNotificationError("Verification notification has no expiry")

    recipient_email, payload = _notification_content(notification)

    verification_token = payload.get("verification_token")
    if not isinstance(verification_token, str) or not verification_token:
        raise PermanentNotificationError("Verification token is missing")

    verification_url = (
        f"{settings.frontend_public_url.rstrip('/')}"
        f"/verify-email?{urlencode({'token': verification_token})}"
    )

    subject = "Please verify your Half Moon account"
    body = (
        "Thank you for creating a Half Moon account.\n\n"
        "To verify your account, please click on the following link:\n"
        f"{verification_url}\n\n"
        "If you did not create this account, please ignore this email."
    )

    return recipient_email, subject, body


def _order_status_email(
    notification: Notification,
) -> tuple[str, str, str]:
    if notification.notification_type not in {
        "ORDER_SHIPPED",
        "ORDER_DELIVERED",
        "ORDER_PICKED_UP",
        "ORDER_READY_FOR_PICKUP",
        "ORDER_PICKUP_EXPIRED",
    }:
        raise PermanentNotificationError("Unsupported notification type")

    if notification.order_id is None:
        raise PermanentNotificationError("Order notification has no order")

    recipient_email, payload = _notification_content(notification)

    order_number = payload.get("order_number")
    if not isinstance(order_number, str) or not order_number.strip():
        raise PermanentNotificationError("Order number is missing")

    if notification.notification_type == "ORDER_READY_FOR_PICKUP":
        subject = "Your Half Moon order is ready for pickup"
        body = (
            f"Your order {order_number} is ready for pickup at our store.\n\n"
            "Please collect it within seven days.\n\n"
            "Thank you for choosing Half Moon!"
        )
        return recipient_email, subject, body

    if notification.notification_type == "ORDER_PICKUP_EXPIRED":
        subject = "Your Half Moon pickup window has expired"
        body = (
            f"Your pickup window for order {order_number} has expired. "
            "Your order has been cancelled and a refund to your original "
            "payment method has been recorded.\n\n"
            "Please note: refunds are simulated in this demo; no "
            "payment-provider refund has been sent."
        )
        return recipient_email, subject, body

    if notification.notification_type == "ORDER_PICKED_UP":
        subject = "Your Half Moon order has been picked up"
        body = (
            f"Your order {order_number} has been picked up.\n\n"
            "Thank you for choosing Half Moon!"
        )
        return recipient_email, subject, body

    if notification.notification_type == "ORDER_SHIPPED":
        shipment_reference = payload.get("shipment_reference")
        if not isinstance(shipment_reference, str) or not shipment_reference.strip():
            raise PermanentNotificationError("Shipment reference is missing")

        subject = "Good news! Your Half Moon order has been shipped."
        body = (
            f"Your order {order_number} has been shipped.\n\n"
            f"Half Moon shipment reference: {shipment_reference}\n\n"
            "Thank you for choosing Half Moon!"
        )
        return recipient_email, subject, body

    item_count = payload.get("item_count")
    if not isinstance(item_count, str):
        raise PermanentNotificationError("Order item count is missing")

    try:
        total_items = int(item_count)
    except ValueError as error:
        raise PermanentNotificationError("Order item count is invalid") from error

    if total_items < 1:
        raise PermanentNotificationError("Order item count is invalid")

    item_word = "piece" if total_items == 1 else "pieces"
    subject = "Your Half Moon order has been delivered"
    body = (
        f"Your order {order_number} has been delivered.\n\n"
        f"We hope you love your Half Moon {item_word}.\n\n"
        "We hope to see you again!"
    )
    return recipient_email, subject, body


def _notification_email(
    notification: Notification,
) -> tuple[str, str, str]:
    if notification.notification_type == "EMAIL_VERIFICATION":
        return _verification_email(notification)

    if notification.notification_type == "PASSWORD_RESET":
        if notification.expires_at is None:
            raise PermanentNotificationError(
                "Password reset notification has no expiry"
            )

        recipient_email, payload = _notification_content(notification)
        reset_token = payload.get("password_reset_token")
        if not isinstance(reset_token, str) or not reset_token.strip():
            raise PermanentNotificationError("Password reset token is missing")

        reset_url = (
            f"{settings.frontend_public_url.rstrip('/')}"
            f"/reset-password?{urlencode({'token': reset_token})}"
        )
        subject = "Reset your Half Moon password"
        body = (
            "We received a request to reset your Half Moon account password.\n\n"
            "To choose a new password, open this link:\n"
            f"{reset_url}\n\n"
            "The token expires in 30 minutes. If you did not request a password "
            "reset, you can ignore this email."
        )
        return recipient_email, subject, body

    if notification.notification_type in {
        "ORDER_SHIPPED",
        "ORDER_DELIVERED",
        "ORDER_PICKED_UP",
        "ORDER_READY_FOR_PICKUP",
        "ORDER_PICKUP_EXPIRED",
    }:
        return _order_status_email(notification)

    raise PermanentNotificationError("Unsupported notification type")


def _http_status(error: HttpError) -> int | None:
    status = error.resp.status
    return status if isinstance(status, int) else None


def _is_rate_limit_response(error: HttpError) -> bool:
    try:
        response = json.loads(error.content)
    except json.JSONDecodeError, TypeError, UnicodeDecodeError:
        return False

    if not isinstance(response, dict):
        return False

    error_details = response.get("error")
    if not isinstance(error_details, dict):
        return False

    reasons = error_details.get("errors")
    if not isinstance(reasons, list):
        return False

    return any(
        isinstance(detail, dict)
        and detail.get("reason") in {"rateLimitExceeded", "userRateLimitExceeded"}
        for detail in reasons
    )


def _is_retryable_http_error(error: HttpError) -> bool:
    status = _http_status(error)

    if status is None:
        return False

    if status in {408, 429} or status >= 500:
        return True

    return status == 403 and _is_rate_limit_response(error)


def _record_failure(
    notification: Notification,
    message: str,
    retryable: bool,
) -> None:
    now = datetime.now(timezone.utc)
    notification.locked_at = None

    if retryable:
        next_attempt_at = now + notification_retry_delay(notification.attempt_count)
        if notification.expires_at is None or next_attempt_at < notification.expires_at:
            notification.status = "PENDING"
            notification.next_attempt_at = next_attempt_at
            notification.last_error = message
        else:
            notification.status = "FAILED"
            notification.last_error = (
                "Temporary delivery failure; no retry fits before expiry"
            )
    else:
        notification.status = "FAILED"
        notification.last_error = message

    logger.warning(
        "Notification %s delivery failed: %s",
        notification.id,
        notification.last_error,
    )


def process_one_notification(db: Session) -> bool:
    now = datetime.now(timezone.utc)

    expired_notifications = (
        db.query(Notification)
        .filter(
            Notification.status == "PENDING",
            Notification.expires_at <= now,
        )
        .with_for_update(skip_locked=True)
        .all()
    )

    for notification in expired_notifications:
        notification.status = "FAILED"
        notification.locked_at = None
        notification.last_error = "Notification expired before delivery"

    notification = (
        db.query(Notification)
        .filter(
            Notification.status == "PENDING",
            Notification.next_attempt_at <= now,
            or_(
                Notification.expires_at.is_(None),
                Notification.expires_at > now,
            ),
        )
        .order_by(Notification.next_attempt_at, Notification.created_at)
        .with_for_update(skip_locked=True)
        .first()
    )

    if notification is None:
        commit_or_rollback(db)
        return bool(expired_notifications)

    notification.status = "PROCESSING"
    notification.locked_at = now
    notification.attempt_count += 1

    try:
        try:
            recipient_email, subject, body = _notification_email(notification)
        except PermanentNotificationError as error:
            _record_failure(notification, str(error), retryable=False)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            logger.error(
                "Notification %s contains invalid processing data (%s)",
                notification.id,
                type(error).__name__,
            )
            _record_failure(
                notification,
                "Notification data is invalid",
                retryable=False,
            )
        else:
            try:
                message_id = send_gmail_message(recipient_email, subject, body)
            except (GmailCredentialsError, GmailDeliveryError, RefreshError):
                _record_failure(
                    notification,
                    "Gmail authorization or configuration failed",
                    retryable=False,
                )
            except HttpError as error:
                status = _http_status(error)
                message = (
                    f"Gmail API returned HTTP {status}"
                    if status is not None
                    else "Gmail API request failed"
                )
                _record_failure(
                    notification,
                    message,
                    retryable=_is_retryable_http_error(error),
                )
            except (
                TransportError,
                httplib2.HttpLib2Error,
                TimeoutError,
                ConnectionError,
            ):
                _record_failure(
                    notification,
                    "Temporary network failure while contacting Gmail",
                    retryable=True,
                )
            else:
                notification.status = "SENT"
                notification.provider_message_id = message_id
                notification.sent_at = datetime.now(timezone.utc)
                notification.locked_at = None
                notification.last_error = None

        commit_or_rollback(db)
    finally:
        if notification.status == "PROCESSING":
            logger.error(
                "Notification %s marked FAILED after unexpected processing failure",
                notification.id,
            )
            notification.status = "FAILED"
            notification.locked_at = None
            notification.last_error = "Unexpected notification processing failure"
            commit_or_rollback(db)

    return True
