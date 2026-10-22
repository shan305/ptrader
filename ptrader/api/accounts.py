from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter
from sqlalchemy.orm import Session

from ..models import Account, OrderStatus, PositionStatus
from ..schemas import (
    AccountCreate,
    AccountOut,
    AccountSummary,
    AccountUpdate,
    BalanceOperation,
    DealOut,
    LedgerEntryOut,
    OrderOut,
    PositionOut,
)
from ..services import accounts, ledger, orders, positions
from .deps import Uow

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _summary(db: Session, account: Account) -> AccountSummary:
    data = AccountOut.model_validate(account).model_dump()
    return AccountSummary(**data, **asdict(accounts.metrics(db, account)))


@router.post("", response_model=AccountSummary, status_code=201)
def create_account(data: AccountCreate, uow: Uow):
    return _summary(uow.session, accounts.create_account(uow, data))


@router.get("", response_model=list[AccountOut])
def list_accounts(uow: Uow):
    return accounts.list_accounts(uow.session)


@router.get("/{account_id}", response_model=AccountSummary)
def get_account(account_id: int, uow: Uow):
    return _summary(uow.session, accounts.get_account(uow.session, account_id))


@router.patch("/{account_id}", response_model=AccountSummary)
def update_account(account_id: int, data: AccountUpdate, uow: Uow):
    return _summary(uow.session, accounts.update_account(uow, account_id, data))


@router.post("/{account_id}/deposit", response_model=AccountSummary)
def deposit(account_id: int, data: BalanceOperation, uow: Uow):
    return _summary(uow.session, accounts.deposit(uow, account_id, data.amount, data.comment))


@router.post("/{account_id}/withdraw", response_model=AccountSummary)
def withdraw(account_id: int, data: BalanceOperation, uow: Uow):
    return _summary(uow.session, accounts.withdraw(uow, account_id, data.amount, data.comment))


@router.get("/{account_id}/positions", response_model=list[PositionOut])
def list_positions(account_id: int, uow: Uow, status: Literal["open", "closed", "all"] = "open"):
    """Open positions by default; status=closed returns position history."""
    db = uow.session
    accounts.get_account(db, account_id)
    selected = None if status == "all" else PositionStatus(status)
    return [positions.position_view(db, p) for p in positions.list_positions(db, account_id, selected)]


@router.get("/{account_id}/orders", response_model=list[OrderOut])
def list_orders(account_id: int, uow: Uow, status: OrderStatus | None = None):
    accounts.get_account(uow.session, account_id)
    return orders.list_orders(uow.session, account_id, status)


@router.get("/{account_id}/deals", response_model=list[DealOut])
def list_deals(account_id: int, uow: Uow):
    accounts.get_account(uow.session, account_id)
    return orders.list_deals(uow.session, account_id)


@router.get("/{account_id}/ledger", response_model=list[LedgerEntryOut])
def list_ledger(account_id: int, uow: Uow):
    """Every balance change, oldest first, with the running balance."""
    accounts.get_account(uow.session, account_id)
    return ledger.entries(uow.session, account_id)
