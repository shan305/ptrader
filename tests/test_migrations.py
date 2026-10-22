"""Schema migrations and database integrity constraints."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from conftest import API, make_settings
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from test_accounting import assert_books_balance

from ptrader.db import Base, make_engine
from ptrader.main import create_app
from ptrader.migrate import current_revision, downgrade_database, head_revision, upgrade_database

ROOT = Path(__file__).resolve().parent.parent
T = "'2026-10-01 12:00:00.000000'"

# What PTrader 0.1 left behind after: deposit 10000, buy 1 EURUSD, close with +100,
# and a close order rejected for a position that never existed.
LEGACY_ROWS = [
    "INSERT INTO symbols VALUES ('EURUSD', 'Euro vs US Dollar', 'EUR', 'USD', '100000', 5, '0.01', '100', '0.01', 1)",
    f"INSERT INTO quotes VALUES (1, 'EURUSD', '1.1011', '1.1012', {T})",
    f"INSERT INTO accounts VALUES (1, 'Legacy', 'USD', '10100.00', 100, 1, {T}, {T})",
    "INSERT INTO positions VALUES"
    f" (1, 1, 'EURUSD', 'buy', 'closed', '0', '1', '1.1001', '0', '100.00', {T}, '1.1011', {T})",
    f"INSERT INTO orders VALUES (1, 1, 'EURUSD', 'buy', 'market', '1', 'filled', '1.1001', 1, NULL, NULL, {T})",
    f"INSERT INTO orders VALUES (2, 1, 'EURUSD', 'sell', 'market', '1', 'filled', '1.1011', 1, NULL, NULL, {T})",
    f"INSERT INTO orders VALUES (3, 1, 'EURUSD', 'sell', 'market', '1', 'rejected', NULL, 999, 'not found', NULL, {T})",
    "INSERT INTO deals VALUES"
    f" (1, 1, 'balance', NULL, NULL, NULL, NULL, NULL, NULL, '10000.00', 'Initial deposit', {T})",
    f"INSERT INTO deals VALUES (2, 1, 'buy', 'in', 1, 1, 'EURUSD', '1', '1.1001', '0', NULL, {T})",
    f"INSERT INTO deals VALUES (3, 1, 'sell', 'out', 2, 1, 'EURUSD', '1', '1.1011', '100.00', NULL, {T})",
]


def make_legacy_database(url: str, extra_rows: tuple[str, ...] = ()) -> None:
    """An unversioned database with the 0.1 schema and data, as create_all produced it."""
    engine = make_engine(url)
    upgrade_database(engine, "0001")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(text("DROP TABLE alembic_version"))
        for row in (*LEGACY_ROWS, *extra_rows):
            connection.execute(text(row))
    engine.dispose()


def snapshot(url: str) -> dict:
    engine = make_engine(url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = sorted(inspector.get_table_names())
        result = {
            "tables": tables,
            "quote_columns": [c["name"] for c in inspector.get_columns("quotes")],
            "rows": {t: connection.execute(text(f"SELECT * FROM {t} ORDER BY 1")).all() for t in tables},
        }
    engine.dispose()
    return result


def test_fresh_database_matches_the_models():
    engine = make_engine("sqlite://")
    upgrade_database(engine)
    assert current_revision(engine) == head_revision()
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_downgrade_and_upgrade_round_trip():
    engine = make_engine("sqlite://")
    upgrade_database(engine)
    downgrade_database(engine, "0001")
    assert "ledger_entries" not in inspect(engine).get_table_names()
    downgrade_database(engine, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    upgrade_database(engine)
    assert current_revision(engine) == head_revision()


def test_legacy_database_is_adopted_and_backfilled(db_url):
    make_legacy_database(db_url)
    with TestClient(create_app(make_settings(database_url=db_url))) as client:
        assert current_revision(client.app.state.engine) == head_revision()

        entries = client.get(f"{API}/accounts/1/ledger").json()
        assert [(e["kind"], e["amount"], e["balance_after"], e["deal_id"]) for e in entries] == [
            ("deposit", 10000, 10000, 1),
            ("realized_pnl", 100, 10100, 3),
        ]
        orders = client.get(f"{API}/accounts/1/orders").json()
        assert [o["position_id"] for o in orders] == [1, 1, None]
        assert client.get(f"{API}/quotes/EURUSD").json()["source"] == "api"
        assert len(client.get(f"{API}/symbols").json()) == 7  # missing defaults were seeded
        assert_books_balance(client, 1)

        position = client.post(
            f"{API}/orders", json={"account_id": 1, "symbol": "EURUSD", "side": "sell", "volume": 2}
        ).json()["position"]
        client.post(f"{API}/positions/{position['id']}/close")
        assert_books_balance(client, 1)


def test_failed_migration_leaves_database_untouched(db_url):
    # 0.1 had no CHECK constraints, so an inverted quote could exist; 0002 refuses it.
    make_legacy_database(db_url, (f"INSERT INTO quotes VALUES (2, 'EURUSD', '1.2', '1.1', {T})",))
    before = snapshot(db_url)
    assert "alembic_version" not in before["tables"]

    with pytest.raises(IntegrityError, match="ask_not_below_bid"):
        with TestClient(create_app(make_settings(database_url=db_url))):
            pass
    assert snapshot(db_url) == before


def test_alembic_cli_uses_the_configured_database(tmp_path):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    env = {**os.environ, "PTRADER_DATABASE_URL": url}
    run = lambda *args: subprocess.run(  # noqa: E731
        [sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True, check=True
    )
    run("upgrade", "head")
    assert "0002 (head)" in run("current").stdout
    assert current_revision(make_engine(url)) == head_revision()


VIOLATIONS = [
    (
        "INSERT INTO quotes (symbol, bid, ask, time, source) VALUES ('EURUSD', '1.2', '1.1', {T}, 'x')",
        "ask_not_below_bid",
    ),
    ("INSERT INTO quotes (symbol, bid, ask, time, source) VALUES ('NOPE', '1', '1', {T}, 'x')", "FOREIGN KEY"),
    (
        "INSERT INTO accounts (name, currency, balance, leverage, is_active, created_at, updated_at)"
        " VALUES ('x', 'USD', '0', 0, 1, {T}, {T})",
        "leverage_range",
    ),
    (
        "INSERT INTO symbols VALUES ('BAD', '', 'AAA', 'BBB', '1', 2, '5', '1', '0.01', 1)",
        "volume_range",
    ),
    (
        "INSERT INTO positions (account_id, symbol, side, status, volume, initial_volume, open_price, margin,"
        " realized_profit, open_time) VALUES (1, 'EURUSD', 'buy', 'closed', '1', '1', '1.1', '0', '0', {T})",
        "status_consistent",
    ),
    (
        "INSERT INTO positions (account_id, symbol, side, status, volume, initial_volume, open_price, margin,"
        " realized_profit, open_time) VALUES (1, 'EURUSD', 'long', 'open', '1', '1', '1.1', '0', '0', {T})",
        "ck_positions_side",
    ),
    (
        "INSERT INTO orders (account_id, symbol, side, type, volume, status, created_at)"
        " VALUES (1, 'EURUSD', 'buy', 'market', '1', 'filled', {T})",
        "status_consistent",
    ),
    (
        "INSERT INTO deals (account_id, type, profit, time) VALUES (1, 'balance', '0', {T})",
        "balance_not_zero",
    ),
    (
        "INSERT INTO deals (account_id, type, entry, profit, time) VALUES (1, 'buy', 'in', '0', {T})",
        "kind_consistent",
    ),
    (
        "INSERT INTO ledger_entries (account_id, deal_id, kind, amount, balance_after, created_at)"
        " VALUES (1, 1, 'withdrawal', '5', '5', {T})",
        "amount_sign",
    ),
]


@pytest.mark.parametrize(("statement", "constraint"), VIOLATIONS, ids=[v[1] for v in VIOLATIONS])
def test_schema_rejects_invalid_rows(client, account, statement, constraint):
    with pytest.raises(IntegrityError, match=constraint):
        with client.app.state.engine.begin() as connection:
            connection.execute(text(statement.format(T=T)))


def test_one_deal_and_one_ledger_entry_per_source(client, account, order):
    order(account["id"], "EURUSD", "buy", 1)
    engine = client.app.state.engine
    with pytest.raises(IntegrityError, match="UNIQUE"):
        with engine.begin() as connection:
            duplicate_deal = (
                "INSERT INTO deals SELECT NULL, account_id, type, entry, order_id, position_id,"
                " symbol, volume, price, profit, comment, time FROM deals WHERE order_id = 1"
            )
            connection.execute(text(duplicate_deal))
    with pytest.raises(IntegrityError, match="UNIQUE"):
        with engine.begin() as connection:
            second_entry = (
                "INSERT INTO ledger_entries (account_id, deal_id, kind, amount, balance_after, created_at)"
                f" VALUES (1, 1, 'deposit', '1', '1', {T})"
            )
            connection.execute(text(second_entry))
