from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request

from ..uow import UnitOfWork


def get_uow(request: Request) -> Iterator[UnitOfWork]:
    session = request.app.state.session_factory()
    try:
        yield UnitOfWork(session, request.app.state.events)
    finally:
        session.close()


Uow = Annotated[UnitOfWork, Depends(get_uow)]
