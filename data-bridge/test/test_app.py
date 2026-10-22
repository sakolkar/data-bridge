import base64
import os
import secrets
import subprocess
import sys
from pathlib import Path


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import app
from config import ConfigurationError


def test_application_is_initialized() -> None:
    assert isinstance(app, FastAPI)
    assert app.title == "DataBridge"


def test_successful_startup(monkeypatch: pytest.MonkeyPatch, secret_key: str, sqlite_path: Path) -> None:
    with TestClient(app):
        assert app.state.settings.secret_key == secret_key
        assert app.state.settings.sqlite_path == sqlite_path
        assert sqlite_path.parent.is_dir()
        assert not sqlite_path.exists()


def test_invalid_secret_key_aborts_startup(monkeypatch: pytest.MonkeyPatch, sqlite_path: Path):
    monkeypatch.setenv("DATABRIDGE_SECRET_KEY", "invalid")

    with pytest.raises(ConfigurationError, match="DATABRIDGE_SECRET_KEY"):
        with TestClient(app):
            pytest.fail("Startup should have failed")


def test_missing_secret_key_aborts_startup(monkeypatch: pytest.MonkeyPatch, sqlite_path: Path):
    with pytest.raises(ConfigurationError, match="DATABRIDGE_SECRET_KEY"):
        with TestClient(app):
            pytest.fail("Startup should have failed")
