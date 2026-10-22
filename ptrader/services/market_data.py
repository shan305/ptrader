"""Market-data ingestion pipeline and quote history queries."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..errors import InvalidRequest, PricingError
from ..events import QuoteReceived
from ..models import Quote, utcnow
from ..uow import UnitOfWork
from .symbols import get_symbol, normalize_price


@dataclass(frozen=True)
class RawQuote:
    """A quote as received from a source, before normalisation and validation."""

    symbol: str
    bid: Decimal
    ask: Decimal
    time: datetime | None = None
    source: str = "api"


class QuotePipeline:
    """Normalises, validates, stores and publishes incoming quotes.

    Stages for each quote: resolve the symbol, round prices to its digits,
    check bid/ask, check the timestamp is neither in the future nor older than
    the symbol's latest quote, store it, emit QuoteReceived, and trim the
    symbol's history to the retention limit.
    """

    def __init__(self, retention_per_symbol: int | None = None, max_clock_skew: timedelta = timedelta(seconds=60)):
        self.retention_per_symbol = retention_per_symbol
        self.max_clock_skew = max_clock_skew

    def submit(self, uow: UnitOfWork, raw_quotes: list[RawQuote]) -> list[Quote]:
        """Ingest quotes in one transaction: all are stored or none are."""
        with uow.transaction():
            return [self.ingest(uow, raw) for raw in raw_quotes]

    def ingest(self, uow: UnitOfWork, raw: RawQuote) -> Quote:
        """Ingest one quote inside the caller's transaction."""
        session = uow.session
        symbol = get_symbol(session, raw.symbol)
        bid, ask = normalize_price(symbol, raw.bid), normalize_price(symbol, raw.ask)
        if bid <= 0:
            raise InvalidRequest(f"Bid must be positive at {symbol.digits} digits")
        if ask < bid:
            raise InvalidRequest("Ask must not be below bid")

        now = utcnow()
        time = _as_utc(raw.time) if raw.time is not None else now
        if time > now + self.max_clock_skew:
            raise InvalidRequest(f"Quote time {time.isoformat()} is in the future")
        latest = latest_quote(session, symbol.name)
        if latest is not None and time < latest.time:
            raise InvalidRequest(
                f"Out-of-order quote for {symbol.name}: {time.isoformat()} is older than {latest.time.isoformat()}"
            )

        quote = Quote(symbol=symbol.name, bid=bid, ask=ask, time=time, source=raw.source)
        session.add(quote)
        session.flush()
        uow.emit(QuoteReceived(symbol.name, bid, ask, time, raw.source))
        self._trim_history(session, symbol.name)
        return quote

    def _trim_history(self, session: Session, symbol: str) -> None:
        if not self.retention_per_symbol:
            return
        cutoff = session.scalar(
            select(Quote.id)
            .where(Quote.symbol == symbol)
            .order_by(Quote.id.desc())
            .offset(self.retention_per_symbol)
            .limit(1)
        )
        if cutoff is not None:
            session.execute(delete(Quote).where(Quote.symbol == symbol, Quote.id <= cutoff))


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def latest_quote(db: Session, symbol: str) -> Quote | None:
    return db.scalar(select(Quote).where(Quote.symbol == symbol).order_by(Quote.id.desc()).limit(1))


def require_quote(db: Session, symbol: str) -> Quote:
    quote = latest_quote(db, symbol)
    if quote is None:
        raise PricingError(f"No quote available for {symbol}")
    return quote


def latest_quotes(db: Session) -> list[Quote]:
    newest_ids = select(func.max(Quote.id)).group_by(Quote.symbol)
    return list(db.scalars(select(Quote).where(Quote.id.in_(newest_ids)).order_by(Quote.symbol)))


def quote_history(
    db: Session, symbol: str, limit: int, since: datetime | None = None, until: datetime | None = None
) -> list[Quote]:
    """Most recent quotes first, optionally within [since, until]."""
    get_symbol(db, symbol)
    query = select(Quote).where(Quote.symbol == symbol)
    if since is not None:
        query = query.where(Quote.time >= _as_utc(since))
    if until is not None:
        query = query.where(Quote.time <= _as_utc(until))
    return list(db.scalars(query.order_by(Quote.id.desc()).limit(limit)))
