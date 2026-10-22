from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from .events import Event, EventBus


class UnitOfWork:
    """Transaction boundary for application commands.

    Command functions (the public functions in ptrader.services that change
    state) open `with uow.transaction():` themselves; the components they call
    only add and flush. On success the session commits and the events emitted
    during the transaction are published; on any exception the session rolls
    back and the events are discarded.
    """

    def __init__(self, session: Session, events: EventBus):
        self.session = session
        self._events = events
        self._pending: list[Event] = []
        self._active = False

    @contextmanager
    def transaction(self) -> Iterator["UnitOfWork"]:
        if self._active:
            raise RuntimeError("Nested transactions are not supported")
        self._active = True
        self._pending = []
        try:
            yield self
            self.session.commit()
        except BaseException:
            self.session.rollback()
            self._pending = []
            raise
        finally:
            self._active = False
        committed, self._pending = self._pending, []
        for event in committed:
            self._events.publish(event)

    def emit(self, event: Event) -> None:
        if not self._active:
            raise RuntimeError("Events can only be emitted inside a transaction")
        self._pending.append(event)
