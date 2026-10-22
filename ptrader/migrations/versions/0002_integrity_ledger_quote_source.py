"""Integrity constraints, account ledger and quote source.

- CHECK constraints for value ranges, enum values and record consistency
- one deal per order
- quotes.source and a (symbol, time) index for history queries
- ledger_entries, backfilled from existing balance deals and realized profits

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22
"""

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def num(column: str) -> str:
    return f"CAST({column} AS NUMERIC)"


# table -> {constraint name: SQL}; names get the ck_<table>_ prefix from the naming convention.
CHECKS: dict[str, dict[str, str]] = {
    "accounts": {
        "name_not_empty": "length(name) > 0",
        "currency_code": "length(currency) = 3",
        "leverage_range": "leverage BETWEEN 1 AND 1000",
    },
    "symbols": {
        "contract_size_positive": f"{num('contract_size')} > 0",
        "digits_range": "digits BETWEEN 0 AND 8",
        "volume_step_positive": f"{num('volume_step')} > 0",
        "min_volume_positive": f"{num('min_volume')} > 0",
        "volume_range": f"{num('max_volume')} >= {num('min_volume')}",
        "currency_codes": "length(base_currency) = 3 AND length(profit_currency) = 3",
    },
    "quotes": {
        "bid_positive": f"{num('bid')} > 0",
        "ask_not_below_bid": f"{num('ask')} >= {num('bid')}",
    },
    "positions": {
        "side": "side IN ('buy', 'sell')",
        "status": "status IN ('open', 'closed')",
        "initial_volume_positive": f"{num('initial_volume')} > 0",
        "volume_range": f"{num('volume')} >= 0 AND {num('volume')} <= {num('initial_volume')}",
        "open_price_positive": f"{num('open_price')} > 0",
        "margin_not_negative": f"{num('margin')} >= 0",
        "status_consistent": (
            f"(status = 'open' AND {num('volume')} > 0 AND close_time IS NULL AND close_price IS NULL)"
            f" OR (status = 'closed' AND {num('volume')} = 0 AND {num('margin')} = 0"
            " AND close_time IS NOT NULL AND close_price IS NOT NULL)"
        ),
    },
    "orders": {
        "side": "side IN ('buy', 'sell')",
        "type": "type IN ('market')",
        "status": "status IN ('filled', 'rejected')",
        "volume_positive": f"{num('volume')} > 0",
        "status_consistent": (
            f"(status = 'filled' AND {num('price')} > 0 AND position_id IS NOT NULL AND reason IS NULL)"
            " OR (status = 'rejected' AND price IS NULL AND reason IS NOT NULL)"
        ),
    },
    "deals": {
        "type": "type IN ('buy', 'sell', 'balance')",
        "entry": "entry IS NULL OR entry IN ('in', 'out')",
        "kind_consistent": (
            "(type = 'balance' AND entry IS NULL AND order_id IS NULL AND position_id IS NULL"
            " AND symbol IS NULL AND volume IS NULL AND price IS NULL)"
            " OR (type IN ('buy', 'sell') AND entry IS NOT NULL AND order_id IS NOT NULL"
            f" AND position_id IS NOT NULL AND symbol IS NOT NULL AND {num('volume')} > 0 AND {num('price')} > 0)"
        ),
        "balance_not_zero": f"type <> 'balance' OR {num('profit')} <> 0",
        "entry_has_no_profit": f"entry IS NULL OR entry <> 'in' OR {num('profit')} = 0",
    },
}


def upgrade() -> None:
    # 0.1 stored rejected close orders with the requested position id even when it did not exist.
    op.execute(
        "UPDATE orders SET position_id = NULL"
        " WHERE position_id IS NOT NULL AND position_id NOT IN (SELECT id FROM positions)"
    )

    for table, checks in CHECKS.items():
        with op.batch_alter_table(table, recreate="always") as batch:
            if table == "quotes":
                batch.add_column(sa.Column("source", sa.String(32), nullable=False, server_default="api"))
                batch.create_index("ix_quotes_symbol_time", ["symbol", "time"])
            if table == "deals":
                batch.create_unique_constraint("uq_deals_order_id", ["order_id"])
            for name, sql in checks.items():
                batch.create_check_constraint(name, sql)

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("deal_id", sa.Integer, sa.ForeignKey("deals.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount", sa.String(40), nullable=False),
        sa.Column("balance_after", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("deal_id", name="uq_ledger_entries_deal_id"),
        sa.CheckConstraint("kind IN ('deposit', 'withdrawal', 'realized_pnl')", name="kind"),
        sa.CheckConstraint(
            f"(kind = 'deposit' AND {num('amount')} > 0)"
            f" OR (kind = 'withdrawal' AND {num('amount')} < 0)"
            f" OR (kind = 'realized_pnl' AND {num('amount')} <> 0)",
            name="amount_sign",
        ),
    )
    op.create_index("ix_ledger_entries_account_id", "ledger_entries", ["account_id"])
    _backfill_ledger()


def _backfill_ledger() -> None:
    """Rebuild each account's balance history from its deals, in deal order."""
    bind = op.get_bind()
    deals = bind.execute(
        sa.text(
            "SELECT id, account_id, type, entry, profit, time FROM deals"
            " WHERE type = 'balance' OR (entry = 'out' AND CAST(profit AS NUMERIC) <> 0)"
            " ORDER BY id"
        )
    ).all()
    balances: dict[int, Decimal] = {}
    rows = []
    for deal_id, account_id, deal_type, _, profit, time in deals:
        amount = Decimal(profit)
        if deal_type == "balance":
            kind = "deposit" if amount > 0 else "withdrawal"
        else:
            kind = "realized_pnl"
        balances[account_id] = balances.get(account_id, Decimal(0)) + amount
        rows.append(
            {
                "account_id": account_id,
                "deal_id": deal_id,
                "kind": kind,
                "amount": format(amount, "f"),
                "balance_after": format(balances[account_id], "f"),
                "created_at": time,
            }
        )
    if rows:
        # Plain SQL so the stored deal timestamps are copied verbatim.
        bind.execute(
            sa.text(
                "INSERT INTO ledger_entries (account_id, deal_id, kind, amount, balance_after, created_at)"
                " VALUES (:account_id, :deal_id, :kind, :amount, :balance_after, :created_at)"
            ),
            rows,
        )


def downgrade() -> None:
    op.drop_index("ix_ledger_entries_account_id", "ledger_entries")
    op.drop_table("ledger_entries")
    for table, checks in CHECKS.items():
        with op.batch_alter_table(table, recreate="always") as batch:
            for name in checks:
                batch.drop_constraint(name, type_="check")
            if table == "quotes":
                batch.drop_index("ix_quotes_symbol_time")
                batch.drop_column("source")
            if table == "deals":
                batch.drop_constraint("uq_deals_order_id", type_="unique")
