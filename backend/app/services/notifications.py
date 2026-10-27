import json
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.data_protection import encrypt_pii
from backend.app.db.database import flush_or_rollback
from backend.app.models.notification import Notification


def enqueue_notification(
    db: Session,
    customer_id: UUID,
    notification_type: str,
    recipient_email: str,
    payload: dict[str, str],
    expires_at: datetime | None = None,
    order_id: UUID | None = None,
) -> Notification:
    notification = Notification(
        customer_id=customer_id,
        notification_type=notification_type,
        recipient_email_ciphertext=encrypt_pii(recipient_email),
        payload_ciphertext=encrypt_pii(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        ),
        expires_at=expires_at,
        order_id=order_id,
    )

    db.add(notification)
    flush_or_rollback(db)
    return notification
