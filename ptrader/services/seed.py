from decimal import Decimal

from ..models import Symbol
from ..uow import UnitOfWork
from .market_data import QuotePipeline, RawQuote

D = Decimal

# name, description, base, profit currency, contract size, digits, initial bid, initial ask
DEFAULT_SYMBOLS = [
    ("EURUSD", "Euro vs US Dollar", "EUR", "USD", D("100000"), 5, D("1.08500"), D("1.08512")),
    ("GBPUSD", "Great Britain Pound vs US Dollar", "GBP", "USD", D("100000"), 5, D("1.26500"), D("1.26515")),
    ("AUDUSD", "Australian Dollar vs US Dollar", "AUD", "USD", D("100000"), 5, D("0.66200"), D("0.66214")),
    ("USDJPY", "US Dollar vs Japanese Yen", "USD", "JPY", D("100000"), 3, D("149.500"), D("149.514")),
    ("USDCHF", "US Dollar vs Swiss Franc", "USD", "CHF", D("100000"), 5, D("0.88200"), D("0.88216")),
    ("XAUUSD", "Gold vs US Dollar", "XAU", "USD", D("100"), 2, D("2350.00"), D("2350.35")),
    ("XAGUSD", "Silver vs US Dollar", "XAG", "USD", D("5000"), 3, D("28.500"), D("28.530")),
]


def seed_defaults(uow: UnitOfWork, pipeline: QuotePipeline) -> list[str]:
    """Insert missing default symbols with an opening quote; returns the names added."""
    added = []
    with uow.transaction():
        session = uow.session
        for name, description, base, profit, contract_size, digits, bid, ask in DEFAULT_SYMBOLS:
            if session.get(Symbol, name) is not None:
                continue
            session.add(
                Symbol(
                    name=name,
                    description=description,
                    base_currency=base,
                    profit_currency=profit,
                    contract_size=contract_size,
                    digits=digits,
                    min_volume=D("0.01"),
                    max_volume=D("100"),
                    volume_step=D("0.01"),
                )
            )
            session.flush()
            pipeline.ingest(uow, RawQuote(name, bid, ask, source="seed"))
            added.append(name)
    return added
