from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, ConfigDict
from sqlalchemy import String
from sqlmodel import Field, SQLModel

from db.fields import EncryptedString


def _validate_nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Value must be nonblank.")
    return value


def _validate_connection_name(value: str) -> str:
    if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Name must be nonblank and contain no control characters.")
    return value


ConnectionName = Annotated[
    str,
    Field(min_length=1, max_length=128),
    AfterValidator(_validate_connection_name),
]


class ConnectionRecord(SQLModel, table=True):
    __tablename__ = "connection"
    model_config = ConfigDict(extra="forbid")

    name: ConnectionName = Field(primary_key=True, sa_type=String(128, collation="BINARY"))
    type: Annotated[str, AfterValidator(_validate_nonblank)] = Field(min_length=1, max_length=64)
    settings: str = Field(sa_type=EncryptedString, repr=False)


class TransferState(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class TransferRecord(SQLModel, table=True):
    __tablename__ = "transfer"

    id: str = Field(primary_key=True)
    source: str
    source_file: str
    destination: str
    destination_file: str
    state: TransferState
    started_at: datetime
    completed_at: datetime | None = None
    bytes_transferred: int | None = None
    error: str | None = None
