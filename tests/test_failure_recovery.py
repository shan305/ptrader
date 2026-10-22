"""Failures part-way through a command must leave no partial state and no published events."""

from decimal import Decimal

import pytest
from conftest import API
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from test_accounting import assert_books_balance

from ptrader.errors import InvalidRequest
from ptrader.events import (
    BalanceChanged,
    EventBus,
    OrderFilled,
    OrderRejected,
    PositionOpened,
    PositionReduced,
    QuoteReceived,
)
from ptrader.services import ledger, positions, pricing
from ptrader.services.market_data import RawQuote, latest_quote


@pytest.fixture
def lenient(client):
    """Same app, but unhandled errors become 500 responses instead of raising in the test."""
    return TestClient(client.app, raise_server_exceptions=False)


def history(client, account_id):
    get = lambda path: client.get(f"{API}/accounts/{account_id}{path}").json()  # noqa: E731
    return get("/orders"), get("/deals"), get("/positions?status=all"), get("/ledger"), get("")["balance"]


def test_crash_after_position_is_flushed_rolls_everything_back(
    client, lenient, account, order, monkeypatch, recorded_events
):
    before = history(client, account["id"])
    original = positions.open_position

    def crash(*args, **kwargs):
        original(*args, **kwargs)  # position, order and deal rows are already flushed
        raise RuntimeError("storage failure")

    monkeypatch.setattr(positions, "open_position", crash)
    body = {"account_id": account["id"], "symbol": "EURUSD", "side": "buy", "volume": 1}
    response = lenient.post(f"{API}/orders", json=body)
    assert response.status_code == 500
    assert history(client, account["id"]) == before
    assert recorded_events == []

    monkeypatch.undo()
    assert order(account["id"], "EURUSD", "buy", 1)["order"]["status"] == "filled"
    assert_books_balance(client, account["id"])


def test_crash_while_booking_profit_keeps_position_and_balance(
    client, lenient, account, order, set_quote, monkeypatch, recorded_events
):
    set_quote("EURUSD", 1.1, 1.1)
    position = order(account["id"], "EURUSD", "buy", 1)["position"]
    set_quote("EURUSD", 1.2, 1.2)
    before = history(client, account["id"])
    recorded_events.clear()

    def crash(*args, **kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(ledger, "post", crash)
    assert lenient.post(f"{API}/positions/{position['id']}/close").status_code == 500
    assert history(client, account["id"]) == before
    assert client.get(f"{API}/positions/{position['id']}").json()["volume"] == 1
    assert recorded_events == []

    monkeypatch.undo()
    result = client.post(f"{API}/positions/{position['id']}/close").json()
    assert result["deal"]["profit"] == 10000
    assert assert_books_balance(client, account["id"])["balance"] == 20000


def test_database_constraints_stop_corrupt_writes(client, account, order, monkeypatch):
    """A bug producing a negative margin is refused by the schema, not silently stored."""
    monkeypatch.setattr(pricing, "required_margin", lambda *args: Decimal("-1"))
    with pytest.raises(IntegrityError, match="margin_not_negative"):
        client.post(f"{API}/orders", json={"account_id": account["id"], "symbol": "EURUSD", "side": "buy", "volume": 1})
    assert client.get(f"{API}/accounts/{account['id']}/orders").json() == []

    monkeypatch.undo()
    order(account["id"], "EURUSD", "buy", 1)
    assert_books_balance(client, account["id"])


def test_rejection_keeps_only_the_rejected_order(client, account, order, recorded_events):
    rejected = order(account["id"], "EURUSD", "buy", 500, expect=422)
    orders, deals, positions_, entries, balance = history(client, account["id"])
    assert [o["status"] for o in orders] == ["rejected"]
    assert positions_ == [] and len(deals) == 1 and len(entries) == 1 and balance == 10000
    assert recorded_events == [OrderRejected(rejected["order_id"], account["id"], "EURUSD", rejected["detail"])]


def test_events_are_published_after_commit_in_order(client, account, order, set_quote, recorded_events):
    set_quote("EURUSD", 1.1, 1.1)
    position = order(account["id"], "EURUSD", "buy", 1)["position"]
    set_quote("EURUSD", 1.11, 1.11)
    client.post(f"{API}/positions/{position['id']}/close")

    assert [type(e) for e in recorded_events] == [
        QuoteReceived,
        PositionOpened,
        OrderFilled,
        QuoteReceived,
        BalanceChanged,
        PositionReduced,
        OrderFilled,
    ]
    reduced = recorded_events[5]
    assert (reduced.realized_profit, reduced.remaining_volume) == (Decimal("1000.00"), Decimal("0"))
    assert recorded_events[4] == BalanceChanged(account["id"], "realized_pnl", Decimal("1000.00"), Decimal("11000.00"))


def test_failing_event_handler_does_not_undo_the_trade(client, account, order):
    bus: EventBus = client.app.state.events

    def broken(event):
        raise RuntimeError("subscriber bug")

    bus.subscribe(OrderFilled, broken)
    result = order(account["id"], "EURUSD", "buy", 1)
    assert result["order"]["status"] == "filled"
    assert bus.handler_failures == 1
    assert client.get(f"{API}/accounts/{account['id']}").json()["open_positions"] == 1


def test_quote_batches_are_atomic(client, uow_for, recorded_events):
    app = client.app
    before = client.get(f"{API}/quotes/EURUSD").json()
    batch = [RawQuote("EURUSD", Decimal("1.2"), Decimal("1.2001")), RawQuote("GBPUSD", Decimal("2"), Decimal("1"))]
    with pytest.raises(InvalidRequest):
        app.state.pipeline.submit(uow_for(app), batch)
    assert client.get(f"{API}/quotes/EURUSD").json() == before
    assert recorded_events == []


def test_unit_of_work_recovers_after_a_failed_transaction(client, uow_for):
    uow = uow_for(client.app)
    with pytest.raises(InvalidRequest):
        client.app.state.pipeline.submit(uow, [RawQuote("EURUSD", Decimal("2"), Decimal("1"))])
    quote = client.app.state.pipeline.submit(uow, [RawQuote("EURUSD", Decimal("1.3"), Decimal("1.3001"))])[0]
    assert latest_quote(uow.session, "EURUSD").id == quote.id

    with pytest.raises(RuntimeError, match="inside a transaction"):
        uow.emit(QuoteReceived("EURUSD", Decimal(1), Decimal(1), quote.time, "test"))
    with uow.transaction(), pytest.raises(RuntimeError, match="Nested"):
        with uow.transaction():
            pass
