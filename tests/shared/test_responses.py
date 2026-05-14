from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.shared.responses import error_envelope


def test_app_error_hierarchy():
    assert issubclass(NotFoundError, AppError)
    assert issubclass(ConflictError, AppError)


def test_error_envelope_shape():
    body = error_envelope(
        code="not_found", message="Cart not found", details={"cart_id": "abc"}, trace_id="t-1"
    )
    assert body == {
        "error": {
            "code": "not_found",
            "message": "Cart not found",
            "details": {"cart_id": "abc"},
            "trace_id": "t-1",
        }
    }
