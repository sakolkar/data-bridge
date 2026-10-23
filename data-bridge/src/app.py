import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from config import load_settings, prepare_storage
from connection_store import ConnectionStore
from db.db import Database
from exceptions import (
    ConnectionNotFoundError,
    ConnectionStoreError,
    DuplicateConnectionError,
)
from routes.connection import router as connection_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = load_settings()
    prepare_storage(settings)
    database = Database(settings.sqlite_path)
    app.state.database = database
    try:
        try:
            database.initialize()
        except SQLAlchemyError:
            raise ConnectionStoreError(
                "Cannot initialize connection storage."
            ) from None
        store = ConnectionStore(database.session)
        store.initialize()
        app.state.settings = settings
        app.state.connection_store = store
        yield
    finally:
        database.close()


app = FastAPI(title="DataBridge", lifespan=lifespan)
app.include_router(connection_router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    details = []
    for item in error.errors():
        location = list(item["loc"])
        if "settings" in location:
            location = location[: location.index("settings") + 1]
        details.append({"loc": location, "msg": item["msg"], "type": item["type"]})
    return JSONResponse(status_code=422, content={"detail": details})


@app.exception_handler(ConnectionStoreError)
async def connection_error_handler(
    request: Request, error: ConnectionStoreError
) -> JSONResponse:
    if isinstance(error, DuplicateConnectionError):
        return JSONResponse(status_code=409, content={"detail": str(error)})
    if isinstance(error, ConnectionNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(error)})
    logger.error("Connection storage operation failed.")
    return JSONResponse(
        status_code=500, content={"detail": "Connection storage is unavailable."}
    )
