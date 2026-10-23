from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

from sqlalchemy import delete, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from db.models import ConnectionRecord
from exceptions import (
    ConnectionNotFoundError,
    ConnectionStoreError,
    DuplicateConnectionError,
)


class ConnectionStore:
    def __init__(
        self,
        session_factory: Callable[[], AbstractContextManager[Session]],
    ) -> None:
        self._session_factory = session_factory

    @contextmanager
    def _session(self) -> Iterator[Session]:
        try:
            with self._session_factory() as session:
                yield session
        except SQLAlchemyError:
            raise ConnectionStoreError("Connection storage is unavailable.") from None

    def initialize(self) -> None:
        with self._session() as session:
            session.exec(select(ConnectionRecord).limit(0))

    def create(self, record: ConnectionRecord) -> dict[str, str]:
        with self._session() as session:
            result = session.connection().execute(
                insert(ConnectionRecord)
                .values(name=record.name, type=record.type, settings=record.settings)
                .on_conflict_do_nothing(index_elements=[ConnectionRecord.name])
            )
            if result.rowcount == 0:
                raise DuplicateConnectionError("Connection already exists.")
        return {"name": record.name, "type": record.type}

    def list(self) -> list[dict[str, str]]:
        with self._session() as session:
            rows = session.exec(
                select(ConnectionRecord).order_by(ConnectionRecord.name)
            ).all()
        return [
            {"name": row.name, "type": row.type, "settings": row.settings}
            for row in rows
        ]

    def get_metadata(self, name: str) -> dict[str, str]:
        with self._session() as session:
            row = session.exec(
                select(ConnectionRecord.name, ConnectionRecord.type).where(
                    ConnectionRecord.name == name
                )
            ).one_or_none()
        if row is None:
            raise ConnectionNotFoundError("Connection not found.")
        return {"name": row.name, "type": row.type}

    def get(self, name: str) -> ConnectionRecord:
        with self._session() as session:
            record = session.get(ConnectionRecord, name)
        if record is None:
            raise ConnectionNotFoundError("Connection not found.")
        return record

    def replace(self, record: ConnectionRecord) -> dict[str, str]:
        with self._session() as session:
            result = session.connection().execute(
                update(ConnectionRecord)
                .where(ConnectionRecord.name == record.name)
                .values(type=record.type, settings=record.settings)
            )
            if result.rowcount == 0:
                raise ConnectionNotFoundError("Connection not found.")
        return {"name": record.name, "type": record.type}

    def delete(self, name: str) -> None:
        with self._session() as session:
            result = session.connection().execute(
                delete(ConnectionRecord).where(ConnectionRecord.name == name)
            )
            if result.rowcount == 0:
                raise ConnectionNotFoundError("Connection not found.")
