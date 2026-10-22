"""Account ledger: the only code that changes an account balance.

Every balance change is a LedgerEntry tied to the deal that caused it, and
Account.balance is the running total of those entries.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import InvalidRequest
from ..events import BalanceChanged
from ..models import Account, Deal, DealType, LedgerEntry, LedgerKind
from ..uow import UnitOfWork
from .pricing import money


def post(uow: UnitOfWork, account: Account, kind: LedgerKind, amount: Decimal, deal: Deal) -> LedgerEntry:
    amount = money(amount)
    if amount == 0:
        raise ValueError("Ledger entries must change the balance")
    account.balance = money(account.balance + amount)
    entry = LedgerEntry(account_id=account.id, deal=deal, kind=kind, amount=amount, balance_after=account.balance)
    uow.session.add(entry)
    uow.emit(BalanceChanged(account.id, kind.value, amount, account.balance))
    return entry


def balance_operation(uow: UnitOfWork, account: Account, amount: Decimal, comment: str) -> Deal:
    """Deposit (positive amount) or withdraw (negative amount) money."""
    amount = money(amount)
    if amount == 0:
        raise InvalidRequest("Amount must be at least 0.01")
    deal = Deal(account_id=account.id, type=DealType.BALANCE, profit=amount, comment=comment)
    uow.session.add(deal)
    post(uow, account, LedgerKind.DEPOSIT if amount > 0 else LedgerKind.WITHDRAWAL, amount, deal)
    return deal


def entries(db: Session, account_id: int) -> list[LedgerEntry]:
    return list(db.scalars(select(LedgerEntry).where(LedgerEntry.account_id == account_id).order_by(LedgerEntry.id)))


@dataclass
class Reconciliation:
    balance: Decimal
    ledger_total: Decimal
    last_balance_after: Decimal | None

    @property
    def consistent(self) -> bool:
        return self.balance == self.ledger_total and self.last_balance_after in (None, self.balance)


def reconcile(db: Session, account: Account) -> Reconciliation:
    """Compare the cached balance with the ledger it is derived from."""
    amounts = db.scalars(select(LedgerEntry.amount).where(LedgerEntry.account_id == account.id))
    last = db.scalar(
        select(LedgerEntry.balance_after)
        .where(LedgerEntry.account_id == account.id)
        .order_by(LedgerEntry.id.desc())
        .limit(1)
    )
    return Reconciliation(account.balance, sum(amounts, Decimal(0)), last)
