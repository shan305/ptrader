from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from .models import DealEntry, DealType, LedgerKind, OrderStatus, OrderType, PositionStatus, Side

# Decimals are exact internally and rendered as JSON numbers.
Num = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]

Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# Accounts

class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    currency: Currency = "USD"
    leverage: int = Field(default=100, ge=1, le=1000)
    initial_deposit: Decimal = Field(default=Decimal("10000"), ge=0)


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    leverage: int | None = Field(default=None, ge=1, le=1000)
    is_active: bool | None = None


class BalanceOperation(BaseModel):
    amount: Decimal = Field(gt=0)
    comment: str | None = Field(default=None, max_length=255)


class AccountOut(ORMModel):
    id: int
    name: str
    currency: str
    balance: Num
    leverage: int
    is_active: bool
    created_at: datetime


class LedgerEntryOut(ORMModel):
    id: int
    account_id: int
    deal_id: int
    kind: LedgerKind
    amount: Num
    balance_after: Num
    created_at: datetime


class AccountSummary(AccountOut):
    floating_pnl: Num
    equity: Num
    margin: Num
    free_margin: Num
    margin_level: Num | None = Field(description="Equity / margin * 100; empty with no open positions")
    open_positions: int


# Symbols and quotes

class SymbolBase(BaseModel):
    description: str = Field(default="", max_length=100)
    base_currency: Currency
    profit_currency: Currency
    contract_size: Decimal = Field(gt=0)
    digits: int = Field(ge=0, le=8)
    min_volume: Decimal = Field(gt=0)
    max_volume: Decimal = Field(gt=0)
    volume_step: Decimal = Field(gt=0)
    enabled: bool = True


class SymbolCreate(SymbolBase):
    name: str = Field(pattern=r"^[A-Z0-9._]{2,20}$")


class SymbolUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=100)
    min_volume: Decimal | None = Field(default=None, gt=0)
    max_volume: Decimal | None = Field(default=None, gt=0)
    volume_step: Decimal | None = Field(default=None, gt=0)
    enabled: bool | None = None


class SymbolOut(ORMModel):
    name: str
    description: str
    base_currency: str
    profit_currency: str
    contract_size: Num
    digits: int
    min_volume: Num
    max_volume: Num
    volume_step: Num
    enabled: bool


class QuoteIn(BaseModel):
    symbol: str
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    time: datetime | None = Field(default=None, description="Quote time; defaults to now. Naive times are UTC")
    source: str = Field(default="api", min_length=1, max_length=32)


class QuoteOut(ORMModel):
    symbol: str
    bid: Num
    ask: Num
    time: datetime
    source: str


class SimulateRequest(BaseModel):
    steps: int = Field(default=1, ge=1, le=1000)
    symbols: list[str] | None = Field(default=None, description="Defaults to every enabled symbol")


# Trading

class OrderCreate(BaseModel):
    account_id: int
    symbol: str
    side: Side
    type: OrderType = OrderType.MARKET
    volume: Decimal = Field(gt=0, description="Lots")
    position_id: int | None = Field(default=None, description="Close (part of) this position instead of opening one")
    comment: str | None = Field(default=None, max_length=255)


class ClosePositionRequest(BaseModel):
    volume: Decimal | None = Field(default=None, gt=0, description="Defaults to the full open volume")


class OrderOut(ORMModel):
    id: int
    account_id: int
    symbol: str
    side: Side
    type: OrderType
    volume: Num
    status: OrderStatus
    price: Num | None
    position_id: int | None
    reason: str | None
    comment: str | None
    created_at: datetime


class DealOut(ORMModel):
    id: int
    account_id: int
    type: DealType
    entry: DealEntry | None
    order_id: int | None
    position_id: int | None
    symbol: str | None
    volume: Num | None
    price: Num | None
    profit: Num
    comment: str | None
    time: datetime


class PositionOut(ORMModel):
    id: int
    account_id: int
    symbol: str
    side: Side
    status: PositionStatus
    volume: Num
    initial_volume: Num
    open_price: Num
    margin: Num
    realized_profit: Num
    open_time: datetime
    close_price: Num | None
    close_time: datetime | None
    current_price: Num | None = None
    floating_pnl: Num | None = None


class ExecutionOut(BaseModel):
    order: OrderOut
    deal: DealOut
    position: PositionOut
