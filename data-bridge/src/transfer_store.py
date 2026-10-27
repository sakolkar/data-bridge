from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select, update

from db.models import TransferRecord, TransferState
from exceptions import TransferNotFoundError, TransferStoreError


class TransferStore:
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
            raise TransferStoreError("Transfer storage is unavailable.") from None

    def initialize(self) -> None:
        with self._session() as session:
            session.exec(select(TransferRecord).limit(0))

    def fail_interrupted(self) -> int:
        """
        Mark transfers left in progress by a previous process as failed.
        """
        with self._session() as session:
            result = session.exec(
                update(TransferRecord)
                .where(col(TransferRecord.state) == TransferState.IN_PROGRESS)
                .values(
                    state=TransferState.FAILED,
                    completed_at=datetime.now(timezone.utc),
                    error="Transfer was interrupted by a service restart. " "Start it again.",
                )
            )
            return result.rowcount

    def create(
        self,
        *,
        source: str,
        source_file: str,
        destination: str,
        destination_file: str,
    ) -> TransferRecord:
        record = TransferRecord(
            id=str(uuid4()),
            source=source,
            source_file=source_file,
            destination=destination,
            destination_file=destination_file,
            state=TransferState.IN_PROGRESS,
            started_at=datetime.now(timezone.utc),
            bytes_transferred=0,
        )
        with self._session() as session:
            session.add(record)
        return record

    def update_progress(self, transfer_id: str, byte_count: int) -> None:
        with self._session() as session:
            session.exec(
                update(TransferRecord)
                .where(col(TransferRecord.id) == transfer_id)
                .where(col(TransferRecord.state) == TransferState.IN_PROGRESS)
                .values(bytes_transferred=byte_count)
            )

    def complete(self, transfer_id: str, byte_count: int) -> TransferRecord:
        return self._finish(
            transfer_id,
            state=TransferState.COMPLETED,
            bytes_transferred=byte_count,
            error=None,
        )

    def fail(
        self,
        transfer_id: str,
        error: str,
        bytes_transferred: int | None = None,
    ) -> TransferRecord:
        return self._finish(
            transfer_id,
            state=TransferState.FAILED,
            bytes_transferred=bytes_transferred,
            error=error,
        )

    def _finish(
        self,
        transfer_id: str,
        *,
        state: TransferState,
        bytes_transferred: int | None,
        error: str | None,
    ) -> TransferRecord:
        with self._session() as session:
            record = session.get(TransferRecord, transfer_id)
            if record is None:
                raise TransferNotFoundError("Transfer not found.")
            record.state = state
            record.completed_at = datetime.now(timezone.utc)
            if bytes_transferred is not None:
                record.bytes_transferred = bytes_transferred
            record.error = error
            session.add(record)
        return record

    def get(self, transfer_id: str) -> TransferRecord:
        with self._session() as session:
            record = session.exec(select(TransferRecord).where(TransferRecord.id == transfer_id)).one_or_none()
        if record is None:
            raise TransferNotFoundError("Transfer not found.")
        return record
