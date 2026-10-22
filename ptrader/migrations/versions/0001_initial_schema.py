"""Initial schema, identical to what PTrader 0.1 created with create_all.

Revision ID: 0001
Revises:
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECIMAL = sa.String(40)
ENUM = sa.String(16)


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("balance", DECIMAL, nullable=False),
        sa.Column("leverage", sa.Integer, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "symbols",
        sa.Column("name", sa.String(20), primary_key=True),
        sa.Column("description", sa.String(100), nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("profit_currency", sa.String(3), nullable=False),
        sa.Column("contract_size", DECIMAL, nullable=False),
        sa.Column("digits", sa.Integer, nullable=False),
        sa.Column("min_volume", DECIMAL, nullable=False),
        sa.Column("max_volume", DECIMAL, nullable=False),
        sa.Column("volume_step", DECIMAL, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
    )
    op.create_table(
        "quotes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("symbol", sa.String(20), sa.ForeignKey("symbols.name"), nullable=False),
        sa.Column("bid", DECIMAL, nullable=False),
        sa.Column("ask", DECIMAL, nullable=False),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_quotes_symbol_id", "quotes", ["symbol", "id"])
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("symbol", sa.String(20), sa.ForeignKey("symbols.name"), nullable=False),
        sa.Column("side", ENUM, nullable=False),
        sa.Column("status", ENUM, nullable=False),
        sa.Column("volume", DECIMAL, nullable=False),
        sa.Column("initial_volume", DECIMAL, nullable=False),
        sa.Column("open_price", DECIMAL, nullable=False),
        sa.Column("margin", DECIMAL, nullable=False),
        sa.Column("realized_profit", DECIMAL, nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_price", DECIMAL, nullable=True),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_positions_account_id", "positions", ["account_id"])
    op.create_index("ix_positions_status", "positions", ["status"])
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("symbol", sa.String(20), sa.ForeignKey("symbols.name"), nullable=False),
        sa.Column("side", ENUM, nullable=False),
        sa.Column("type", ENUM, nullable=False),
        sa.Column("volume", DECIMAL, nullable=False),
        sa.Column("status", ENUM, nullable=False),
        sa.Column("price", DECIMAL, nullable=True),
        sa.Column("position_id", sa.Integer, sa.ForeignKey("positions.id"), nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("comment", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_orders_account_id", "orders", ["account_id"])
    op.create_table(
        "deals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("type", ENUM, nullable=False),
        sa.Column("entry", ENUM, nullable=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("position_id", sa.Integer, sa.ForeignKey("positions.id"), nullable=True),
        sa.Column("symbol", sa.String(20), sa.ForeignKey("symbols.name"), nullable=True),
        sa.Column("volume", DECIMAL, nullable=True),
        sa.Column("price", DECIMAL, nullable=True),
        sa.Column("profit", DECIMAL, nullable=False),
        sa.Column("comment", sa.String(255), nullable=True),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_deals_account_id", "deals", ["account_id"])


def downgrade() -> None:
    for table in ("deals", "orders", "positions", "quotes", "symbols", "accounts"):
        op.drop_table(table)
