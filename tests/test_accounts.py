from conftest import API


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_create_account_records_initial_deposit(client, account):
    assert account["balance"] == 10000
    assert account["equity"] == 10000
    assert account["free_margin"] == 10000
    assert account["margin"] == 0
    assert account["margin_level"] is None
    assert account["currency"] == "USD"
    assert account["leverage"] == 100

    deals = client.get(f"{API}/accounts/{account['id']}/deals").json()
    assert [(d["type"], d["profit"]) for d in deals] == [("balance", 10000)]


def test_create_account_validates_input(client):
    assert client.post(f"{API}/accounts", json={"name": "x", "currency": "usd"}).status_code == 422
    assert client.post(f"{API}/accounts", json={"name": "x", "leverage": 0}).status_code == 422
    assert client.post(f"{API}/accounts", json={"name": ""}).status_code == 422


def test_list_and_missing_accounts(client, make_account):
    make_account(name="A")
    make_account(name="B")
    assert [a["name"] for a in client.get(f"{API}/accounts").json()] == ["A", "B"]
    assert client.get(f"{API}/accounts/999").status_code == 404


def test_update_settings(client, account):
    response = client.patch(f"{API}/accounts/{account['id']}", json={"name": "Renamed", "leverage": 500})
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    assert response.json()["leverage"] == 500


def test_leverage_is_locked_while_positions_are_open(client, account, order):
    order(account["id"], "EURUSD", "buy", 0.1)
    response = client.patch(f"{API}/accounts/{account['id']}", json={"leverage": 50})
    assert response.status_code == 409


def test_deposit_and_withdraw(client, account):
    account_id = account["id"]
    assert client.post(f"{API}/accounts/{account_id}/deposit", json={"amount": 500.55}).json()["balance"] == 10500.55
    assert client.post(f"{API}/accounts/{account_id}/withdraw", json={"amount": 0.55}).json()["balance"] == 10500
    assert client.post(f"{API}/accounts/{account_id}/withdraw", json={"amount": 20000}).status_code == 409
    assert client.post(f"{API}/accounts/{account_id}/deposit", json={"amount": -5}).status_code == 422

    deals = client.get(f"{API}/accounts/{account_id}/deals").json()
    assert [d["profit"] for d in deals] == [10000, 500.55, -0.55]


def test_withdraw_is_limited_by_used_margin(client, account, order, set_quote):
    set_quote("EURUSD", 1.1, 1.1)
    order(account["id"], "EURUSD", "buy", 1)  # margin 1100
    assert client.post(f"{API}/accounts/{account['id']}/withdraw", json={"amount": 8900.01}).status_code == 409
    assert client.post(f"{API}/accounts/{account['id']}/withdraw", json={"amount": 8900}).status_code == 200
