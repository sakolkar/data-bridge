from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from db import fields


@pytest.fixture(autouse=True)
def encryption(secret_key: str) -> None:
    fields.configure(secret_key)


@pytest.fixture
def secret_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("DATABRIDGE_SECRET_KEY", key)
    return key


@pytest.fixture
def sqlite_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "nested" / "db.sqlite"
    monkeypatch.setenv("DATABRIDGE_SQLITE_PATH", str(path))
    return path
