"""add is_admin flag to users

Revision ID: 0009_users_add_is_admin
Revises: 0008_items_add_category
Create Date: 2026-05-15

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_users_add_is_admin"
down_revision = "0008_items_add_category"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
