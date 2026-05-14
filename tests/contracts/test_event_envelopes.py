import json
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator


CONTRACT_DIR = Path(__file__).parent


def _load(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((CONTRACT_DIR / name).read_text()))


def test_checkout_requested_envelope_matches_schema():
    from app.modules.carts.events import build_checkout_requested
    from app.modules.carts.models import Cart, CartItem, CartStatus
    from app.modules.items.models import Item

    cart = Cart(id=uuid.uuid4(), user_id=uuid.uuid4(), status=CartStatus.submitted)
    item = Item(id=uuid.uuid4(), name="x", price_cents=100, currency="JPY", is_active=True)
    line = CartItem(id=uuid.uuid4(), cart_id=cart.id, item_id=item.id, quantity=2, unit_price_cents=100)
    envelope = build_checkout_requested(cart=cart, lines=[(line, item)], checkout_request_id=uuid.uuid4())
    _load("checkout.requested.schema.json").validate(envelope)


def test_order_created_handler_accepts_schema_compliant_envelope():
    schema = _load("order.created.schema.json")
    example = {
        "event_id": str(uuid.uuid4()),
        "event_type": "order.created",
        "occurred_at": "2026-05-12T03:21:00Z",
        "data": {
            "checkout_request_id": str(uuid.uuid4()),
            "order_id": str(uuid.uuid4()),
            "status": "confirmed",
            "confirmed_at": "2026-05-12T03:21:05Z",
        },
    }
    schema.validate(example)
