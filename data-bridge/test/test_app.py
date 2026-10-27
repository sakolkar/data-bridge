import os
import subprocess
import sys
from pathlib import Path


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app import app
from controllers.connection import ConnectionController
from db.db import Database
from exceptions import ConfigurationError, ConnectionStoreError


def test_application_is_initialized() -> None:
    assert isinstance(app, FastAPI)
    assert app.title == "DataBridge"


def test_successful_startup(
    monkeypatch: pytest.MonkeyPatch, secret_key: str, sqlite_path: Path
) -> None:
    closed = []
    original_close = Database.close

    def track_close(self: Database) -> None:
        original_close(self)
        closed.append(self)

    monkeypatch.setattr(Database, "close", track_close)
    with TestClient(app) as client:
        database = app.state.database
        assert isinstance(database, Database)
        assert app.state.settings.secret_key == secret_key
        assert app.state.settings.sqlite_path == sqlite_path
        assert sqlite_path.parent.is_dir()
        assert sqlite_path.is_file()
        assert client.get("/connection").json() == []
        assert closed == []
    assert closed == [database]


def test_invalid_secret_key_aborts_startup(
    monkeypatch: pytest.MonkeyPatch, sqlite_path: Path
):
    monkeypatch.setenv("DATABRIDGE_SECRET_KEY", "invalid")

    with pytest.raises(ConfigurationError, match="DATABRIDGE_SECRET_KEY"):
        with TestClient(app):
            pytest.fail("Startup should have failed")


def test_missing_secret_key_aborts_startup(
    monkeypatch: pytest.MonkeyPatch, sqlite_path: Path
):
    monkeypatch.delenv("DATABRIDGE_SECRET_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="DATABRIDGE_SECRET_KEY"):
        with TestClient(app):
            pytest.fail("Startup should have failed")


def test_import_does_not_create_storage(secret_key: str, sqlite_path: Path) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    subprocess.run(
        [sys.executable, "-c", "import app"],
        env=environment,
        check=True,
        capture_output=True,
    )
    assert not sqlite_path.parent.exists()


def test_controller_initialization_failure_aborts_startup(
    monkeypatch: pytest.MonkeyPatch, secret_key: str, sqlite_path: Path
) -> None:
    closed = []

    def fail_initialization(self: ConnectionController) -> None:
        raise ConnectionStoreError("Cannot initialize connection storage.")

    original_close = Database.close

    def track_close(self: Database) -> None:
        original_close(self)
        closed.append(True)

    monkeypatch.setattr(ConnectionController, "initialize", fail_initialization)
    monkeypatch.setattr(Database, "close", track_close)
    with pytest.raises(ConnectionStoreError, match="initialize"):
        with TestClient(app):
            pytest.fail("Startup should have failed")
    assert closed == [True]


def test_database_initialization_failure_closes_database(
    monkeypatch: pytest.MonkeyPatch, secret_key: str, sqlite_path: Path
) -> None:
    closed = []
    original_close = Database.close

    def fail_initialization(self: Database) -> None:
        raise SQLAlchemyError("Cannot initialize database.")

    def track_close(self: Database) -> None:
        original_close(self)
        closed.append(self)

    monkeypatch.setattr(Database, "initialize", fail_initialization)
    monkeypatch.setattr(Database, "close", track_close)
    with pytest.raises(ConnectionStoreError, match="initialize"):
        with TestClient(app):
            pytest.fail("Startup should have failed")
    assert closed == [app.state.database]
