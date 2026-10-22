from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import Conflict, NotFound
from ..models import Account, Symbol
from ..schemas import AccountCreate, AccountUpdate
from ..uow import UnitOfWork
from . import ledger
from .positions import open_positions
from .pricing import floating, money


@dataclass
class AccountMetrics:
    floating_pnl: Decimal
    equity: Decimal
    margin: Decimal
    free_margin: Decimal
    margin_level: Decimal | None
    open_positions: int


def get_account(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise NotFound(f"Account {account_id} not found")
    return account


def list_accounts(db: Session) -> list[Account]:
    return list(db.scalars(select(Account).order_by(Account.id)))


def metrics(db: Session, account: Account) -> AccountMetrics:
    positions = open_positions(db, account.id)
    floating_pnl = Decimal(0)
    margin = Decimal(0)
    for position in positions:
        _, pnl = floating(db, position, db.get(Symbol, position.symbol), account.currency)
        floating_pnl += pnl or 0
        margin += position.margin
    equity = account.balance + floating_pnl
    return AccountMetrics(
        floating_pnl=money(floating_pnl),
        equity=money(equity),
        margin=money(margin),
        free_margin=money(equity - margin),
        margin_level=money(equity / margin * 100) if margin > 0 else None,
        open_positions=len(positions),
    )


def create_account(uow: UnitOfWork, data: AccountCreate) -> Account:
    with uow.transaction():
        account = Account(name=data.name, currency=data.currency, leverage=data.leverage, balance=Decimal(0))
        uow.session.add(account)
        uow.session.flush()
        if money(data.initial_deposit) > 0:
            ledger.balance_operation(uow, account, data.initial_deposit, "Initial deposit")
    return account


def update_account(uow: UnitOfWork, account_id: int, data: AccountUpdate) -> Account:
    with uow.transaction():
        account = get_account(uow.session, account_id)
        changes = data.model_dump(exclude_unset=True, exclude_none=True)
        leverage_changes = changes.get("leverage", account.leverage) != account.leverage
        if leverage_changes and open_positions(uow.session, account.id):
            raise Conflict("Leverage cannot be changed while positions are open")
        for field, value in changes.items():
            setattr(account, field, value)
    return account


def deposit(uow: UnitOfWork, account_id: int, amount: Decimal, comment: str | None = None) -> Account:
    with uow.transaction():
        account = get_account(uow.session, account_id)
        ledger.balance_operation(uow, account, amount, comment or "Deposit")
    return account


def withdraw(uow: UnitOfWork, account_id: int, amount: Decimal, comment: str | None = None) -> Account:
    with uow.transaction():
        account = get_account(uow.session, account_id)
        free_margin = metrics(uow.session, account).free_margin
        if amount > free_margin:
            raise Conflict(f"Insufficient free margin: requested {amount}, available {free_margin}")
        ledger.balance_operation(uow, account, -amount, comment or "Withdrawal")
    return account
