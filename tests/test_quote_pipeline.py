from datetime import datetime, timedelta, timezone

from conftest import API, make_settings
from fastapi.testclient import TestClient

from ptrader.events import QuoteReceived
from ptrader.main import create_app


def push(client, symbol, bid, ask, **extra):
    return client.post(f"{API}/quotes", json={"symbol": symbol, "bid": bid, "ask": ask, **extra})


def test_quotes_record_their_source(client, recorded_events):
    assert client.get(f"{API}/quotes/EURUSD").json()["source"] == "seed"
    quote = push(client, "EURUSD", 1.2, 1.2001, source="lp-feed").json()
    assert quote["source"] == "lp-feed"
    simulated = client.post(f"{API}/quotes/simulate", json={"symbols": ["EURUSD"]}).json()[0]
    assert simulated["source"] == "simulator"
    assert [(e.symbol, e.source) for e in recorded_events] == [("EURUSD", "lp-feed"), ("EURUSD", "simulator")]
    assert all(isinstance(e, QuoteReceived) for e in recorded_events)


def test_out_of_order_and_future_quotes_are_refused(client):
    now = datetime.now(timezone.utc)
    accepted = push(client, "GBPUSD", 1.3, 1.3002, time=now.isoformat())
    assert accepted.status_code == 201
    assert datetime.fromisoformat(accepted.json()["time"]) == now

    stale = push(client, "GBPUSD", 1.31, 1.3102, time=(now - timedelta(seconds=1)).isoformat())
    assert stale.status_code == 422
    assert "Out-of-order" in stale.json()["detail"]

    future = push(client, "GBPUSD", 1.31, 1.3102, time=(now + timedelta(minutes=5)).isoformat())
    assert future.status_code == 422
    assert "in the future" in future.json()["detail"]

    # Other symbols have their own sequence.
    assert push(client, "AUDUSD", 0.7, 0.7001, time=now.isoformat()).status_code == 201
    assert client.get(f"{API}/quotes/GBPUSD").json()["bid"] == 1.3


def test_naive_timestamps_are_utc(client):
    naive = datetime.now(timezone.utc).replace(tzinfo=None)
    quote = push(client, "EURUSD", 1.2, 1.2001, time=naive.isoformat()).json()
    assert datetime.fromisoformat(quote["time"]) == naive.replace(tzinfo=timezone.utc)


def test_history_time_range(client):
    start = datetime.now(timezone.utc)
    times = [start + timedelta(milliseconds=i) for i in range(1, 6)]
    for i, time in enumerate(times):
        push(client, "XAUUSD", 2000 + i, 2000.5 + i, time=time.isoformat())

    params = {"since": times[1].isoformat(), "until": times[3].isoformat()}
    window = client.get(f"{API}/quotes/XAUUSD/history", params=params).json()
    assert [q["bid"] for q in window] == [2003, 2002, 2001]


def test_history_retention_per_symbol():
    with TestClient(create_app(make_settings(quote_retention_per_symbol=5))) as client:
        client.post(f"{API}/quotes/simulate", json={"steps": 20})
        latest = {q["symbol"]: q for q in client.get(f"{API}/quotes").json()}
        for symbol, quote in latest.items():
            history = client.get(f"{API}/quotes/{symbol}/history").json()
            assert len(history) == 5
            assert history[0] == quote


def test_retention_can_be_disabled():
    with TestClient(create_app(make_settings(quote_retention_per_symbol=0))) as client:
        client.post(f"{API}/quotes/simulate", json={"steps": 20, "symbols": ["EURUSD"]})
        assert len(client.get(f"{API}/quotes/EURUSD/history").json()) == 21
