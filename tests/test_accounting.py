"""Trade accounting: ledger, deals, positions and account metrics must always agree."""

from decimal import Decimal

from conftest import API, dec

from ptrader.models import Account
from ptrader.services import ledger


def books(client, account_id):
    get = lambda path: client.get(f"{API}/accounts/{account_id}{path}").json()  # noqa: E731
    return get(""), get("/ledger"), get("/deals"), get("/positions?status=all")


def assert_books_balance(client, account_id):
    """Check every accounting invariant for one account and return its summary."""
    account, entries, deals, positions = books(client, account_id)
    balance = dec(account["balance"])

    # The ledger is a running total ending at the cached balance.
    running = Decimal(0)
    for entry in entries:
        running += dec(entry["amount"])
        assert dec(entry["balance_after"]) == running
    assert running == balance

    # Each ledger entry is backed by exactly the deal that moved the money, and vice versa.
    deals_by_id = {d["id"]: d for d in deals}
    for entry in entries:
        assert dec(deals_by_id[entry["deal_id"]]["profit"]) == dec(entry["amount"])
    assert {e["deal_id"] for e in entries} == {d["id"] for d in deals if dec(d["profit"]) != 0}

    # Positions agree with their deals.
    open_margin = Decimal(0)
    open_floating = Decimal(0)
    for position in positions:
        own = [d for d in deals if d["position_id"] == position["id"]]
        entries_in = [d for d in own if d["entry"] == "in"]
        exits = [d for d in own if d["entry"] == "out"]
        assert len(entries_in) == 1
        assert dec(entries_in[0]["volume"]) == dec(position["initial_volume"])
        assert dec(entries_in[0]["price"]) == dec(position["open_price"])
        closed_volume = sum((dec(d["volume"]) for d in exits), Decimal(0))
        assert dec(position["initial_volume"]) - closed_volume == dec(position["volume"])
        assert sum((dec(d["profit"]) for d in exits), Decimal(0)) == dec(position["realized_profit"])
        if position["status"] == "closed":
            assert dec(position["volume"]) == 0 and dec(position["margin"]) == 0
            assert dec(position["close_price"]) == dec(exits[-1]["price"])
        else:
            open_margin += dec(position["margin"])
            open_floating += dec(position["floating_pnl"])

    assert dec(account["margin"]) == open_margin
    assert dec(account["floating_pnl"]) == open_floating
    assert dec(account["equity"]) == balance + open_floating
    assert dec(account["free_margin"]) == dec(account["equity"]) - open_margin
    return account


def test_mixed_session_keeps_books_balanced(client, make_account, order):
    account = make_account(initial_deposit=50000)
    account_id = account["id"]
    tick = lambda steps: client.post(f"{API}/quotes/simulate", json={"steps": steps})  # noqa: E731
    close = lambda pid, volume=None: client.post(  # noqa: E731
        f"{API}/positions/{pid}/close", json={"volume": volume} if volume else None
    )

    eur = order(account_id, "EURUSD", "buy", 1.5)["position"]["id"]
    gbp = order(account_id, "GBPUSD", "sell", 0.7)["position"]["id"]
    gold = order(account_id, "XAUUSD", "buy", 0.3)["position"]["id"]
    yen = order(account_id, "USDJPY", "sell", 2)["position"]["id"]
    eur_short = order(account_id, "EURUSD", "sell", 0.5)["position"]["id"]
    assert_books_balance(client, account_id)

    tick(10)
    assert close(eur, 0.5).status_code == 200
    assert close(gbp).status_code == 200
    assert_books_balance(client, account_id)

    tick(7)
    assert close(gold, 0.1).status_code == 200
    assert close(yen, 1.25).status_code == 200
    client.post(f"{API}/accounts/{account_id}/deposit", json={"amount": 1234.56})
    client.post(f"{API}/accounts/{account_id}/withdraw", json={"amount": 100})
    order(account_id, "EURUSD", "buy", 0.001, expect=422)  # a rejection must not touch the books
    assert_books_balance(client, account_id)

    tick(5)
    for position_id in (eur, gold, yen, eur_short):
        assert close(position_id).status_code == 200
    final = assert_books_balance(client, account_id)

    _, _, _, positions = books(client, account_id)
    realized = sum((dec(p["realized_profit"]) for p in positions), Decimal(0))
    assert dec(final["balance"]) == Decimal("50000") + Decimal("1234.56") - Decimal("100") + realized
    assert final["open_positions"] == 0
    assert dec(final["equity"]) == dec(final["balance"])


def test_ledger_entries_for_a_simple_flow(client, account, order, set_quote):
    set_quote("EURUSD", 1.10000, 1.10010)
    position = order(account["id"], "EURUSD", "buy", 1)["position"]
    set_quote("EURUSD", 1.10110, 1.10120)
    client.post(f"{API}/positions/{position['id']}/close", json={"volume": 0.5})
    client.post(f"{API}/accounts/{account['id']}/withdraw", json={"amount": 50})

    entries = client.get(f"{API}/accounts/{account['id']}/ledger").json()
    assert [(e["kind"], e["amount"], e["balance_after"]) for e in entries] == [
        ("deposit", 10000, 10000),
        ("realized_pnl", 50, 10050),
        ("withdrawal", -50, 10000),
    ]
    assert client.get(f"{API}/accounts/999/ledger").status_code == 404


def test_break_even_close_posts_no_ledger_entry(client, account, order, set_quote):
    set_quote("EURUSD", 1.1, 1.1)
    position = order(account["id"], "EURUSD", "buy", 1)["position"]
    result = client.post(f"{API}/positions/{position['id']}/close").json()
    assert result["deal"]["profit"] == 0
    assert [e["kind"] for e in client.get(f"{API}/accounts/{account['id']}/ledger").json()] == ["deposit"]
    assert_books_balance(client, account["id"])


def test_zero_initial_deposit_and_sub_cent_amounts(client, make_account):
    empty = make_account(initial_deposit=0)
    assert client.get(f"{API}/accounts/{empty['id']}/ledger").json() == []
    response = client.post(f"{API}/accounts/{empty['id']}/deposit", json={"amount": 0.001})
    assert response.status_code == 422
    assert client.get(f"{API}/accounts/{empty['id']}/ledger").json() == []


def test_ledger_reconciles_in_the_database(client, account, order, set_quote, uow_for):
    set_quote("XAUUSD", 2000, 2000.5)
    position = order(account["id"], "XAUUSD", "buy", 1)["position"]
    set_quote("XAUUSD", 1990, 1990.5)
    client.post(f"{API}/positions/{position['id']}/close")

    session = uow_for(client.app).session
    result = ledger.reconcile(session, session.get(Account, account["id"]))
    assert result.consistent
    assert result.balance == Decimal("8950.00")  # (1990.00 - 2000.50) * 100 oz
