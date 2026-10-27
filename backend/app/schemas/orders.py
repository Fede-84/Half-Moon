from datetime import datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


OrderStatus = Literal[
    "PENDING_PAYMENT",
    "PAID",
    "CANCELLED",
    "EXPIRED",
    "READY_TO_SHIP",
    "READY_FOR_PICKUP",
    "SHIPPED",
    "DELIVERED",
    "PICKED_UP",
    "COMPLETED",
]

PaymentStatus = Literal[
    "UNPAID",
    "PAID",
    "FAILED",
    "PARTIALLY_REFUNDED",
    "REFUNDED",
]

ShipmentStatus = Literal[
    "READY_TO_SHIP",
    "READY_FOR_PICKUP",
    "SHIPPED",
    "DELIVERED",
    "PICKED_UP",
    "CANCELLED",
    "FAILED",
]


class OrderLineRequest(BaseModel):
    variant_id: UUID
    quantity: int = Field(gt=0)


class ShippingAddressRequest(BaseModel):
    recipient_name: str = Field(min_length=1, max_length=200)
    address_line_1: str = Field(min_length=1, max_length=200)
    address_line_2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    postal_code: str = Field(min_length=1, max_length=20)
    country_code: str = Field(min_length=2, max_length=2)
    phone: str | None = Field(default=None, max_length=50)

    @field_validator("country_code")
    @classmethod
    def normalize_country_code(cls, value: str) -> str:
        return value.strip().upper()


class CreateOrderRequest(BaseModel):
    lines: list[OrderLineRequest] = Field(min_length=1)
    fulfillment_method: Literal["SHIPPING", "STORE_PICKUP"] = "SHIPPING"
    shipping_address: ShippingAddressRequest | None = None

    @model_validator(mode="before")
    @classmethod
    def discard_pickup_shipping_address(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value

        request_data = value.copy()
        if request_data.get("fulfillment_method", "SHIPPING") == "STORE_PICKUP":
            request_data.pop("shipping_address", None)

        return request_data

    @model_validator(mode="after")
    def require_shipping_address_for_shipping(self) -> Self:
        if self.fulfillment_method == "SHIPPING" and self.shipping_address is None:
            raise ValueError("shipping_address is required for SHIPPING orders")
        return self


class GuestCreateOrderRequest(CreateOrderRequest):
    email: EmailStr
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    recipient_is_buyer: bool


class PendingOrderResponse(BaseModel):
    id: UUID
    order_number: str
    order_status: OrderStatus
    payment_status: PaymentStatus
    currency: str
    total_gross: Decimal
    reservation_expires_at: datetime
    reserved_quantity: int
    checkout_url: str


class CheckoutPaymentStatusResponse(BaseModel):
    order_status: OrderStatus
    payment_status: PaymentStatus


class PaymentResponse(BaseModel):
    payment_id: UUID
    order_id: UUID
    order_number: str
    order_status: OrderStatus
    payment_status: PaymentStatus
    amount: Decimal
    currency: str
    failure_reason: str | None = None


class CustomerOrderSummaryResponse(BaseModel):
    order_id: UUID
    order_number: str
    order_status: OrderStatus
    payment_status: PaymentStatus
    currency: str
    total_gross: str
    created_at: datetime


class OrderLineUnitDetailsResponse(BaseModel):
    order_line_unit_id: UUID
    already_returned: bool


class OrderLineDetailsResponse(BaseModel):
    order_line_id: UUID
    product_name: str
    variant: dict[str, object]
    image_url: str
    sku: str
    quantity: int
    unit_price_gross: str
    total_gross: str
    units: list[OrderLineUnitDetailsResponse]


class OrderDetailsResponse(BaseModel):
    order_id: UUID
    order_number: str
    channel: Literal["ECOM", "POS"]
    order_status: OrderStatus
    payment_status: PaymentStatus
    currency: str
    total_gross: str
    created_at: datetime
    paid_at: datetime | None
    lines: list[OrderLineDetailsResponse]


class CustomerOrderShipmentStatusResponse(BaseModel):
    shipment_id: UUID
    order_id: UUID
    order_number: str
    status: ShipmentStatus
    order_status: OrderStatus
    mock_tracking_number: str | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    ready_for_pickup_at: datetime | None
    pickup_deadline_at: datetime | None
    picked_up_at: datetime | None
