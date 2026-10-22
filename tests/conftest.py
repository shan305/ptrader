from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from ptrader.config import Settings
from ptrader.events import Event
from ptrader.main import create_app
from ptrader.uow import UnitOfWork

API = "/api/v1"


@pytest.fixture
def client():
    settings = Settings(_env_file=None, database_url="sqlite://", simulator_enabled=False, simulator_seed=42)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def make_account(client):
    def make(**fields):
        body = {"name": "Demo", **fields}
        response = client.post(f"{API}/accounts", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    return make


@pytest.fixture
def account(make_account):
    return make_account(initial_deposit=10000)


@pytest.fixture
def set_quote(client):
    def set_(symbol, bid, ask):
        response = client.post(f"{API}/quotes", json={"symbol": symbol, "bid": bid, "ask": ask})
        assert response.status_code == 201, response.text
        return response.json()

    return set_


@pytest.fixture
def order(client):
    def place(account_id, symbol, side, volume, expect=201, **extra):
        body = {"account_id": account_id, "symbol": symbol, "side": side, "volume": volume, **extra}
        response = client.post(f"{API}/orders", json=body)
        assert response.status_code == expect, response.text
        return response.json()

    return place


@pytest.fixture
def get_account(client):
    def get(account_id):
        response = client.get(f"{API}/accounts/{account_id}")
        assert response.status_code == 200, response.text
        return response.json()

    return get


def make_settings(**overrides) -> Settings:
    values = {"database_url": "sqlite://", "simulator_enabled": False, "simulator_seed": 42, **overrides}
    return Settings(_env_file=None, **values)


@pytest.fixture
def db_url(tmp_path):
    """A file-backed SQLite database that outlives a single app instance."""
    return f"sqlite:///{tmp_path / 'ptrader.db'}"


@pytest.fixture
def uow_for():
    """Open a unit of work against a running app's database and event bus."""
    sessions = []

    def make(app):
        session = app.state.session_factory()
        sessions.append(session)
        return UnitOfWork(session, app.state.events)

    yield make
    for session in sessions:
        session.close()


@pytest.fixture
def recorded_events(client):
    """Every event published by the client's app, in order."""
    events = []
    client.app.state.events.subscribe(Event, events.append)
    return events


def dec(value) -> Decimal:
    """Exact Decimal from a JSON number."""
    return Decimal(str(value))
