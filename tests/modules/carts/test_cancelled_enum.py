
from app.modules.carts.models import CartStatus


def test_cancelled_enum_value_exists():
    assert CartStatus.cancelled.value == "cancelled"
    assert "cancelled" in {s.value for s in CartStatus}
