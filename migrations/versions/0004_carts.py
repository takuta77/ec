"""carts and cart_items

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE cart_status AS ENUM ('open','submitted','ordered','failed')")
    op.create_table(
        "carts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.Enum("open", "submitted", "ordered", "failed", name="cart_status", create_type=False), nullable=False, server_default="open"),
        sa.Column("checkout_request_id", UUID(as_uuid=True), nullable=True),
        sa.Column("order_id", UUID(as_uuid=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_carts_user_open",
        "carts",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "uq_carts_checkout_request_id",
        "carts",
        ["checkout_request_id"],
        unique=True,
        postgresql_where=sa.text("checkout_request_id IS NOT NULL"),
    )
    op.create_table(
        "cart_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("cart_id", UUID(as_uuid=True), sa.ForeignKey("carts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("unit_price_cents", sa.Integer, nullable=False),
        sa.UniqueConstraint("cart_id", "item_id", name="uq_cart_items_cart_item"),
    )


def downgrade() -> None:
    op.drop_table("cart_items")
    op.drop_index("uq_carts_checkout_request_id", table_name="carts")
    op.drop_index("ix_carts_user_open", table_name="carts")
    op.drop_table("carts")
    op.execute("DROP TYPE cart_status")
