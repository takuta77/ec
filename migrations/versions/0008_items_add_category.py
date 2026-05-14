"""add category column to items

Revision ID: 0008_items_add_category
Revises: 0007
Create Date: 2026-05-14

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_items_add_category"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("category", sa.String(length=50), nullable=True))
    op.create_index("ix_items_category", "items", ["category"])


def downgrade() -> None:
    op.drop_index("ix_items_category", table_name="items")
    op.drop_column("items", "category")
