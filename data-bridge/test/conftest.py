from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from cryptography.fernet import Fernet

from config import Settings

# The encrypted field reads configuration during import.
with pytest.MonkeyPatch.context() as environment, TemporaryDirectory() as directory:
    environment.setenv("DATABRIDGE_SECRET_KEY", Fernet.generate_key().decode("ascii"))
    environment.setenv("DATABRIDGE_SQLITE_PATH", str(Path(directory) / "db.sqlite"))
    import db.fields as encrypted_fields


@pytest.fixture(autouse=True)
def field_settings(
    monkeypatch: pytest.MonkeyPatch, secret_key: str, sqlite_path: Path
) -> None:
    monkeypatch.setattr(encrypted_fields, "settings", Settings(sqlite_path, secret_key))


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
