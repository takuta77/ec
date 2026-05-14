from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    price_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    category: str | None = Field(default=None, min_length=1, max_length=50)


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str | None
    price_cents: int
    currency: str
    is_active: bool
    category: str | None
