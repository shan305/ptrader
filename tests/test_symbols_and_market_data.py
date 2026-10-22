from conftest import API


def test_default_symbols_are_seeded(client):
    symbols = {s["name"]: s for s in client.get(f"{API}/symbols").json()}
    assert {"EURUSD", "GBPUSD", "USDJPY", "XAUUSD"} <= symbols.keys()
    assert symbols["EURUSD"]["contract_size"] == 100000
    assert symbols["EURUSD"]["digits"] == 5
    assert symbols["XAUUSD"]["contract_size"] == 100
    assert symbols["XAUUSD"]["digits"] == 2
    assert symbols["USDJPY"]["profit_currency"] == "JPY"
    assert symbols["GBPUSD"]["min_volume"] == 0.01
    assert symbols["GBPUSD"]["volume_step"] == 0.01


def test_create_and_update_symbol(client):
    body = {
        "name": "EURGBP",
        "base_currency": "EUR",
        "profit_currency": "GBP",
        "contract_size": 100000,
        "digits": 5,
        "min_volume": 0.01,
        "max_volume": 50,
        "volume_step": 0.01,
    }
    assert client.post(f"{API}/symbols", json=body).status_code == 201
    assert client.post(f"{API}/symbols", json=body).status_code == 409
    assert client.post(f"{API}/symbols", json={**body, "name": "BAD", "min_volume": 100}).status_code == 422

    response = client.patch(f"{API}/symbols/EURGBP", json={"enabled": False, "max_volume": 10})
    assert response.json()["enabled"] is False
    assert response.json()["max_volume"] == 10
    assert client.get(f"{API}/symbols/NOPE").status_code == 404


def test_every_default_symbol_has_an_opening_quote(client):
    quotes = {q["symbol"]: q for q in client.get(f"{API}/quotes").json()}
    symbols = {s["name"] for s in client.get(f"{API}/symbols").json()}
    assert quotes.keys() == symbols
    for quote in quotes.values():
        assert quote["ask"] >= quote["bid"] > 0


def test_pushed_quote_is_rounded_to_symbol_digits(client, set_quote):
    quote = set_quote("EURUSD", 1.123456789, 1.12360001)
    assert (quote["bid"], quote["ask"]) == (1.12346, 1.1236)
    assert client.get(f"{API}/quotes/EURUSD").json()["bid"] == 1.12346

    gold = set_quote("XAUUSD", 2400.129, 2400.5)
    assert gold["bid"] == 2400.13


def test_invalid_quotes_are_refused(client):
    assert client.post(f"{API}/quotes", json={"symbol": "EURUSD", "bid": 1.2, "ask": 1.1}).status_code == 422
    assert client.post(f"{API}/quotes", json={"symbol": "EURUSD", "bid": 0, "ask": 1.1}).status_code == 422
    assert client.post(f"{API}/quotes", json={"symbol": "NOPE", "bid": 1, "ask": 1}).status_code == 404


def test_simulator_generates_ticks(client):
    before = {q["symbol"]: q for q in client.get(f"{API}/quotes").json()}
    response = client.post(f"{API}/quotes/simulate", json={"steps": 5})
    assert response.status_code == 200
    after = {q["symbol"]: q for q in response.json()}
    assert after.keys() == before.keys()
    for name, quote in after.items():
        assert quote["ask"] >= quote["bid"] > 0
        spread_before = round(before[name]["ask"] - before[name]["bid"], 8)
        assert round(quote["ask"] - quote["bid"], 8) == spread_before

    history = client.get(f"{API}/quotes/EURUSD/history", params={"limit": 3}).json()
    assert len(history) == 3
    assert history[0]["bid"] == after["EURUSD"]["bid"]
    assert len(client.get(f"{API}/quotes/EURUSD/history").json()) == 6


def test_simulator_can_target_symbols(client):
    response = client.post(f"{API}/quotes/simulate", json={"symbols": ["XAUUSD"]})
    assert [q["symbol"] for q in response.json()] == ["XAUUSD"]
    assert client.post(f"{API}/quotes/simulate", json={"symbols": ["NOPE"]}).status_code == 404
