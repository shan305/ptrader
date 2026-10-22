from datetime import datetime

from fastapi import APIRouter, Query, Request

from ..schemas import QuoteIn, QuoteOut, SimulateRequest
from ..services import market_data
from ..services.market_data import RawQuote
from ..services.symbols import get_symbol
from .deps import Uow

router = APIRouter(prefix="/quotes", tags=["market data"])


@router.get("", response_model=list[QuoteOut])
def latest_quotes(uow: Uow):
    """Latest bid/ask for every symbol."""
    return market_data.latest_quotes(uow.session)


@router.post("", response_model=QuoteOut, status_code=201)
def push_quote(data: QuoteIn, request: Request, uow: Uow):
    """Feed an external quote through the ingestion pipeline; prices are rounded to the symbol's digits."""
    raw = RawQuote(data.symbol, data.bid, data.ask, data.time, data.source)
    return request.app.state.pipeline.submit(uow, [raw])[0]


@router.post("/simulate", response_model=list[QuoteOut])
def simulate(data: SimulateRequest, request: Request, uow: Uow):
    """Generate random-walk ticks and return the newest quote per symbol."""
    return request.app.state.simulator.run(uow, request.app.state.pipeline, data.symbols, data.steps)


@router.get("/{symbol}", response_model=QuoteOut)
def latest_quote(symbol: str, uow: Uow):
    get_symbol(uow.session, symbol)
    return market_data.require_quote(uow.session, symbol)


@router.get("/{symbol}/history", response_model=list[QuoteOut])
def quote_history(
    symbol: str,
    uow: Uow,
    limit: int = Query(default=100, ge=1, le=10000),
    since: datetime | None = None,
    until: datetime | None = None,
):
    """Most recent quotes first, optionally limited to a time range."""
    return market_data.quote_history(uow.session, symbol, limit, since, until)
