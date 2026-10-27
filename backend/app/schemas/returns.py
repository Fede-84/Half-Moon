from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ReturnItemRequest(BaseModel):
    order_line_unit_id: UUID


class CreateReturnRequest(BaseModel):
    requested_outcome: Literal["REFUND"] = "REFUND"
    reason: str = Field(min_length=1, max_length=500)
    items: list[ReturnItemRequest] = Field(min_length=1)


class CustomerOrderReturnItemResponse(BaseModel):
    order_line_unit_id: UUID
    inspection_result: Literal["PENDING", "RESTOCK", "REPAIR", "DAMAGED"]


class CustomerOrderReturnRefundResponse(BaseModel):
    amount: str
    method: Literal["ORIGINAL_PAYMENT"]
    status: Literal["PENDING", "REFUNDED", "FAILED"]
    completed_at: datetime | None


class CustomerOrderReturnStatusResponse(BaseModel):
    return_id: UUID
    order_id: UUID
    status: Literal[
        "REQUESTED",
        "APPROVED",
        "RECEIVED",
        "INSPECTED",
        "COMPLETED",
        "REJECTED",
    ]
    requested_outcome: Literal["REFUND"]
    reason: str
    requested_at: datetime
    completed_at: datetime | None
    items: list[CustomerOrderReturnItemResponse]
    refunds: list[CustomerOrderReturnRefundResponse]


class POSReturnProcessedResponse(BaseModel):
    return_id: UUID
    order_id: UUID
    status: Literal["COMPLETED"]
    completed_outcome: Literal["REFUND"]
    payment_status: Literal["PARTIALLY_REFUNDED", "REFUNDED"]
    returned_item_count: int
    financial_outcome: Literal["REFUND"]
    amount: str
