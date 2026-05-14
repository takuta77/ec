from __future__ import annotations


def error_envelope(
    *,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
    trace_id: str | None = None,
) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "trace_id": trace_id,
        }
    }
