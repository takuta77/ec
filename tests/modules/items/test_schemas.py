from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.items.schemas import ItemCreate


def test_item_create_accepts_category() -> None:
    payload = ItemCreate(name="Tea", price_cents=100, currency="JPY", category="beverages")
    assert payload.category == "beverages"


def test_item_create_category_optional() -> None:
    payload = ItemCreate(name="Tea", price_cents=100, currency="JPY")
    assert payload.category is None


def test_item_create_rejects_empty_category() -> None:
    with pytest.raises(ValidationError):
        ItemCreate(name="Tea", price_cents=100, currency="JPY", category="")


def test_item_create_rejects_overlong_category() -> None:
    with pytest.raises(ValidationError):
        ItemCreate(name="Tea", price_cents=100, currency="JPY", category="x" * 51)
