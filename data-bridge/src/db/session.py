from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from exceptions import DatabridgeException


class SessionController:
    error: type[DatabridgeException]

    def __init__(self, session_factory: Callable[[], AbstractContextManager[Session]]) -> None:
        self._session_factory = session_factory

    @contextmanager
    def _session(self) -> Iterator[Session]:
        try:
            with self._session_factory() as session:
                yield session
        except SQLAlchemyError:
            raise self.error() from None
