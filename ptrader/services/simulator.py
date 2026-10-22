import random
from decimal import Decimal

from sqlalchemy import select

from ..models import Quote, Symbol, utcnow
from ..uow import UnitOfWork
from .market_data import QuotePipeline, RawQuote, latest_quote
from .symbols import get_symbol


class MarketSimulator:
    """Random-walk quote source that keeps each symbol's current spread.

    Generated quotes go through the QuotePipeline like any external feed.
    """

    source = "simulator"

    def __init__(self, seed: int | None = None, volatility: float = 0.0002):
        self._rng = random.Random(seed)
        self.volatility = volatility

    def next_quote(self, symbol: Symbol, last: Quote) -> RawQuote:
        point = symbol.point
        spread = max(last.ask - last.bid, point)
        mid = (last.bid + last.ask) / 2
        change = Decimal(str(self._rng.gauss(0, self.volatility))) * mid
        bid = max((mid + change - spread / 2).quantize(point), point)
        # Never earlier than the last quote, which may carry a feed's slightly-ahead clock.
        return RawQuote(symbol.name, bid, bid + spread, max(utcnow(), last.time), self.source)

    def run(
        self, uow: UnitOfWork, pipeline: QuotePipeline, symbols: list[str] | None = None, steps: int = 1
    ) -> list[Quote]:
        """Generate `steps` ticks per symbol in one transaction; returns the newest quote per symbol."""
        with uow.transaction():
            session = uow.session
            if symbols is None:
                targets = list(session.scalars(select(Symbol).where(Symbol.enabled).order_by(Symbol.name)))
            else:
                targets = [get_symbol(session, name) for name in symbols]
            newest: dict[str, Quote] = {}
            for _ in range(steps):
                for symbol in targets:
                    last = newest.get(symbol.name) or latest_quote(session, symbol.name)
                    if last is not None:
                        newest[symbol.name] = pipeline.ingest(uow, self.next_quote(symbol, last))
            return list(newest.values())
