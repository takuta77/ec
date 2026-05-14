"""Names of consumer queues whose `<queue>.dlq` admin should surface.

Add entries here as new consumers are introduced. Currently:
- ec.api.order-events (app/workers/order_consumer.py)
"""

from __future__ import annotations

KNOWN_CONSUMER_QUEUES: tuple[str, ...] = ("ec.api.order-events",)
