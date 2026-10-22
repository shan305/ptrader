"""Price selection, currency conversion, margin and profit/loss calculations."""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from ..errors import PricingError
from ..models import Position, Quote, Side, Symbol
from .market_data import latest_quote

MONEY = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def entry_price(side: Side, quote: Quote) -> Decimal:
    """Buys fill at the ask, sells at the bid."""
    return quote.ask if side is Side.BUY else quote.bid


def exit_price(position_side: Side, quote: Quote) -> Decimal:
    """A buy position is closed by selling at the bid, a sell position by buying at the ask."""
    return quote.bid if position_side is Side.BUY else quote.ask


def conversion_rate(db: Session, from_currency: str, to_currency: str) -> Decimal:
    """Rate that converts an amount in from_currency to to_currency, using latest mid prices."""
    if from_currency == to_currency:
        return Decimal(1)
    direct = latest_quote(db, f"{from_currency}{to_currency}")
    if direct is not None:
        return (direct.bid + direct.ask) / 2
    inverse = latest_quote(db, f"{to_currency}{from_currency}")
    if inverse is not None:
        return 2 / (inverse.bid + inverse.ask)
    raise PricingError(f"No conversion rate from {from_currency} to {to_currency}")


def profit(
    side: Side, open_price: Decimal, close_price: Decimal, volume: Decimal, contract_size: Decimal, rate: Decimal
) -> Decimal:
    direction = 1 if side is Side.BUY else -1
    return money((close_price - open_price) * direction * volume * contract_size * rate)


def required_margin(volume: Decimal, contract_size: Decimal, price: Decimal, leverage: int, rate: Decimal) -> Decimal:
    return money(volume * contract_size * price * rate / leverage)


def floating(
    db: Session, position: Position, symbol: Symbol, account_currency: str
) -> tuple[Decimal | None, Decimal | None]:
    """Current exit price and unrealized P&L of an open position, or None where no price is known."""
    quote = latest_quote(db, position.symbol)
    if quote is None:
        return None, None
    price = exit_price(position.side, quote)
    try:
        rate = conversion_rate(db, symbol.profit_currency, account_currency)
    except PricingError:
        return price, None
    return price, profit(position.side, position.open_price, price, position.volume, symbol.contract_size, rate)
