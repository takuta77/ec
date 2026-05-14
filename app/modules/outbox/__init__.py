"""Outbox module.

Importing this package eagerly registers ORM models on ``Base.metadata`` so
that test fixtures using ``Base.metadata.create_all`` see every table —
including ones whose repository module is only imported lazily inside a test.
"""

from app.modules.outbox import models, processed  # noqa: F401
