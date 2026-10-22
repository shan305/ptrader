import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from . import __version__
from .api import router
from .config import Settings
from .db import make_engine, make_session_factory
from .errors import PTraderError
from .events import EventBus
from .migrate import MigrationError, current_revision, head_revision, upgrade_database
from .services.market_data import QuotePipeline
from .services.seed import seed_defaults
from .services.simulator import MarketSimulator
from .uow import UnitOfWork
from .workers import BackgroundWorker, WorkerManager

logger = logging.getLogger(__name__)


def prepare_database(
    settings: Settings, engine: Engine, session_factory: sessionmaker, events: EventBus, pipeline: QuotePipeline
) -> None:
    if settings.auto_migrate:
        upgrade_database(engine)
    else:
        current, head = current_revision(engine), head_revision()
        if current != head:
            raise MigrationError(f"Database schema is at {current}, expected {head}; run `alembic upgrade head`")
    if settings.seed_on_startup:
        with session_factory() as session:
            added = seed_defaults(UnitOfWork(session, events), pipeline)
        if added:
            logger.info("Seeded symbols: %s", ", ".join(added))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    events = EventBus()
    pipeline = QuotePipeline(retention_per_symbol=settings.quote_retention_per_symbol or None)
    simulator = MarketSimulator(seed=settings.simulator_seed)
    workers = WorkerManager(stop_timeout=settings.worker_stop_timeout_seconds)

    if settings.simulator_enabled:

        def simulate_tick() -> None:
            with session_factory() as session:
                simulator.run(UnitOfWork(session, events), pipeline)

        workers.add(BackgroundWorker("quote-simulator", simulate_tick, settings.simulator_interval_seconds))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Startup: migrate, seed, start workers. Shutdown (also after a failed
        startup): stop workers and wait for their current run, then close the engine."""
        try:
            await asyncio.to_thread(prepare_database, settings, engine, session_factory, events, pipeline)
            await workers.start_all()
            app.state.ready = True
            yield
        finally:
            app.state.ready = False
            await workers.stop_all()
            engine.dispose()

    app = FastAPI(title="PTrader", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.events = events
    app.state.pipeline = pipeline
    app.state.simulator = simulator
    app.state.workers = workers
    app.state.ready = False

    @app.exception_handler(PTraderError)
    async def handle_ptrader_error(request: Request, exc: PTraderError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.get("/health", tags=["health"])
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "ready": app.state.ready,
            "workers": [asdict(status) for status in workers.status()],
        }

    app.include_router(router)
    return app


app = create_app()
