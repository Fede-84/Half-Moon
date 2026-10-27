from uuid import UUID

from pydantic import BaseModel, Field


class InventoryTransferRequest(BaseModel):
    variant_id: UUID
    source_location_id: UUID
    destination_location_id: UUID
    quantity: int = Field(gt=0)
    note: str | None = Field(default=None, max_length=500)
