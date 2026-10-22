"""In-process domain events.

Events are collected while a transaction runs and dispatched only after it
commits (see UnitOfWork), so subscribers never observe rolled-back state.
Handlers run synchronously in the publishing thread; a failing handler is
logged and does not affect the publisher or other handlers.
"""

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    pass


@dataclass(frozen=True)
class QuoteReceived(Event):
    symbol: str
    bid: Decimal
    ask: Decimal
    time: datetime
    source: str


@dataclass(frozen=True)
class OrderFilled(Event):
    order_id: int
    account_id: int
    symbol: str
    side: str
    volume: Decimal
    price: Decimal
    position_id: int


@dataclass(frozen=True)
class OrderRejected(Event):
    order_id: int
    account_id: int
    symbol: str
    reason: str


@dataclass(frozen=True)
class PositionOpened(Event):
    position_id: int
    account_id: int
    symbol: str
    side: str
    volume: Decimal
    price: Decimal


@dataclass(frozen=True)
class PositionReduced(Event):
    """Part or all of a position was closed; remaining_volume is 0 once it is closed."""

    position_id: int
    account_id: int
    symbol: str
    volume: Decimal
    price: Decimal
    realized_profit: Decimal
    remaining_volume: Decimal


@dataclass(frozen=True)
class BalanceChanged(Event):
    account_id: int
    kind: str
    amount: Decimal
    balance: Decimal


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[Handler]] = defaultdict(list)
        self._lock = threading.Lock()
        self.handler_failures = 0

    def subscribe(self, event_type: type[Event], handler: Handler) -> Callable[[], None]:
        """Register a handler for an event type and its subclasses; returns an unsubscribe function."""
        with self._lock:
            self._handlers[event_type].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._handlers[event_type]:
                    self._handlers[event_type].remove(handler)

        return unsubscribe

    def publish(self, event: Event) -> None:
        with self._lock:
            handlers = [h for cls in type(event).__mro__ for h in self._handlers.get(cls, ())]
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                self.handler_failures += 1
                logger.exception("Event handler %r failed for %r", handler, event)
