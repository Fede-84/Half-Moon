from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.schemas.orders import OrderStatus, PaymentStatus


class POSOrderLineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: UUID
    quantity: int = Field(gt=0)


class CreatePOSCheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[POSOrderLineRequest] = Field(min_length=1)
    customer_id: UUID | None = None
    payment_method: Literal["CASH", "CARD"]


class POSDemoCheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[POSOrderLineRequest] = Field(min_length=1)


class POSCashCheckoutResponse(BaseModel):
    order_id: UUID
    order_number: str
    payment_method: Literal["CASH"]
    order_status: Literal["COMPLETED"]
    payment_status: Literal["PAID"]
    total_gross: Decimal
    currency: str


class POSCardCheckoutResponse(BaseModel):
    order_id: UUID
    order_number: str
    payment_method: Literal["CARD"]
    order_status: Literal["PENDING_PAYMENT"]
    payment_status: Literal["UNPAID"]
    total_gross: Decimal
    currency: str
    reservation_expires_at: datetime
    payment_intent_client_secret: str


POSCheckoutResponse = Annotated[
    POSCashCheckoutResponse | POSCardCheckoutResponse,
    Field(discriminator="payment_method"),
]


class POSOrderStatusResponse(BaseModel):
    order_id: UUID
    order_number: str
    payment_method: Literal["CASH", "CARD"]
    order_status: OrderStatus
    payment_status: PaymentStatus
    total_gross: Decimal
    currency: str
    reservation_expires_at: datetime | None = None


class POSReceiptDeliveryResponse(BaseModel):
    order_id: UUID
    order_number: str
    claim_code: str | None
    claim_expires_at: datetime | None


class POSReceiptDeliveryAcknowledgementResponse(BaseModel):
    acknowledged: Literal[True]
    acknowledged_at: datetime


class POSOrderClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_number: str = Field(min_length=1, max_length=30)
    claim_code: str = Field(pattern=r"^HM-\d{8}$")

    @field_validator("order_number", mode="before")
    @classmethod
    def strip_order_number(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class POSOrderClaimResponse(BaseModel):
    claimed: Literal[True]
    order_id: UUID
    order_number: str
    claimed_at: datetime


class POSClaimResetResponse(BaseModel):
    reset: Literal[True]
    failed_attempt_count: Literal[0]
    reset_at: datetime


class POSClaimResetByOrderNumberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_number: str = Field(min_length=1, max_length=30)

    @field_validator("order_number", mode="before")
    @classmethod
    def strip_order_number(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
