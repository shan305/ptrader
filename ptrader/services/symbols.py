from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import Conflict, InvalidRequest, NotFound
from ..models import Symbol
from ..schemas import SymbolCreate, SymbolUpdate
from ..uow import UnitOfWork


def list_symbols(db: Session) -> list[Symbol]:
    return list(db.scalars(select(Symbol).order_by(Symbol.name)))


def get_symbol(db: Session, name: str) -> Symbol:
    symbol = db.get(Symbol, name)
    if symbol is None:
        raise NotFound(f"Symbol {name} not found")
    return symbol


def _check_volume_limits(min_volume: Decimal, max_volume: Decimal, step: Decimal) -> None:
    if min_volume > max_volume:
        raise InvalidRequest("min_volume must not exceed max_volume")
    if min_volume % step != 0:
        raise InvalidRequest("min_volume must be a multiple of volume_step")


def create_symbol(uow: UnitOfWork, data: SymbolCreate) -> Symbol:
    with uow.transaction():
        if uow.session.get(Symbol, data.name) is not None:
            raise Conflict(f"Symbol {data.name} already exists")
        _check_volume_limits(data.min_volume, data.max_volume, data.volume_step)
        symbol = Symbol(**data.model_dump())
        uow.session.add(symbol)
    return symbol


def update_symbol(uow: UnitOfWork, name: str, data: SymbolUpdate) -> Symbol:
    with uow.transaction():
        symbol = get_symbol(uow.session, name)
        for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
            setattr(symbol, field, value)
        _check_volume_limits(symbol.min_volume, symbol.max_volume, symbol.volume_step)
    return symbol


def normalize_price(symbol: Symbol, price: Decimal) -> Decimal:
    return price.quantize(symbol.point)


def volume_error(symbol: Symbol, volume: Decimal) -> str | None:
    """Return why the volume is not tradable for this symbol, or None if it is."""
    if volume < symbol.min_volume:
        return f"Volume {volume} is below the minimum of {symbol.min_volume} for {symbol.name}"
    if volume > symbol.max_volume:
        return f"Volume {volume} exceeds the maximum of {symbol.max_volume} for {symbol.name}"
    if volume % symbol.volume_step != 0:
        return f"Volume {volume} is not a multiple of the volume step {symbol.volume_step} for {symbol.name}"
    return None
