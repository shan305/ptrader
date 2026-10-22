"""Order lifecycle: validate, execute, account, and record the outcome.

A market order goes through these stages:

1. resolve the account and symbol (unknown ones raise NotFound, nothing is stored)
2. pre-trade checks: account and symbol enabled, volume rules, and for closing
   orders the target position
3. execution: a fill price from the latest quote (MarketExecution)
4. risk check: free margin for opening orders
5. position accounting and ledger posting (positions module)

Stages 2-5 run in one transaction. A failed business rule (Rejection) rolls
that transaction back, then the order is stored as 'rejected' in a separate
transaction and OrderRejected is raised. Any other error rolls back and
propagates with nothing stored.
"""

import threading
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import NotFound, OrderRejected, Rejection
from ..events import OrderFilled
from ..events import OrderRejected as OrderRejectedEvent
from ..models import Account, Deal, Order, OrderStatus, Position, PositionStatus, Symbol
from ..schemas import OrderCreate
from ..uow import UnitOfWork
from . import accounts, positions
from .execution import MarketExecution
from .symbols import get_symbol, volume_error

# Serialises executions within this process so balance and margin checks do not interleave.
_execution_lock = threading.Lock()


@dataclass
class Execution:
    order: Order
    deal: Deal
    position: Position


def place_order(uow: UnitOfWork, request: OrderCreate, execution: MarketExecution | None = None) -> Execution:
    """Open a position, or close (part of) request.position_id, at market."""
    execution = execution or MarketExecution()
    with _execution_lock:
        try:
            with uow.transaction():
                return _execute(uow, request, execution)
        except Rejection as rejection:
            with uow.transaction():
                order = _record_rejection(uow, request, rejection.reason)
            raise OrderRejected(rejection.reason, order.id) from None


def close_position(
    uow: UnitOfWork, position_id: int, volume: Decimal | None = None, execution: MarketExecution | None = None
) -> Execution:
    position = positions.get_position(uow.session, position_id)
    request = OrderCreate(
        account_id=position.account_id,
        symbol=position.symbol,
        side=position.side.opposite,
        volume=volume if volume is not None else position.volume,
        position_id=position.id,
    )
    return place_order(uow, request, execution)


def _execute(uow: UnitOfWork, request: OrderCreate, execution: MarketExecution) -> Execution:
    session = uow.session
    account = accounts.get_account(session, request.account_id)
    symbol = get_symbol(session, request.symbol)
    target = _pre_trade_checks(session, account, symbol, request)

    fill = execution.fill(session, symbol, request.side, account.currency)
    order = _new_order(request, OrderStatus.FILLED, price=fill.price)
    if target is None:
        margin = positions.required_margin(symbol, request.volume, fill, account.leverage)
        free_margin = accounts.metrics(session, account).free_margin
        if margin > free_margin:
            raise Rejection(f"Insufficient free margin: required {margin}, available {free_margin}")
        position, deal = positions.open_position(uow, account, symbol, order, fill, margin)
    else:
        position = target
        deal = positions.reduce_position(uow, account, symbol, position, order, fill)

    uow.emit(
        OrderFilled(order.id, account.id, symbol.name, order.side.value, order.volume, order.price, position.id)
    )
    return Execution(order, deal, position)


def _pre_trade_checks(session: Session, account: Account, symbol: Symbol, request: OrderCreate) -> Position | None:
    """Raise Rejection if the order may not trade; return the position a closing order targets."""
    position = None
    if request.position_id is not None:
        position = session.get(Position, request.position_id)
        if position is None or position.account_id != account.id:
            raise Rejection(f"Position {request.position_id} not found for account {account.id}")
        if position.status is not PositionStatus.OPEN:
            raise Rejection(f"Position {position.id} is already closed")
        if position.symbol != symbol.name:
            raise Rejection(f"Position {position.id} is on {position.symbol}, not {symbol.name}")
        if request.side is position.side:
            raise Rejection(f"Closing a {position.side} position requires a {position.side.opposite} order")

    if not account.is_active:
        raise Rejection(f"Account {account.id} is disabled")
    if not symbol.enabled:
        raise Rejection(f"Trading is disabled for {symbol.name}")
    error = volume_error(symbol, request.volume)
    if error:
        raise Rejection(error)
    if position is not None:
        positions.check_reducible(position, symbol, request.volume)
    return position


def _new_order(request: OrderCreate, status: OrderStatus, **fields) -> Order:
    return Order(
        account_id=request.account_id,
        symbol=request.symbol,
        side=request.side,
        type=request.type,
        volume=request.volume,
        comment=request.comment,
        status=status,
        **fields,
    )


def _record_rejection(uow: UnitOfWork, request: OrderCreate, reason: str) -> Order:
    order = _new_order(request, OrderStatus.REJECTED, reason=reason)
    # Keep the reference only when it points at a real position.
    if request.position_id is not None and uow.session.get(Position, request.position_id) is not None:
        order.position_id = request.position_id
    uow.session.add(order)
    uow.session.flush()
    uow.emit(OrderRejectedEvent(order.id, order.account_id, order.symbol, reason))
    return order


def get_order(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise NotFound(f"Order {order_id} not found")
    return order


def list_orders(db: Session, account_id: int, status: OrderStatus | None) -> list[Order]:
    query = select(Order).where(Order.account_id == account_id)
    if status is not None:
        query = query.where(Order.status == status)
    return list(db.scalars(query.order_by(Order.id)))


def list_deals(db: Session, account_id: int) -> list[Deal]:
    return list(db.scalars(select(Deal).where(Deal.account_id == account_id).order_by(Deal.id)))
