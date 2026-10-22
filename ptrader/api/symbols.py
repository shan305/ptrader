from fastapi import APIRouter

from ..schemas import SymbolCreate, SymbolOut, SymbolUpdate
from ..services import symbols
from .deps import Uow

router = APIRouter(prefix="/symbols", tags=["symbols"])


@router.get("", response_model=list[SymbolOut])
def list_symbols(uow: Uow):
    return symbols.list_symbols(uow.session)


@router.post("", response_model=SymbolOut, status_code=201)
def create_symbol(data: SymbolCreate, uow: Uow):
    return symbols.create_symbol(uow, data)


@router.get("/{name}", response_model=SymbolOut)
def get_symbol(name: str, uow: Uow):
    return symbols.get_symbol(uow.session, name)


@router.patch("/{name}", response_model=SymbolOut)
def update_symbol(name: str, data: SymbolUpdate, uow: Uow):
    return symbols.update_symbol(uow, name, data)
