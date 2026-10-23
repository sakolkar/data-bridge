from typing import Annotated

from pydantic import ConfigDict, field_validator
from sqlalchemy import String
from sqlmodel import Field, SQLModel

from db.fields import EncryptedString

ConnectionName = Annotated[str, Field(min_length=1, max_length=128)]


class ConnectionRecord(SQLModel, table=True):
    __tablename__ = "connection"
    model_config = ConfigDict(extra="forbid")

    name: ConnectionName = Field(
        primary_key=True, sa_type=String(128, collation="BINARY")
    )
    type: str = Field(min_length=1, max_length=64)
    settings: str = Field(
        sa_type=EncryptedString,
        repr=False,
        schema_extra={"json_schema_extra": {"writeOnly": True}},
    )

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Type must be nonblank.")
        return value
