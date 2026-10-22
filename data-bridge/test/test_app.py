from fastapi import FastAPI

from app import app


def test_application_is_initialized() -> None:
    assert isinstance(app, FastAPI)
    assert app.title == "DataBridge"
