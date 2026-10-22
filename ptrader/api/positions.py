from fastapi import APIRouter

from ..schemas import ClosePositionRequest, ExecutionOut, PositionOut
from ..services import orders, positions
from .deps import Uow
from .orders import execution_out

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("/{position_id}", response_model=PositionOut)
def get_position(position_id: int, uow: Uow):
    return positions.position_view(uow.session, positions.get_position(uow.session, position_id))


@router.post("/{position_id}/close", response_model=ExecutionOut)
def close_position(position_id: int, uow: Uow, data: ClosePositionRequest | None = None):
    """Close the whole position, or part of it when volume is given."""
    volume = data.volume if data else None
    return execution_out(uow, orders.close_position(uow, position_id, volume))
