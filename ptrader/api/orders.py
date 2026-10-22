from fastapi import APIRouter

from ..schemas import DealOut, ExecutionOut, OrderCreate, OrderOut
from ..services import orders, positions
from .deps import Uow

router = APIRouter(prefix="/orders", tags=["orders"])


def execution_out(uow, execution: orders.Execution) -> ExecutionOut:
    return ExecutionOut(
        order=OrderOut.model_validate(execution.order),
        deal=DealOut.model_validate(execution.deal),
        position=positions.position_view(uow.session, execution.position),
    )


@router.post(
    "",
    response_model=ExecutionOut,
    status_code=201,
    responses={422: {"description": "Order rejected; the body carries detail and order_id"}},
)
def place_order(data: OrderCreate, uow: Uow):
    """Place a market order. Omit position_id to open a position, set it to close one."""
    return execution_out(uow, orders.place_order(uow, data))


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, uow: Uow):
    return orders.get_order(uow.session, order_id)
