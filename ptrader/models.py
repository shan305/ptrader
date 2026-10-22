import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, DecimalString, UTCDateTime


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def num(column: str) -> str:
    """SQL expression comparing a DecimalString column as a number."""
    return f"CAST({column} AS NUMERIC)"


class Side(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(enum.StrEnum):
    MARKET = "market"


class OrderStatus(enum.StrEnum):
    FILLED = "filled"
    REJECTED = "rejected"


class PositionStatus(enum.StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class DealType(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"
    BALANCE = "balance"


class DealEntry(enum.StrEnum):
    IN = "in"
    OUT = "out"


class LedgerKind(enum.StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    REALIZED_PNL = "realized_pnl"


def _enum(cls: type[enum.Enum]) -> Enum:
    return Enum(cls, native_enum=False, length=16, values_callable=lambda e: [m.value for m in e])


def _one_of(column: str, cls: type[enum.Enum]) -> str:
    values = ", ".join(f"'{m.value}'" for m in cls)
    return f"{column} IN ({values})"


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("length(name) > 0", name="name_not_empty"),
        CheckConstraint("length(currency) = 3", name="currency_code"),
        CheckConstraint("leverage BETWEEN 1 AND 1000", name="leverage_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    # Cached running total of the account's ledger entries.
    balance: Mapped[Decimal] = mapped_column(DecimalString, default=Decimal("0"))
    leverage: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Symbol(Base):
    __tablename__ = "symbols"
    __table_args__ = (
        CheckConstraint(f"{num('contract_size')} > 0", name="contract_size_positive"),
        CheckConstraint("digits BETWEEN 0 AND 8", name="digits_range"),
        CheckConstraint(f"{num('volume_step')} > 0", name="volume_step_positive"),
        CheckConstraint(f"{num('min_volume')} > 0", name="min_volume_positive"),
        CheckConstraint(f"{num('max_volume')} >= {num('min_volume')}", name="volume_range"),
        CheckConstraint("length(base_currency) = 3 AND length(profit_currency) = 3", name="currency_codes"),
    )

    name: Mapped[str] = mapped_column(String(20), primary_key=True)
    description: Mapped[str] = mapped_column(String(100), default="")
    base_currency: Mapped[str] = mapped_column(String(3))
    profit_currency: Mapped[str] = mapped_column(String(3))
    contract_size: Mapped[Decimal] = mapped_column(DecimalString)
    digits: Mapped[int] = mapped_column(Integer)
    min_volume: Mapped[Decimal] = mapped_column(DecimalString)
    max_volume: Mapped[Decimal] = mapped_column(DecimalString)
    volume_step: Mapped[Decimal] = mapped_column(DecimalString)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def point(self) -> Decimal:
        return Decimal(1).scaleb(-self.digits)


class Quote(Base):
    __tablename__ = "quotes"
    __table_args__ = (
        Index("ix_quotes_symbol_id", "symbol", "id"),
        Index("ix_quotes_symbol_time", "symbol", "time"),
        CheckConstraint(f"{num('bid')} > 0", name="bid_positive"),
        CheckConstraint(f"{num('ask')} >= {num('bid')}", name="ask_not_below_bid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("symbols.name"))
    bid: Mapped[Decimal] = mapped_column(DecimalString)
    ask: Mapped[Decimal] = mapped_column(DecimalString)
    time: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # Where the quote came from: api, simulator, seed, ...
    source: Mapped[str] = mapped_column(String(32), default="api", server_default="api")


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        CheckConstraint(_one_of("side", Side), name="side"),
        CheckConstraint(_one_of("status", PositionStatus), name="status"),
        CheckConstraint(f"{num('initial_volume')} > 0", name="initial_volume_positive"),
        CheckConstraint(f"{num('volume')} >= 0 AND {num('volume')} <= {num('initial_volume')}", name="volume_range"),
        CheckConstraint(f"{num('open_price')} > 0", name="open_price_positive"),
        CheckConstraint(f"{num('margin')} >= 0", name="margin_not_negative"),
        CheckConstraint(
            f"(status = 'open' AND {num('volume')} > 0 AND close_time IS NULL AND close_price IS NULL)"
            f" OR (status = 'closed' AND {num('volume')} = 0 AND {num('margin')} = 0"
            " AND close_time IS NOT NULL AND close_price IS NOT NULL)",
            name="status_consistent",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("symbols.name"))
    side: Mapped[Side] = mapped_column(_enum(Side))
    status: Mapped[PositionStatus] = mapped_column(_enum(PositionStatus), default=PositionStatus.OPEN, index=True)
    volume: Mapped[Decimal] = mapped_column(DecimalString)
    initial_volume: Mapped[Decimal] = mapped_column(DecimalString)
    open_price: Mapped[Decimal] = mapped_column(DecimalString)
    margin: Mapped[Decimal] = mapped_column(DecimalString)
    realized_profit: Mapped[Decimal] = mapped_column(DecimalString, default=Decimal("0"))
    open_time: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    close_price: Mapped[Decimal | None] = mapped_column(DecimalString, nullable=True)
    close_time: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(_one_of("side", Side), name="side"),
        CheckConstraint(_one_of("type", OrderType), name="type"),
        CheckConstraint(_one_of("status", OrderStatus), name="status"),
        CheckConstraint(f"{num('volume')} > 0", name="volume_positive"),
        CheckConstraint(
            f"(status = 'filled' AND {num('price')} > 0 AND position_id IS NOT NULL AND reason IS NULL)"
            " OR (status = 'rejected' AND price IS NULL AND reason IS NOT NULL)",
            name="status_consistent",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("symbols.name"))
    side: Mapped[Side] = mapped_column(_enum(Side))
    type: Mapped[OrderType] = mapped_column(_enum(OrderType), default=OrderType.MARKET)
    volume: Mapped[Decimal] = mapped_column(DecimalString)
    status: Mapped[OrderStatus] = mapped_column(_enum(OrderStatus))
    # Fill price; empty for rejected orders.
    price: Mapped[Decimal | None] = mapped_column(DecimalString, nullable=True)
    # The position this order opened, or the one it was sent to close.
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    position: Mapped[Position | None] = relationship()


class Deal(Base):
    """A balance change or an execution against a position."""

    __tablename__ = "deals"
    __table_args__ = (
        CheckConstraint(_one_of("type", DealType), name="type"),
        CheckConstraint(f"entry IS NULL OR {_one_of('entry', DealEntry)}", name="entry"),
        CheckConstraint(
            "(type = 'balance' AND entry IS NULL AND order_id IS NULL AND position_id IS NULL"
            " AND symbol IS NULL AND volume IS NULL AND price IS NULL)"
            " OR (type IN ('buy', 'sell') AND entry IS NOT NULL AND order_id IS NOT NULL"
            f" AND position_id IS NOT NULL AND symbol IS NOT NULL AND {num('volume')} > 0 AND {num('price')} > 0)",
            name="kind_consistent",
        ),
        CheckConstraint(f"type <> 'balance' OR {num('profit')} <> 0", name="balance_not_zero"),
        CheckConstraint(f"entry IS NULL OR entry <> 'in' OR {num('profit')} = 0", name="entry_has_no_profit"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    type: Mapped[DealType] = mapped_column(_enum(DealType))
    entry: Mapped[DealEntry | None] = mapped_column(_enum(DealEntry), nullable=True)
    # At most one deal per order.
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, unique=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    symbol: Mapped[str | None] = mapped_column(ForeignKey("symbols.name"), nullable=True)
    volume: Mapped[Decimal | None] = mapped_column(DecimalString, nullable=True)
    price: Mapped[Decimal | None] = mapped_column(DecimalString, nullable=True)
    profit: Mapped[Decimal] = mapped_column(DecimalString, default=Decimal("0"))
    comment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    time: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    order: Mapped[Order | None] = relationship()
    position: Mapped[Position | None] = relationship()


class LedgerEntry(Base):
    """Every change to an account balance, each backed by exactly one deal."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint(_one_of("kind", LedgerKind), name="kind"),
        CheckConstraint(
            f"(kind = 'deposit' AND {num('amount')} > 0)"
            f" OR (kind = 'withdrawal' AND {num('amount')} < 0)"
            f" OR (kind = 'realized_pnl' AND {num('amount')} <> 0)",
            name="amount_sign",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"), unique=True)
    kind: Mapped[LedgerKind] = mapped_column(_enum(LedgerKind))
    amount: Mapped[Decimal] = mapped_column(DecimalString)
    balance_after: Mapped[Decimal] = mapped_column(DecimalString)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    deal: Mapped[Deal] = relationship()
