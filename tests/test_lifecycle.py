"""Application startup/shutdown, persistence across restarts, and background workers."""

import asyncio
import threading
import time

import pytest
from conftest import API, make_settings
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_accounting import assert_books_balance

from ptrader.db import make_engine
from ptrader.main import create_app
from ptrader.migrate import MigrationError, current_revision, head_revision
from ptrader.services.simulator import MarketSimulator
from ptrader.workers import BackgroundWorker, WorkerManager


def count_quotes(url: str, symbol: str = "EURUSD") -> int:
    engine = make_engine(url)
    with engine.connect() as connection:
        count = connection.execute(text("SELECT count(*) FROM quotes WHERE symbol = :s"), {"s": symbol}).scalar()
    engine.dispose()
    return count


def wait_for(condition, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        time.sleep(0.01)


def test_health_reports_readiness_and_workers():
    app = create_app(make_settings(simulator_enabled=True, simulator_interval_seconds=0.05))
    assert app.state.ready is False
    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["ready"] is True
        assert [(w["name"], w["state"]) for w in health["workers"]] == [("quote-simulator", "running")]
    assert app.state.ready is False
    assert app.state.workers.status()[0].state == "stopped"


def test_restart_preserves_state_and_does_not_reseed(db_url):
    with TestClient(create_app(make_settings(database_url=db_url))) as client:
        account = client.post(f"{API}/accounts", json={"name": "Persistent"}).json()
        body = {"account_id": account["id"], "symbol": "EURUSD", "side": "buy", "volume": 1}
        position = client.post(f"{API}/orders", json=body).json()["position"]
        client.post(f"{API}/positions/{position['id']}/close", json={"volume": 0.4})
        before = client.get(f"{API}/accounts/{account['id']}").json()
    quotes_before = count_quotes(db_url)

    with TestClient(create_app(make_settings(database_url=db_url))) as client:
        assert current_revision(client.app.state.engine) == head_revision()
        assert count_quotes(db_url) == quotes_before
        assert len(client.get(f"{API}/symbols").json()) == 7
        assert client.get(f"{API}/accounts/{account['id']}").json() == before
        assert client.get(f"{API}/positions/{position['id']}").json()["volume"] == 0.6

        assert client.post(f"{API}/positions/{position['id']}/close").status_code == 200
        assert_books_balance(client, account["id"])


def test_startup_without_auto_migrate_requires_current_schema(db_url):
    strict = make_settings(database_url=db_url, auto_migrate=False, simulator_enabled=True)
    app = create_app(strict)
    with pytest.raises(MigrationError, match="expected 0002"):
        with TestClient(app):
            pass
    assert app.state.workers.status()[0].state == "created"  # never started

    with TestClient(create_app(make_settings(database_url=db_url))):
        pass
    with TestClient(create_app(strict)) as client:
        assert client.get("/health").json()["ready"] is True


def test_simulator_worker_feeds_quotes_and_stops_with_the_app(db_url):
    settings = make_settings(database_url=db_url, simulator_enabled=True, simulator_interval_seconds=0.01)
    with TestClient(create_app(settings)) as client:
        wait_for(lambda: count_quotes(db_url) > 3)
        history = client.get(f"{API}/quotes/EURUSD/history").json()
        assert history[0]["source"] == "simulator"

    stopped_at = count_quotes(db_url)
    time.sleep(0.1)
    assert count_quotes(db_url) == stopped_at


def test_shutdown_waits_for_the_run_in_progress(db_url, monkeypatch):
    started, finished = threading.Event(), threading.Event()
    original = MarketSimulator.run

    def slow_run(self, *args, **kwargs):
        started.set()
        time.sleep(0.3)
        result = original(self, *args, **kwargs)
        finished.set()
        return result

    monkeypatch.setattr(MarketSimulator, "run", slow_run)
    app = create_app(make_settings(database_url=db_url, simulator_enabled=True, simulator_interval_seconds=10))
    with TestClient(app):
        assert started.wait(2)
        before = count_quotes(db_url)
    # Shutdown returned only after the run committed, and before the engine was disposed.
    assert finished.is_set()
    assert count_quotes(db_url) == before + 1
    status = app.state.workers.status()[0]
    assert (status.state, status.failures) == ("stopped", 0)


def test_worker_retries_with_backoff_and_recovers():
    calls = []

    def flaky():
        calls.append(time.monotonic())
        if len(calls) <= 2:
            raise ConnectionError("feed down")

    async def scenario():
        worker = BackgroundWorker("flaky", flaky, interval=0.01, max_backoff=0.04)
        await worker.start()
        while len(calls) < 4:
            await asyncio.sleep(0.01)
        assert await worker.stop(timeout=1)
        return worker.status()

    status = asyncio.run(scenario())
    assert (status.failures, status.consecutive_failures) == (2, 0)
    assert status.last_error == "ConnectionError: feed down"
    assert status.last_success is not None
    # Backoff: the wait after the second failure (0.04) is longer than after the first (0.02).
    assert calls[2] - calls[1] > calls[1] - calls[0]


def test_stuck_worker_is_cancelled_after_the_stop_timeout():
    async def scenario():
        worker = BackgroundWorker("stuck", lambda: time.sleep(0.5), interval=0.01)
        await worker.start()
        await asyncio.sleep(0.05)
        clean = await worker.stop(timeout=0.05)
        return clean, worker.status().state, worker.running

    assert asyncio.run(scenario()) == (False, "failed", False)


def test_manager_unwinds_when_a_worker_fails_to_start():
    class Broken(BackgroundWorker):
        async def start(self):
            raise RuntimeError("cannot start")

    async def scenario():
        manager = WorkerManager(stop_timeout=1)
        first = BackgroundWorker("first", lambda: None, interval=0.01)
        manager.add(first)
        manager.add(Broken("second", lambda: None, interval=0.01))
        with pytest.raises(RuntimeError, match="cannot start"):
            await manager.start_all()
        return first

    first = asyncio.run(scenario())
    assert not first.running
    assert first.status().state == "stopped"

    with pytest.raises(ValueError, match="Duplicate"):
        manager = WorkerManager()
        manager.add(BackgroundWorker("a", lambda: None, 1))
        manager.add(BackgroundWorker("a", lambda: None, 1))
