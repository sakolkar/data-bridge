import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from config import load_settings, prepare_storage
from controllers.connection import ConnectionController
from controllers.transfer import TransferController
from controllers.transfer_manager import TransferManager
from db import fields
from db.db import Database
from exceptions import DatabridgeException
from routes.connection import router as connection_router
from routes.file import router as file_router
from routes.transfer import router as transfer_router

logger = logging.getLogger(__name__)

TRANSFER_WORKERS = 4


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = load_settings()
    prepare_storage(settings)
    fields.configure(settings.secret_key)
    database = Database(settings.sqlite_path)
    app.state.database = database
    try:
        database.initialize()
        connection_controller = ConnectionController(database.session)
        connection_controller.initialize()
        transfer_controller = TransferController(database.session)
        transfer_controller.initialize()
        interrupted = transfer_controller.fail_interrupted()
        if interrupted:
            logger.warning("Marked %d interrupted transfer(s) as failed.", interrupted)
        app.state.connection_controller = connection_controller
        app.state.transfer_controller = transfer_controller
        manager = TransferManager(TRANSFER_WORKERS)
        app.state.transfer_manager = manager
        manager.install_signal_handlers()
        try:
            yield
        finally:
            try:
                await manager.shutdown(transfer_controller)
            finally:
                manager.restore_signal_handlers()
    finally:
        database.close()


app = FastAPI(title="DataBridge", lifespan=lifespan)
app.include_router(connection_router)
app.include_router(file_router)
app.include_router(transfer_router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, error: RequestValidationError) -> JSONResponse:
    details = []
    for item in error.errors():
        location = list(item["loc"])
        if "settings" in location:
            location = location[: location.index("settings") + 1]
        details.append({"loc": location, "msg": item["msg"], "type": item["type"]})
    return JSONResponse(status_code=422, content={"detail": details})


@app.exception_handler(DatabridgeException)
async def databridge_error_handler(request: Request, error: DatabridgeException) -> JSONResponse:
    if error.status_code == 500:
        logger.error("Request failed. %s %s raised %s.", request.method, request.url.path, type(error).__name__)
        return JSONResponse(status_code=500, content={"detail": error.default_message})
    if error.status_code == 502:
        logger.warning("Connector unavailable. %s %s: %s", request.method, request.url.path, error)
    return JSONResponse(status_code=error.status_code, content={"detail": str(error)})
