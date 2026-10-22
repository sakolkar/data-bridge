import base64
import sqlite3
import traceback
from contextlib import closing
from pathlib import Path

import pytest

from config import ConfigurationError, load_settings, prepare_storage


pytestmark = pytest.mark.usefixtures("secret_key", "sqlite_path")


def test_loading_settings_does_not_create_storage(secret_key: str, sqlite_path: Path) -> None:
    settings = load_settings()

    assert settings.sqlite_path == sqlite_path
    assert settings.secret_key == secret_key
    assert secret_key not in repr(settings)
    assert not settings.sqlite_path.parent.exists()


@pytest.mark.parametrize("name", ["DATABRIDGE_SECRET_KEY", "DATABRIDGE_SQLITE_PATH"])
@pytest.mark.parametrize("value", [None, "", " \t"])
def test_required_environment(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str | None, sqlite_path: Path
) -> None:
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError, match=name):
        load_settings()
    assert not sqlite_path.parent.exists()


@pytest.mark.parametrize(
    "key",
    [
        "not-a-key",
        base64.urlsafe_b64encode(b"x" * 31).decode("ascii"),
        base64.urlsafe_b64encode(b"x" * 33).decode("ascii"),
        "+" * 43 + "=",
        "!" * 43 + "=",
        "é" * 43 + "=",
        "A" * 42 + "B=",
    ],
)
def test_invalid_key_is_not_exposed(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    monkeypatch.setenv("DATABRIDGE_SECRET_KEY", key)

    with pytest.raises(ConfigurationError, match="DATABRIDGE_SECRET_KEY") as caught:
        load_settings()
    assert key not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("value", ["db.sqlite", "../db.sqlite", "."])
def test_invalid_database_path(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("DATABRIDGE_SQLITE_PATH", value)

    with pytest.raises(ConfigurationError, match="DATABRIDGE_SQLITE_PATH"):
        load_settings()


def test_directory_target_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABRIDGE_SQLITE_PATH", str(tmp_path))

    with pytest.raises(ConfigurationError, match="regular file"):
        load_settings()


def test_symlink_loop_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    link = tmp_path / "loop"
    link.symlink_to(link)
    monkeypatch.setenv("DATABRIDGE_SQLITE_PATH", str(link))

    with pytest.raises(ConfigurationError, match="DATABRIDGE_SQLITE_PATH"):
        load_settings()


def test_storage_prepares_parents_without_creating_database(sqlite_path: Path) -> None:
    settings = load_settings()
    prepare_storage(settings)
    prepare_storage(settings)

    assert sqlite_path.parent.is_dir()
    assert not settings.sqlite_path.exists()


def test_existing_database_is_unchanged(sqlite_path: Path) -> None:
    sqlite_path.parent.mkdir()
    with closing(sqlite3.connect(sqlite_path)) as database:
        database.execute("CREATE TABLE records (value TEXT)")
        database.execute("INSERT INTO records VALUES ('existing data')")
        database.commit()
    original = sqlite_path.read_bytes()

    settings = load_settings()
    prepare_storage(settings)

    assert sqlite_path.read_bytes() == original


@pytest.mark.parametrize("corrupt_database", [False, True])
@pytest.mark.parametrize("during_preparation", [False, True])
def test_invalid_existing_database_is_rejected(
    sqlite_path: Path, corrupt_database: bool, during_preparation: bool
) -> None:
    settings = load_settings()
    sqlite_path.parent.mkdir()
    if corrupt_database:
        with closing(sqlite3.connect(sqlite_path)) as database:
            database.execute("CREATE TABLE records (value TEXT)")
            database.commit()
        sqlite_path.write_bytes(sqlite_path.read_bytes()[:100])
    else:
        sqlite_path.write_bytes(b"not a SQLite database")
    original = sqlite_path.read_bytes()

    with pytest.raises(ConfigurationError, match="DATABRIDGE_SQLITE_PATH.*valid SQLite"):
        if during_preparation:
            prepare_storage(settings)
        else:
            load_settings()

    assert sqlite_path.read_bytes() == original


def test_parent_file_collision_is_reported(sqlite_path: Path) -> None:
    sqlite_path.parent.write_text("not a directory")

    with pytest.raises(ConfigurationError, match="DATABRIDGE_SQLITE_PATH"):
        prepare_storage(load_settings())


def test_permission_error_is_reported_safely(
    monkeypatch: pytest.MonkeyPatch, secret_key: str
) -> None:
    settings = load_settings()

    def deny_creation(*args: object, **kwargs: object) -> None:
        raise PermissionError(secret_key)

    monkeypatch.setattr(Path, "mkdir", deny_creation)
    with pytest.raises(ConfigurationError, match="write permissions") as caught:
        prepare_storage(settings)
    assert secret_key not in "".join(traceback.format_exception(caught.value))
