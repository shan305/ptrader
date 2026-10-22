import pytest
from conftest import API


def test_buy_fills_at_ask_and_opens_position(client, account, order, set_quote, get_account):
    set_quote("EURUSD", 1.10000, 1.10010)
    result = order(account["id"], "EURUSD", "buy", 1)

    assert result["order"]["status"] == "filled"
    assert result["order"]["price"] == 1.1001
    assert result["deal"]["entry"] == "in"
    assert result["deal"]["price"] == 1.1001
    position = result["position"]
    assert position["status"] == "open"
    assert position["side"] == "buy"
    assert position["open_price"] == 1.1001
    assert position["margin"] == 1100.10  # 1 lot * 100000 * 1.1001 / 100
    assert position["current_price"] == 1.1
    assert position["floating_pnl"] == -10  # spread cost

    summary = get_account(account["id"])
    assert summary["balance"] == 10000
    assert summary["equity"] == 9990
    assert summary["margin"] == 1100.10
    assert summary["free_margin"] == 8889.90
    assert summary["open_positions"] == 1


def test_sell_fills_at_bid(account, order, set_quote):
    set_quote("GBPUSD", 1.25000, 1.25020)
    result = order(account["id"], "GBPUSD", "sell", 0.5)
    assert result["order"]["price"] == 1.25
    assert result["position"]["current_price"] == 1.2502
    assert result["position"]["floating_pnl"] == -10


def test_floating_pnl_follows_quotes(client, account, order, set_quote, get_account):
    set_quote("EURUSD", 1.10000, 1.10010)
    buy = order(account["id"], "EURUSD", "buy", 1)["position"]
    sell = order(account["id"], "EURUSD", "sell", 2)["position"]

    set_quote("EURUSD", 1.10110, 1.10120)
    buy_now = client.get(f"{API}/positions/{buy['id']}").json()
    sell_now = client.get(f"{API}/positions/{sell['id']}").json()
    assert buy_now["floating_pnl"] == 100  # (1.10110 - 1.10010) * 100000
    assert sell_now["floating_pnl"] == -240  # (1.10000 - 1.10120) * 200000

    summary = get_account(account["id"])
    assert summary["floating_pnl"] == -140
    assert summary["equity"] == 9860


def test_close_realizes_profit(client, account, order, set_quote, get_account):
    set_quote("EURUSD", 1.10000, 1.10010)
    position = order(account["id"], "EURUSD", "buy", 1)["position"]
    set_quote("EURUSD", 1.10110, 1.10120)

    response = client.post(f"{API}/positions/{position['id']}/close")
    assert response.status_code == 200
    result = response.json()
    assert result["order"]["side"] == "sell"
    assert result["order"]["price"] == 1.1011
    assert result["deal"]["entry"] == "out"
    assert result["deal"]["profit"] == 100
    closed = result["position"]
    assert closed["status"] == "closed"
    assert closed["volume"] == 0
    assert closed["close_price"] == 1.1011
    assert closed["realized_profit"] == 100
    assert closed["floating_pnl"] is None

    summary = get_account(account["id"])
    assert summary["balance"] == 10100
    assert summary["equity"] == 10100
    assert summary["margin"] == 0
    assert summary["open_positions"] == 0


def test_close_through_orders_endpoint(client, account, order, set_quote, get_account):
    set_quote("EURUSD", 1.10000, 1.10010)
    position = order(account["id"], "EURUSD", "sell", 1)["position"]
    set_quote("EURUSD", 1.09900, 1.09910)

    result = order(account["id"], "EURUSD", "buy", 1, position_id=position["id"])
    assert result["order"]["price"] == 1.0991  # a sell position is closed at the ask
    assert result["deal"]["profit"] == 90
    assert get_account(account["id"])["balance"] == 10090


def test_partial_close(client, account, order, set_quote, get_account):
    set_quote("EURUSD", 1.10000, 1.10010)
    position = order(account["id"], "EURUSD", "buy", 1)["position"]
    set_quote("EURUSD", 1.10210, 1.10220)

    first = client.post(f"{API}/positions/{position['id']}/close", json={"volume": 0.4}).json()
    assert first["deal"]["profit"] == 80
    assert first["position"]["status"] == "open"
    assert first["position"]["volume"] == 0.6
    assert first["position"]["initial_volume"] == 1
    assert first["position"]["margin"] == 660.06
    assert first["position"]["floating_pnl"] == 120

    second = client.post(f"{API}/positions/{position['id']}/close").json()
    assert second["deal"]["volume"] == 0.6
    assert second["position"]["status"] == "closed"
    assert second["position"]["realized_profit"] == 200
    assert get_account(account["id"])["balance"] == 10200


def test_gold_pnl_uses_contract_size(account, order, set_quote, client):
    set_quote("XAUUSD", 2000.00, 2000.50)
    position = order(account["id"], "XAUUSD", "buy", 0.1)["position"]
    assert position["margin"] == 200.05  # 0.1 * 100 * 2000.50 / 100

    set_quote("XAUUSD", 2010.50, 2011.00)
    result = client.post(f"{API}/positions/{position['id']}/close").json()
    assert result["deal"]["profit"] == 100  # 10.00 * 0.1 lot * 100 oz


def test_profit_in_other_currency_is_converted(account, order, set_quote, client):
    set_quote("USDJPY", 150.000, 150.000)
    position = order(account["id"], "USDJPY", "buy", 1)["position"]
    assert position["margin"] == 1000  # 100000 USD / 100 leverage

    set_quote("USDJPY", 151.000, 151.000)
    result = client.post(f"{API}/positions/{position['id']}/close").json()
    assert result["deal"]["profit"] == 662.25  # 100000 JPY / 151


def test_trade_history(client, account, order, set_quote):
    set_quote("EURUSD", 1.10000, 1.10010)
    position = order(account["id"], "EURUSD", "buy", 1)["position"]
    order(account["id"], "EURUSD", "buy", 0.001, expect=422)
    client.post(f"{API}/positions/{position['id']}/close")
    account_id = account["id"]

    orders = client.get(f"{API}/accounts/{account_id}/orders").json()
    assert [(o["side"], o["status"]) for o in orders] == [("buy", "filled"), ("buy", "rejected"), ("sell", "filled")]
    filled = client.get(f"{API}/accounts/{account_id}/orders", params={"status": "filled"}).json()
    assert len(filled) == 2
    assert client.get(f"{API}/orders/{orders[0]['id']}").json()["position_id"] == position["id"]

    deals = client.get(f"{API}/accounts/{account_id}/deals").json()
    assert [(d["type"], d["entry"]) for d in deals] == [("balance", None), ("buy", "in"), ("sell", "out")]

    assert client.get(f"{API}/accounts/{account_id}/positions").json() == []
    closed = client.get(f"{API}/accounts/{account_id}/positions", params={"status": "closed"}).json()
    assert [p["id"] for p in closed] == [position["id"]]
    assert len(client.get(f"{API}/accounts/{account_id}/positions", params={"status": "all"}).json()) == 1


@pytest.mark.parametrize(
    ("volume", "reason"),
    [
        (0.001, "below the minimum"),
        (101, "exceeds the maximum"),
        (0.015, "not a multiple of the volume step"),
    ],
)
def test_volume_rules(client, account, order, volume, reason):
    rejected = order(account["id"], "EURUSD", "buy", volume, expect=422)
    assert reason in rejected["detail"]
    stored = client.get(f"{API}/orders/{rejected['order_id']}").json()
    assert stored["status"] == "rejected"
    assert reason in stored["reason"]
    assert stored["price"] is None


def test_insufficient_margin_is_rejected(client, make_account, order, set_quote, get_account):
    poor = make_account(initial_deposit=500)
    set_quote("EURUSD", 1.1, 1.1)
    rejected = order(poor["id"], "EURUSD", "buy", 1, expect=422)
    assert "Insufficient free margin" in rejected["detail"]
    assert get_account(poor["id"])["open_positions"] == 0
    order(poor["id"], "EURUSD", "buy", 0.4)  # margin 440 fits


def test_disabled_account_and_symbol_are_rejected(client, account, order):
    client.patch(f"{API}/symbols/GBPUSD", json={"enabled": False})
    assert "disabled" in order(account["id"], "GBPUSD", "buy", 0.1, expect=422)["detail"]

    client.patch(f"{API}/accounts/{account['id']}", json={"is_active": False})
    assert "disabled" in order(account["id"], "EURUSD", "buy", 0.1, expect=422)["detail"]


def test_unknown_account_or_symbol(client, account, order):
    order(999, "EURUSD", "buy", 0.1, expect=404)
    order(account["id"], "NOPE", "buy", 0.1, expect=404)
    order(account["id"], "EURUSD", "hold", 0.1, expect=422)
    order(account["id"], "EURUSD", "buy", 0.1, expect=422, type="limit")
    assert client.post(f"{API}/positions/999/close").status_code == 404


def test_invalid_close_requests(client, make_account, order, set_quote):
    owner = make_account(name="Owner")
    other = make_account(name="Other")
    position = order(owner["id"], "EURUSD", "buy", 1)["position"]
    pid = position["id"]

    assert "requires a sell order" in order(owner["id"], "EURUSD", "buy", 1, expect=422, position_id=pid)["detail"]
    assert "not found for account" in order(other["id"], "EURUSD", "sell", 1, expect=422, position_id=pid)["detail"]
    assert "is on EURUSD" in order(owner["id"], "GBPUSD", "sell", 1, expect=422, position_id=pid)["detail"]
    assert "exceeds the open volume" in order(owner["id"], "EURUSD", "sell", 2, expect=422, position_id=pid)["detail"]
    response = client.post(f"{API}/positions/{pid}/close", json={"volume": 0.995})
    assert response.status_code == 422

    assert client.post(f"{API}/positions/{pid}/close").status_code == 200
    assert "already closed" in client.post(f"{API}/positions/{pid}/close", json={"volume": 1}).json()["detail"]


def test_order_without_conversion_rate_is_rejected(client, make_account, order):
    account = make_account(currency="SEK")
    rejected = order(account["id"], "EURUSD", "buy", 0.1, expect=422)
    assert "No conversion rate from USD to SEK" in rejected["detail"]


def test_account_in_other_currency(make_account, order, set_quote):
    account = make_account(currency="EUR", initial_deposit=10000)
    set_quote("EURUSD", 1.25, 1.25)
    set_quote("XAUUSD", 2000, 2000)
    position = order(account["id"], "XAUUSD", "buy", 0.1)["position"]
    assert position["margin"] == 160  # 20000 USD notional / 1.25 / 100
