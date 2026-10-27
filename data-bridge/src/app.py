import asyncio
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
    ConnectorError,
    ConnectorFileExistsError,
    ConnectorFileNotFoundError,
    ConnectorPermissionError,
    ConnectorUnavailableError,
    DuplicateConnectionError,
    InvalidConnectorArgumentError,
    TransferNotFoundError,
    TransferStoreError,
)
from routes.connection import router as connection_router
from routes.file import router as file_router
from routes.transfer import router as transfer_router
from transfer_manager import TransferManager
from transfer_store import TransferStore

logger = logging.getLogger(__name__)

TRANSFER_WORKERS = 4


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
        transfer_store = TransferStore(database.session)
        transfer_store.initialize()
        interrupted = transfer_store.fail_interrupted()
        if interrupted:
            logger.warning("Marked %d interrupted transfer(s) as failed.", interrupted)
        app.state.settings = settings
        app.state.connection_store = store
        app.state.transfer_store = transfer_store
        manager = TransferManager(TRANSFER_WORKERS)
        app.state.transfer_manager = manager
        manager.install_signal_handlers()
        try:
            yield
        finally:
            try:
                await _shutdown_transfers(manager, transfer_store)
            finally:
                manager.restore_signal_handlers()
    finally:
        database.close()


async def _shutdown_transfers(
    manager: TransferManager, transfer_store: TransferStore
) -> None:
    cancelled = manager.cancel_queued()
    for transfer_id in cancelled:
        transfer_store.fail(
            transfer_id,
            "Transfer was cancelled because the service shut down before it "
            "started. Start it again.",
        )
    if cancelled:
        logger.warning(
            "Cancelled %d queued transfer(s) that had not started.", len(cancelled)
        )

    running = manager.running
    if running and not manager.stopping:
        hint = " Interrupt again to stop them." if manager.interrupted else ""
        logger.warning(
            "Waiting for %d running transfer(s) to finish before shutting down.%s",
            running,
            hint,
        )

    await asyncio.to_thread(manager.wait)
    if running:
        logger.warning("All transfers have ended; shutdown can continue.")


app = FastAPI(title="DataBridge", lifespan=lifespan)
app.include_router(connection_router)
app.include_router(file_router)
app.include_router(transfer_router)


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


@app.exception_handler(TransferStoreError)
async def transfer_store_error_handler(
    request: Request, error: TransferStoreError
) -> JSONResponse:
    if isinstance(error, TransferNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(error)})
    logger.error("Transfer storage operation failed.")
    return JSONResponse(
        status_code=500, content={"detail": "Transfer storage is unavailable."}
    )


@app.exception_handler(ConnectorError)
async def connector_error_handler(
    request: Request, error: ConnectorError
) -> JSONResponse:
    if isinstance(error, ConnectorFileNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(error)})
    if isinstance(error, ConnectorFileExistsError):
        return JSONResponse(status_code=409, content={"detail": str(error)})
    if isinstance(error, InvalidConnectorArgumentError):
        return JSONResponse(status_code=422, content={"detail": str(error)})
    if isinstance(error, ConnectorPermissionError):
        return JSONResponse(status_code=403, content={"detail": str(error)})
    if isinstance(error, ConnectorUnavailableError):
        logger.warning(
            "Connector unavailable. %s %s: %s", request.method, request.url.path, error
        )
        return JSONResponse(status_code=502, content={"detail": str(error)})
    logger.error(
        "Connector operation failed. %s %s raised %s.",
        request.method,
        request.url.path,
        type(error).__name__,
    )
    return JSONResponse(status_code=500, content={"detail": "File operation failed."})
