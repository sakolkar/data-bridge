import base64
import binascii
import os
import re
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from cryptography.fernet import Fernet


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    sqlite_path: Path
    secret_key: str = field(repr=False)


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ConfigurationError(f"{name} must be set to a non-blank value.")
    return value
    

def _validate_secret_key(key: str) -> None:
    try:
        Fernet(key.encode())
    except (ValueError, TypeError):
        raise ConfigurationError(
            "DATABRIDGE_SECRET_KEY must be a valid Fernet key."
        ) from None


def _validate_database_path(value: str) -> Path:
    try:
        path = Path(value)
        if not path.is_absolute():
            raise ConfigurationError("DATABRIDGE_SQLITE_PATH must be an absolute file path.")
        path = path.resolve()
        try:
            mode = path.stat().st_mode
        except FileNotFoundError:
            return path
        if not stat.S_ISREG(mode):
            raise ConfigurationError("DATABRIDGE_SQLITE_PATH must name a regular file.")
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as database:
            if database.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ConfigurationError(
                    "DATABRIDGE_SQLITE_PATH must contain a valid SQLite database."
                )
        return path
    except ConfigurationError:
        raise
    except sqlite3.Error:
        raise ConfigurationError(
            "DATABRIDGE_SQLITE_PATH must contain a readable, valid SQLite database."
        ) from None
    except (OSError, RuntimeError, ValueError):
        raise ConfigurationError(
            "Cannot resolve DATABRIDGE_SQLITE_PATH. Check the path, permissions, and symlinks."
        ) from None


def load_settings() -> Settings:
    secret_key = _required_environment("DATABRIDGE_SECRET_KEY")
    _validate_secret_key(secret_key)
    sqlite_path = _validate_database_path(_required_environment("DATABRIDGE_SQLITE_PATH"))
    return Settings(sqlite_path=sqlite_path, secret_key=secret_key)


def prepare_storage(settings: Settings) -> None:
    try:
        _validate_database_path(str(settings.sqlite_path))
        settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ConfigurationError(
            "Cannot create the SQLite parent directory. Check the path and write permissions."
        ) from None
