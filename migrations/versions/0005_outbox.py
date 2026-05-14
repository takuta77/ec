"""outbox_events

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("aggregate_type", sa.String(50), nullable=False),
        sa.Column("aggregate_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("headers", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("dead_letter_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_outbox_unpublished",
        "outbox_events",
        ["created_at"],
        postgresql_where=sa.text("published_at IS NULL AND dead_letter_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_unpublished", table_name="outbox_events")
    op.drop_table("outbox_events")
