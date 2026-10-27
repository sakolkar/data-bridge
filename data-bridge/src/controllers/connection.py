from collections.abc import Sequence

from sqlalchemy import delete, update
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import select

from db.models import ConnectionRecord
from db.session import SessionController
from exceptions import (
    ConnectionNotFoundError,
    ConnectionStoreError,
    DuplicateConnectionError,
)


class ConnectionController(SessionController):
    error = ConnectionStoreError

    def initialize(self) -> None:
        with self._session() as session:
            session.exec(select(ConnectionRecord).limit(0))

    def create(self, record: ConnectionRecord) -> None:
        with self._session() as session:
            result = session.connection().execute(
                insert(ConnectionRecord)
                .values(name=record.name, type=record.type, settings=record.settings)
                .on_conflict_do_nothing(index_elements=[ConnectionRecord.name])
            )
            if result.rowcount == 0:
                raise DuplicateConnectionError()

    def list(self) -> Sequence[ConnectionRecord]:
        with self._session() as session:
            return session.exec(select(ConnectionRecord).order_by(ConnectionRecord.name)).all()

    def get(self, name: str) -> ConnectionRecord:
        with self._session() as session:
            record = session.get(ConnectionRecord, name)
        if record is None:
            raise ConnectionNotFoundError()
        return record

    def replace(self, record: ConnectionRecord) -> None:
        with self._session() as session:
            result = session.connection().execute(
                update(ConnectionRecord)
                .where(ConnectionRecord.name == record.name)
                .values(type=record.type, settings=record.settings)
            )
            if result.rowcount == 0:
                raise ConnectionNotFoundError()

    def delete(self, name: str) -> None:
        with self._session() as session:
            result = session.connection().execute(delete(ConnectionRecord).where(ConnectionRecord.name == name))
            if result.rowcount == 0:
                raise ConnectionNotFoundError()
