"""Execution: turns an accepted market order into a fill price."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from ..errors import PricingError, Rejection
from ..models import Side, Symbol
from . import pricing
from .market_data import require_quote


@dataclass(frozen=True)
class Fill:
    price: Decimal
    # Converts amounts in the symbol's profit currency to the account currency.
    conversion_rate: Decimal
    quote_id: int
    quote_time: datetime


class MarketExecution:
    """Simulated dealing desk: market orders fill in full at the latest quote.

    Buys fill at the ask and sells at the bid, so closing a buy position (a
    sell order) fills at the bid and closing a sell position at the ask.
    """

    def fill(self, db: Session, symbol: Symbol, side: Side, account_currency: str) -> Fill:
        try:
            quote = require_quote(db, symbol.name)
            rate = pricing.conversion_rate(db, symbol.profit_currency, account_currency)
        except PricingError as exc:
            raise Rejection(exc.detail) from None
        return Fill(pricing.entry_price(side, quote), rate, quote.id, quote.time)
