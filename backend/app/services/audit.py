from collections.abc import Mapping
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.models.audit_event import AuditEvent

AuditActorType = Literal["CUSTOMER", "STAFF", "SYSTEM", "ANONYMOUS"]
AuditOutcome = Literal["SUCCEEDED", "FAILED"]
AuditActionCode = Literal[
    "STAFF_INVENTORY_TRANSFER",
    "STAFF_POS_CLAIM_RESET",
    "CUSTOMER_POS_ORDER_CLAIM",
    "CUSTOMER_RETURN_REQUESTED",
    "CUSTOMER_RETURN_PROCESSED",
    "CUSTOMER_POS_RETURN_REQUESTED",
    "STAFF_POS_RETURN_PROCESSED",
    "SYSTEM_POS_RETURN_PROCESSED",
    "SYSTEM_PICKUP_EXPIRY",
    "SYSTEM_LATE_PAYMENT_REFUND",
    "POS_CLAIM_LOCKOUT",
    "RETURN_PROCESSING_INTEGRITY_FAILURE",
]
AuditDetailValue = str | int | bool

_ALLOWED_DETAIL_KEYS: dict[AuditActionCode, frozenset[str]] = {
    "STAFF_INVENTORY_TRANSFER": frozenset(
        {"from_location", "to_location", "quantity"}
    ),
    "STAFF_POS_CLAIM_RESET": frozenset({"failed_attempt_count"}),
    "CUSTOMER_POS_ORDER_CLAIM": frozenset(),
    "CUSTOMER_RETURN_REQUESTED": frozenset({"channel", "item_count"}),
    "CUSTOMER_RETURN_PROCESSED": frozenset(
        {"amount", "item_count", "payment_status"}
    ),
    "CUSTOMER_POS_RETURN_REQUESTED": frozenset({"item_count"}),
    "STAFF_POS_RETURN_PROCESSED": frozenset(
        {"amount", "item_count", "payment_status"}
    ),
    "SYSTEM_POS_RETURN_PROCESSED": frozenset(
        {"amount", "item_count", "payment_status"}
    ),
    "SYSTEM_PICKUP_EXPIRY": frozenset({"amount", "item_count"}),
    "SYSTEM_LATE_PAYMENT_REFUND": frozenset({"amount", "currency"}),
    "POS_CLAIM_LOCKOUT": frozenset({"failed_attempt_count"}),
    "RETURN_PROCESSING_INTEGRITY_FAILURE": frozenset(),
}
_TERMINAL_FAILURE_ACTIONS = frozenset(
    {
        "SYSTEM_PICKUP_EXPIRY",
        "SYSTEM_LATE_PAYMENT_REFUND",
        "RETURN_PROCESSING_INTEGRITY_FAILURE",
    }
)


def record_audit_event(
    db: Session,
    *,
    actor_type: AuditActorType,
    action_code: AuditActionCode,
    outcome: AuditOutcome,
    customer_id: UUID | None = None,
    staff_user_id: UUID | None = None,
    target_type: str | None = None,
    target_id: UUID | None = None,
    error_code: str | None = None,
    correlation_id: str | None = None,
    details: Mapping[str, AuditDetailValue] | None = None,
) -> None:
    if action_code not in _ALLOWED_DETAIL_KEYS:
        raise ValueError("Unsupported audit action code")

    if actor_type not in {"CUSTOMER", "STAFF", "SYSTEM", "ANONYMOUS"}:
        raise ValueError("Unsupported audit actor type")

    if actor_type == "CUSTOMER":
        if customer_id is None or staff_user_id is not None:
            raise ValueError("Customer audit events require only a customer reference")
    elif actor_type == "STAFF":
        if staff_user_id is None or customer_id is not None:
            raise ValueError("Staff audit events require only a staff reference")
    elif customer_id is not None or staff_user_id is not None:
        raise ValueError("System and anonymous audit events cannot reference a user")

    if outcome not in {"SUCCEEDED", "FAILED"}:
        raise ValueError("Unsupported audit outcome")

    if (target_type is None) != (target_id is None):
        raise ValueError("Audit target type and ID must be provided together")
    if target_type is not None and (not target_type or len(target_type) > 50):
        raise ValueError("Audit target type is invalid")

    if outcome == "FAILED":
        if (
            error_code is None
            or not error_code
            or len(error_code) > 64
            or not error_code.isascii()
            or not error_code.replace("_", "").isalnum()
        ):
            raise ValueError("Failed audit events require a sanitized error code")
    elif error_code is not None:
        raise ValueError("Successful audit events cannot have an error code")

    allowed_keys = _ALLOWED_DETAIL_KEYS[action_code]
    event_details = dict(details) if details is not None else None
    if event_details is not None:
        if set(event_details) - allowed_keys:
            raise ValueError("Audit details contain an unsupported field")
        if any(
            not isinstance(value, (str, int, bool))
            for value in event_details.values()
        ):
            raise ValueError("Audit detail values must be scalar JSON values")

    if correlation_id is not None and (
        not correlation_id or len(correlation_id) > 255
    ):
        raise ValueError("Audit correlation ID is invalid")

    if outcome == "FAILED" and action_code in _TERMINAL_FAILURE_ACTIONS:
        if target_type is None or target_id is None:
            raise ValueError("Terminal audit failures require a target")
        pending_event_exists = any(
            isinstance(pending_event, AuditEvent)
            and pending_event.action_code == action_code
            and pending_event.outcome == "FAILED"
            and pending_event.target_type == target_type
            and pending_event.target_id == target_id
            for pending_event in db.new
        )
        if pending_event_exists:
            return

        existing_event = (
            db.query(AuditEvent.id)
            .filter(
                AuditEvent.action_code == action_code,
                AuditEvent.outcome == "FAILED",
                AuditEvent.target_type == target_type,
                AuditEvent.target_id == target_id,
            )
            .first()
        )
        if existing_event is not None:
            return

    db.add(
        AuditEvent(
            actor_type=actor_type,
            customer_id=customer_id,
            staff_user_id=staff_user_id,
            action_code=action_code,
            outcome=outcome,
            target_type=target_type,
            target_id=target_id,
            error_code=error_code,
            correlation_id=correlation_id,
            details=event_details,
        )
    )
