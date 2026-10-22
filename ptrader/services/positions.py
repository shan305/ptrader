"""Position accounting: opening, reducing and valuing positions.

Positions are hedged: every opening order creates its own position, and
closing orders reduce one specific position. Realized profit is posted to the
account ledger as it is booked.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import NotFound, Rejection
from ..events import PositionOpened, PositionReduced
from ..models import (
    Account,
    Deal,
    DealEntry,
    DealType,
    LedgerKind,
    Order,
    Position,
    PositionStatus,
    Symbol,
    utcnow,
)
from ..schemas import PositionOut
from ..uow import UnitOfWork
from . import ledger, pricing
from .execution import Fill


def required_margin(symbol: Symbol, volume: Decimal, fill: Fill, leverage: int) -> Decimal:
    return pricing.required_margin(volume, symbol.contract_size, fill.price, leverage, fill.conversion_rate)


def check_reducible(position: Position, symbol: Symbol, volume: Decimal) -> None:
    if volume > position.volume:
        raise Rejection(f"Volume {volume} exceeds the open volume {position.volume} of position {position.id}")
    remaining = position.volume - volume
    if 0 < remaining < symbol.min_volume:
        raise Rejection(f"Remaining volume {remaining} would be below the minimum of {symbol.min_volume}")


def open_position(
    uow: UnitOfWork, account: Account, symbol: Symbol, order: Order, fill: Fill, margin: Decimal
) -> tuple[Position, Deal]:
    position = Position(
        account_id=account.id,
        symbol=symbol.name,
        side=order.side,
        volume=order.volume,
        initial_volume=order.volume,
        open_price=fill.price,
        margin=margin,
    )
    order.position = position
    deal = Deal(
        account_id=account.id,
        type=DealType(order.side.value),
        entry=DealEntry.IN,
        order=order,
        position=position,
        symbol=symbol.name,
        volume=order.volume,
        price=fill.price,
        profit=Decimal(0),
        comment=order.comment,
    )
    uow.session.add_all([position, order, deal])
    uow.session.flush()
    uow.emit(PositionOpened(position.id, account.id, symbol.name, position.side.value, position.volume, fill.price))
    return position, deal


def reduce_position(
    uow: UnitOfWork, account: Account, symbol: Symbol, position: Position, order: Order, fill: Fill
) -> Deal:
    """Close order.volume of the position at the fill price and book the realized profit."""
    check_reducible(position, symbol, order.volume)
    realized = pricing.profit(
        position.side, position.open_price, fill.price, order.volume, symbol.contract_size, fill.conversion_rate
    )
    released_margin = pricing.money(position.margin * order.volume / position.volume)
    position.volume -= order.volume
    position.margin -= released_margin
    position.realized_profit += realized
    if position.volume == 0:
        position.status = PositionStatus.CLOSED
        position.margin = Decimal(0)
        position.close_price = fill.price
        position.close_time = utcnow()

    order.position = position
    deal = Deal(
        account_id=account.id,
        type=DealType(order.side.value),
        entry=DealEntry.OUT,
        order=order,
        position=position,
        symbol=symbol.name,
        volume=order.volume,
        price=fill.price,
        profit=realized,
        comment=order.comment,
    )
    uow.session.add_all([order, deal])
    if realized != 0:
        ledger.post(uow, account, LedgerKind.REALIZED_PNL, realized, deal)
    uow.session.flush()
    uow.emit(
        PositionReduced(
            position.id, account.id, symbol.name, order.volume, fill.price, realized, position.volume
        )
    )
    return deal


def get_position(db: Session, position_id: int) -> Position:
    position = db.get(Position, position_id)
    if position is None:
        raise NotFound(f"Position {position_id} not found")
    return position


def open_positions(db: Session, account_id: int) -> list[Position]:
    return list_positions(db, account_id, PositionStatus.OPEN)


def list_positions(db: Session, account_id: int, status: PositionStatus | None) -> list[Position]:
    query = select(Position).where(Position.account_id == account_id)
    if status is not None:
        query = query.where(Position.status == status)
    return list(db.scalars(query.order_by(Position.id)))


def position_view(db: Session, position: Position) -> PositionOut:
    """Position with its current exit price and floating P&L when it is open."""
    view = PositionOut.model_validate(position)
    if position.status is PositionStatus.OPEN:
        account = db.get(Account, position.account_id)
        view.current_price, view.floating_pnl = pricing.floating(
            db, position, db.get(Symbol, position.symbol), account.currency
        )
    return view
