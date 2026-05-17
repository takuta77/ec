from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ItemsStats(BaseModel):
    total: int
    active: int
    by_category: dict[str, int]


class CartsStats(BaseModel):
    by_status: dict[str, int]
    failed_with_timeout: int


class OutboxStats(BaseModel):
    pending: int
    dispatched: int
    oldest_pending_at: datetime | None


class DLQQueueStats(BaseModel):
    queue: str
    message_count: int


class CartAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    user_id: uuid.UUID
    status: str
    failure_reason: str | None
    submitted_at: datetime | None
    line_count: int
    created_at: datetime
    updated_at: datetime


class DLQMessageOut(BaseModel):
    event_id: str | None
    routing_key: str | None
    death_count: int
    body_preview: str
