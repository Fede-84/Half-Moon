from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from backend.app.db.database import commit_or_rollback, flush_or_rollback
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.inventory_movement import InventoryMovement
from backend.app.models.inventory_unit import InventoryUnit
from backend.app.models.order import Order
from backend.app.models.order_line import OrderLine
from backend.app.models.order_line_unit import OrderLineUnit
from backend.app.models.payment import Payment
from backend.app.models.refund import Refund
from backend.app.models.return_item import ReturnItem
from backend.app.models.return_record import ReturnRecord
from backend.app.models.shipment import Shipment
from backend.app.schemas.returns import (
    CreateReturnRequest,
    CustomerOrderReturnItemResponse,
    CustomerOrderReturnRefundResponse,
    CustomerOrderReturnStatusResponse,
)
from backend.app.services.audit import record_audit_event
from backend.app.services.deadlines import utc_deadline_after_14_days


class ReturnIntegrityError(ValueError):
    pass


class POSReturnOrderNotFoundError(ValueError):
    pass


def _require_verified_customer_account(
    db: Session,
    customer_id: UUID,
) -> None:
    customer_account = (
        db.query(CustomerAccount)
        .filter(
            CustomerAccount.customer_id == customer_id,
            CustomerAccount.account_status == "ACTIVE",
            CustomerAccount.email_verified_at.is_not(None),
        )
        .first()
    )
    if customer_account is None:
        raise ValueError("A verified customer account is required for returns")


def _get_pos_sale_source_locations(
    db: Session,
    order_id: UUID,
    inventory_unit_ids: list[UUID],
) -> dict[UUID, UUID]:
    if len(set(inventory_unit_ids)) != len(inventory_unit_ids):
        raise ValueError("POS return contains duplicate inventory allocations")

    sale_movements = (
        db.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order_id,
            InventoryMovement.inventory_unit_id.in_(inventory_unit_ids),
            InventoryMovement.movement_type == "SALE",
        )
        .all()
    )

    if len(sale_movements) != len(inventory_unit_ids):
        raise ReturnIntegrityError(
            "Original POS sale source location could not be determined"
        )

    source_locations: dict[UUID, UUID] = {}
    for movement in sale_movements:
        if (
            movement.inventory_unit_id in source_locations
            or movement.from_location_id is None
            or movement.from_status != "RESERVED"
            or movement.to_location_id is not None
            or movement.to_status != "SOLD"
        ):
            raise ReturnIntegrityError("Original POS sale source location is inconsistent")

        source_locations[movement.inventory_unit_id] = movement.from_location_id

    if set(source_locations) != set(inventory_unit_ids):
        raise ReturnIntegrityError(
            "Original POS sale source location could not be determined"
        )

    return source_locations


def _validate_pos_return(
    db: Session,
    order: Order,
    customer_id: UUID,
) -> None:
    if order.customer_id != customer_id:
        raise ValueError("POS order does not belong to this customer")

    if order.order_status != "COMPLETED":
        raise ValueError("Only completed POS orders can be returned")

    if order.payment_status not in ("PAID", "PARTIALLY_REFUNDED"):
        raise ValueError("POS order has no refundable payment balance")

    if order.paid_at is None:
        raise ValueError("Paid POS order has no paid_at timestamp")

    return_deadline = utc_deadline_after_14_days(order.paid_at)
    if datetime.now(timezone.utc) > return_deadline:
        raise ValueError("POS return window has expired")


def _get_unreturned_order_line_units(
    db: Session,
    order_id: UUID,
    request: CreateReturnRequest,
) -> list[OrderLineUnit]:
    item_ids = [item.order_line_unit_id for item in request.items]
    if len(set(item_ids)) != len(item_ids):
        raise ValueError("Each return item may appear only once")

    purchased_units = (
        db.query(OrderLineUnit)
        .join(
            OrderLine,
            OrderLineUnit.order_line_id == OrderLine.id,
        )
        .filter(
            OrderLine.order_id == order_id,
            OrderLineUnit.id.in_(item_ids),
        )
        .order_by(OrderLineUnit.id)
        .with_for_update(skip_locked=True)
        .all()
    )

    if len(purchased_units) != len(item_ids):
        raise ValueError("One or more return items do not belong to this order")

    already_returned = (
        db.query(ReturnItem.id)
        .filter(ReturnItem.order_line_unit_id.in_(item_ids))
        .first()
    )
    if already_returned is not None:
        raise ValueError("One or more items have already been returned")

    return purchased_units


def _stage_return_record(
    db: Session,
    order: Order,
    customer_id: UUID,
    request: CreateReturnRequest,
    purchased_units: list[OrderLineUnit],
) -> ReturnRecord:
    return_record = ReturnRecord(
        order_id=order.id,
        customer_id=customer_id,
        status="REQUESTED",
        requested_outcome=request.requested_outcome,
        reason=request.reason,
    )
    db.add(return_record)
    flush_or_rollback(db)

    db.add_all(
        [
            ReturnItem(
                return_id=return_record.id,
                order_line_unit_id=order_line_unit.id,
                inspection_result="PENDING",
            )
            for order_line_unit in purchased_units
        ]
    )
    flush_or_rollback(db)
    return return_record


def create_return_request(
    db: Session,
    customer_id: UUID,
    order_id: UUID,
    request: CreateReturnRequest,
) -> dict[str, object]:
    order = (
        db.query(Order)
        .filter(
            Order.id == order_id,
            Order.customer_id == customer_id,
        )
        .with_for_update(skip_locked=True)
        .first()
    )

    if order is None:
        raise ValueError("Order not found")

    if order.payment_status not in ("PAID", "PARTIALLY_REFUNDED"):
        raise ValueError("Only paid orders can be returned")

    if order.channel == "ECOM":
        shipment = db.query(Shipment).filter(Shipment.order_id == order.id).first()
        if shipment is None:
            raise ValueError("Order fulfillment record not found")

        if order.fulfillment_method == "SHIPPING":
            if order.order_status != "DELIVERED":
                raise ValueError("Only delivered orders can be returned")
            if shipment.delivered_at is None:
                raise ValueError("Order delivery timestamp not found")
            return_anchor = shipment.delivered_at
            expired_message = "ECOM return window has expired"
        elif order.fulfillment_method == "STORE_PICKUP":
            if (
                order.order_status != "PICKED_UP"
                or shipment.fulfillment_method != "STORE_PICKUP"
                or shipment.status != "PICKED_UP"
            ):
                raise ValueError("Only picked-up store orders can be returned")
            if shipment.picked_up_at is None:
                raise ValueError("Order pickup timestamp not found")
            return_anchor = shipment.picked_up_at
            expired_message = "ECOM pickup return window has expired"
        else:
            raise ValueError("ECOM order has an unsupported fulfillment method")

        return_deadline = utc_deadline_after_14_days(return_anchor)
        if datetime.now(timezone.utc) > return_deadline:
            raise ValueError(expired_message)

    elif order.channel == "POS":
        raise ValueError("POS returns must be processed by store staff")

    else:
        raise ValueError("Only ECOM and POS orders can be returned")

    _require_verified_customer_account(db, customer_id)
    purchased_units = _get_unreturned_order_line_units(db, order_id, request)
    return_record = _stage_return_record(
        db,
        order,
        customer_id,
        request,
        purchased_units,
    )

    record_audit_event(
        db,
        actor_type="CUSTOMER",
        customer_id=customer_id,
        action_code="CUSTOMER_RETURN_REQUESTED",
        outcome="SUCCEEDED",
        target_type="RETURN",
        target_id=return_record.id,
        details={"channel": order.channel, "item_count": len(purchased_units)},
    )
    commit_or_rollback(db)
    db.refresh(return_record)

    return {
        "return_id": str(return_record.id),
        "order_id": str(return_record.order_id),
        "status": return_record.status,
        "requested_outcome": return_record.requested_outcome,
        "item_count": len(purchased_units),
    }


def list_customer_order_returns(
    db: Session,
    customer_id: UUID,
    order_id: UUID,
) -> list[CustomerOrderReturnStatusResponse]:
    order = (
        db.query(Order.id)
        .filter(
            Order.id == order_id,
            Order.customer_id == customer_id,
            Order.channel.in_(("ECOM", "POS")),
        )
        .one_or_none()
    )
    if order is None:
        raise ValueError("Order not found")

    return_records = (
        db.query(ReturnRecord)
        .filter(
            ReturnRecord.order_id == order_id,
            ReturnRecord.customer_id == customer_id,
        )
        .order_by(ReturnRecord.requested_at.desc(), ReturnRecord.id)
        .all()
    )
    if not return_records:
        return []

    return_ids = [return_record.id for return_record in return_records]
    return_items = (
        db.query(ReturnItem)
        .filter(ReturnItem.return_id.in_(return_ids))
        .order_by(ReturnItem.return_id, ReturnItem.order_line_unit_id)
        .all()
    )
    refunds = (
        db.query(Refund)
        .filter(Refund.return_id.in_(return_ids))
        .order_by(Refund.return_id, Refund.created_at, Refund.id)
        .all()
    )

    items_by_return: dict[UUID, list[CustomerOrderReturnItemResponse]] = {
        return_id: [] for return_id in return_ids
    }
    for return_item in return_items:
        items_by_return[return_item.return_id].append(
            CustomerOrderReturnItemResponse(
                order_line_unit_id=return_item.order_line_unit_id,
                inspection_result=return_item.inspection_result,
            )
        )

    refunds_by_return: dict[UUID, list[CustomerOrderReturnRefundResponse]] = {
        return_id: [] for return_id in return_ids
    }
    for refund in refunds:
        if refund.return_id is None:
            continue

        refunds_by_return[refund.return_id].append(
            CustomerOrderReturnRefundResponse(
                amount=str(refund.amount),
                method=refund.method,
                status=refund.status,
                completed_at=refund.completed_at,
            )
        )

    return [
        CustomerOrderReturnStatusResponse(
            return_id=return_record.id,
            order_id=return_record.order_id,
            status=return_record.status,
            requested_outcome=return_record.requested_outcome,
            reason=return_record.reason,
            requested_at=return_record.requested_at,
            completed_at=return_record.completed_at,
            items=items_by_return[return_record.id],
            refunds=refunds_by_return[return_record.id],
        )
        for return_record in return_records
    ]


def process_return(
    db: Session,
    customer_id: UUID,
    return_id: UUID,
) -> dict[str, object]:
    return_reference = (
        db.query(ReturnRecord.id, ReturnRecord.order_id)
        .filter(
            ReturnRecord.id == return_id,
            ReturnRecord.customer_id == customer_id,
        )
        .with_for_update(skip_locked=True)
        .one_or_none()
    )

    if return_reference is None:
        raise ValueError("Return not found")

    return_record_id, order_id = return_reference
    order = (
        db.query(Order)
        .filter(Order.id == order_id)
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    if order is None:
        raise ValueError("Return order not found")

    return_record = (
        db.query(ReturnRecord)
        .filter(
            ReturnRecord.id == return_record_id,
            ReturnRecord.customer_id == customer_id,
            ReturnRecord.order_id == order.id,
        )
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    if return_record is None:
        raise ValueError("Return not found")

    return _complete_return_record(
        db,
        order,
        return_record,
        acting_customer_id=customer_id,
        staff_user_id=None,
    )


def process_pos_return_by_staff(
    db: Session,
    order_id: UUID,
    request: CreateReturnRequest,
    staff_user_id: UUID,
) -> dict[str, object]:
    order = (
        db.query(Order)
        .filter(
            Order.id == order_id,
            Order.channel == "POS",
        )
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    if order is None:
        raise POSReturnOrderNotFoundError("POS order not found")

    customer_id = order.customer_id
    if customer_id is None:
        raise ValueError("The customer must claim this POS order before a return")

    if order.order_status != "COMPLETED":
        raise ValueError("Only completed POS orders can be returned")
    purchased_units = _get_unreturned_order_line_units(
        db,
        order.id,
        request,
    )
    _validate_pos_return(db, order, customer_id)
    _require_verified_customer_account(db, customer_id)

    return_record = _stage_return_record(
        db,
        order,
        customer_id,
        request,
        purchased_units,
    )

    return _complete_return_record(
        db,
        order,
        return_record,
        acting_customer_id=None,
        staff_user_id=staff_user_id,
    )


def process_pos_return_by_customer(
    db: Session,
    customer_id: UUID,
    order_id: UUID,
    request: CreateReturnRequest,
) -> dict[str, object]:
    order = (
        db.query(Order)
        .filter(
            Order.id == order_id,
            Order.customer_id == customer_id,
            Order.channel == "POS",
        )
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    if order is None:
        raise POSReturnOrderNotFoundError("POS order not found")

    if order.order_status != "COMPLETED":
        raise ValueError("Only completed POS orders can be returned")

    purchased_units = _get_unreturned_order_line_units(db, order.id, request)
    _validate_pos_return(db, order, customer_id)
    _require_verified_customer_account(db, customer_id)
    return_record = _stage_return_record(
        db,
        order,
        customer_id,
        request,
        purchased_units,
    )

    record_audit_event(
        db,
        actor_type="CUSTOMER",
        customer_id=customer_id,
        action_code="CUSTOMER_POS_RETURN_REQUESTED",
        outcome="SUCCEEDED",
        target_type="RETURN",
        target_id=return_record.id,
        details={"item_count": len(purchased_units)},
    )

    return _complete_return_record(
        db,
        order,
        return_record,
        acting_customer_id=None,
        staff_user_id=None,
        system_actor=True,
    )


def _complete_return_record(
    db: Session,
    order: Order,
    return_record: ReturnRecord,
    *,
    acting_customer_id: UUID | None,
    staff_user_id: UUID | None,
    system_actor: bool = False,
) -> dict[str, object]:
    actor_count = sum(
        (
            acting_customer_id is not None,
            staff_user_id is not None,
            system_actor,
        )
    )
    if actor_count != 1:
        raise ValueError("Exactly one return actor must be provided")

    if return_record.status != "REQUESTED":
        raise ValueError("Return has already been processed")

    if return_record.requested_outcome != "REFUND":
        raise ValueError("Unsupported return outcome")

    if order.channel == "POS":
        if acting_customer_id is not None:
            raise ValueError("POS returns must be processed by staff or system")

        if order.customer_id != return_record.customer_id:
            raise ValueError("POS order is not attached to the return customer")

        if order.order_status != "COMPLETED":
            raise ValueError("POS order is not eligible for return processing")

        if order.paid_at is None:
            raise ValueError("Paid POS order has no paid_at timestamp")

        _require_verified_customer_account(db, return_record.customer_id)
    elif order.channel != "ECOM":
        raise ValueError("Only ECOM and POS orders can be returned")
    elif system_actor:
        raise ValueError("System processing is only supported for POS returns")
    elif order.customer_id != return_record.customer_id:
        raise ValueError("Order is not attached to the return customer")
    elif staff_user_id is not None:
        raise ValueError("Staff can only process POS returns")

    shipment = None
    if order.channel == "ECOM":
        shipment = db.query(Shipment).filter(Shipment.order_id == order.id).first()
        if shipment is None:
            raise ReturnIntegrityError("Return shipment source not found")

    payment = (
        db.query(Payment)
        .filter(
            Payment.order_id == order.id,
            Payment.status.in_(("PAID", "PARTIALLY_REFUNDED")),
        )
        .order_by(Payment.created_at.desc())
        .with_for_update(skip_locked=True)
        .first()
    )

    if payment is None:
        raise ReturnIntegrityError("Paid payment not found")

    return_items = (
        db.query(ReturnItem)
        .filter(ReturnItem.return_id == return_record.id)
        .order_by(ReturnItem.order_line_unit_id)
        .with_for_update(skip_locked=True)
        .all()
    )

    if not return_items:
        raise ReturnIntegrityError("Return has no items")

    now = datetime.now(timezone.utc)
    refund_amount = Decimal("0.00")
    validated_items: list[tuple[ReturnItem, InventoryUnit]] = []
    return_items_by_allocation_id = {
        return_item.order_line_unit_id: return_item for return_item in return_items
    }

    allocations = (
        db.query(OrderLineUnit, OrderLine, InventoryUnit)
        .join(
            OrderLine,
            OrderLineUnit.order_line_id == OrderLine.id,
        )
        .join(
            InventoryUnit,
            OrderLineUnit.inventory_unit_id == InventoryUnit.id,
        )
        .filter(
            OrderLineUnit.id.in_(return_items_by_allocation_id),
            OrderLine.order_id == order.id,
        )
        .order_by(InventoryUnit.id)
        .with_for_update(skip_locked=True)
        .all()
    )
    if len(allocations) != len(return_items):
        raise ReturnIntegrityError("Return item does not belong to this order")

    for order_line_unit, order_line, inventory_unit in allocations:
        return_item = return_items_by_allocation_id.get(order_line_unit.id)
        if return_item is None:
            raise ReturnIntegrityError("Return item does not belong to this order")

        if inventory_unit.status != "SOLD":
            raise ReturnIntegrityError("Returned inventory unit is not currently sold")

        unit_refund = (order_line.total_gross / order_line.quantity).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        refund_amount += unit_refund
        validated_items.append((return_item, inventory_unit))

    previous_refunds = (
        db.query(Refund)
        .filter(
            Refund.payment_id == payment.id,
            Refund.status == "REFUNDED",
        )
        .all()
    )

    already_refunded = sum(
        (refund.amount for refund in previous_refunds),
        Decimal("0.00"),
    )

    if already_refunded + refund_amount > payment.amount:
        raise ReturnIntegrityError("Refund exceeds the original payment")

    refund_status = (
        "REFUNDED"
        if already_refunded + refund_amount == payment.amount
        else "PARTIALLY_REFUNDED"
    )

    if order.channel == "POS":
        restock_locations = _get_pos_sale_source_locations(
            db,
            order.id,
            [inventory_unit.id for _, inventory_unit in validated_items],
        )
    else:
        if shipment is None:
            raise ReturnIntegrityError("Return shipment source not found")

        restock_locations = {
            inventory_unit.id: shipment.source_location_id
            for _, inventory_unit in validated_items
        }

    for return_item, inventory_unit in validated_items:
        target_location_id = restock_locations[inventory_unit.id]
        inventory_unit.location_id = target_location_id
        inventory_unit.status = "IN_STOCK"
        return_item.inspection_result = "RESTOCK"

        db.add(
            InventoryMovement(
                inventory_unit_id=inventory_unit.id,
                movement_type="RETURN",
                from_location_id=None,
                to_location_id=target_location_id,
                from_status="SOLD",
                to_status="IN_STOCK",
                order_id=order.id,
                return_id=return_record.id,
                actor_type="SYSTEM",
                note="Automated return inspection: restocked",
            )
        )

    db.add(
        Refund(
            return_id=return_record.id,
            payment_id=payment.id,
            amount=refund_amount,
            method="ORIGINAL_PAYMENT",
            provider_ref=f"sim_ref_{uuid4().hex}",
            status="REFUNDED",
            completed_at=now,
        )
    )

    payment.status = refund_status
    order.payment_status = refund_status

    return_record.status = "COMPLETED"
    return_record.completed_outcome = return_record.requested_outcome
    return_record.received_at = now
    return_record.inspected_at = now
    return_record.completed_at = now

    audit_details = {
        "amount": str(refund_amount),
        "item_count": len(return_items),
        "payment_status": payment.status,
    }
    if system_actor:
        record_audit_event(
            db,
            actor_type="SYSTEM",
            action_code="SYSTEM_POS_RETURN_PROCESSED",
            outcome="SUCCEEDED",
            target_type="RETURN",
            target_id=return_record.id,
            details=audit_details,
        )
    elif staff_user_id is not None:
        record_audit_event(
            db,
            actor_type="STAFF",
            staff_user_id=staff_user_id,
            action_code="STAFF_POS_RETURN_PROCESSED",
            outcome="SUCCEEDED",
            target_type="RETURN",
            target_id=return_record.id,
            details=audit_details,
        )
    else:
        if acting_customer_id is None:
            raise ValueError("Customer return actor is missing")

        record_audit_event(
            db,
            actor_type="CUSTOMER",
            customer_id=acting_customer_id,
            action_code="CUSTOMER_RETURN_PROCESSED",
            outcome="SUCCEEDED",
            target_type="RETURN",
            target_id=return_record.id,
            details=audit_details,
        )
    commit_or_rollback(db)

    return {
        "return_id": str(return_record.id),
        "order_id": str(order.id),
        "status": return_record.status,
        "completed_outcome": return_record.completed_outcome,
        "payment_status": payment.status,
        "returned_item_count": len(return_items),
        "financial_outcome": return_record.completed_outcome,
        "amount": str(refund_amount),
    }
