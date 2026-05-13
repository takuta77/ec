from __future__ import annotations

import uuid
from pydantic import BaseModel, ConfigDict, Field


class AddItemIn(BaseModel):
    item_id: uuid.UUID
    quantity: int = Field(ge=1, le=999)


class CartLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    item_id: uuid.UUID
    quantity: int
    unit_price_cents: int


class CartOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    failure_reason: str | None = None
    lines: list[CartLineOut] = []


class CheckoutOut(BaseModel):
    checkout_request_id: uuid.UUID
