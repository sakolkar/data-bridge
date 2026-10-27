import logging
import threading
import time
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from controllers.connection import ConnectionController
from connector import Connector
from connector.resolution import resolve_connector
from exceptions import (
    ConnectorError,
    ConnectorFileNotFoundError,
    ConnectorPermissionError,
    TransferStoreError,
)
from controllers.file_transfer import TransferCancelledError, TransferStepError, transfer_file
from controllers.transfer_manager import TransferManager
from controllers.transfer import TransferController

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transfer")

PROGRESS_INTERVAL = 1.0


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=128)
    source_file: str = Field(min_length=1)
    destination: str = Field(min_length=1, max_length=128)
    destination_file: str = Field(min_length=1)

    @field_validator("source_file", "destination_file")
    @classmethod
    def validate_file_path(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("File path must be nonblank and contain no null characters.")
        return value


def get_transfer_controller(request: Request) -> TransferController:
    return request.app.state.transfer_controller


def get_connection_controller(request: Request) -> ConnectionController:
    return request.app.state.connection_controller


def _failure_message(error: TransferStepError, transfer: TransferRequest) -> str:
    if error.side == "source":
        action = "reading"
        target = f"source '{transfer.source}:{transfer.source_file}'"
    else:
        action = "writing"
        target = f"destination '{transfer.destination}:{transfer.destination_file}'"
    if isinstance(error.error, ConnectorPermissionError):
        return f"Permission denied {action} {target}. {error.error}"
    return f"Failed {action} {target}. {error.error}"


def _run_transfer(
    transfers: TransferController,
    transfer_id: str,
    transfer: TransferRequest,
    source: Connector,
    destination: Connector,
    stop: threading.Event,
) -> None:
    logger.info("Transfer %s started.", transfer_id)
    last_saved = time.monotonic()

    def save_progress(byte_count: int) -> None:
        # Throttled so large transfers don't write to the database per chunk.
        nonlocal last_saved
        now = time.monotonic()
        if now - last_saved >= PROGRESS_INTERVAL:
            try:
                transfers.update_progress(transfer_id, byte_count)
            except TransferStoreError:
                logger.warning("Could not record progress of transfer %s.", transfer_id)
            last_saved = now

    try:
        byte_count = transfer_file(
            source,
            transfer.source_file,
            destination,
            transfer.destination_file,
            on_progress=save_progress,
            stop=stop,
        )
    except TransferCancelledError as error:
        logger.warning(
            "Transfer %s stopped by shutdown after %d bytes; destination " "'%s:%s' may be incomplete.",
            transfer_id,
            error.bytes_transferred,
            transfer.destination,
            transfer.destination_file,
        )
        transfers.fail(
            transfer_id,
            "Transfer was stopped because the service shut down. The destination "
            "file may be incomplete; remove it before starting the transfer again.",
            error.bytes_transferred,
        )
        return
    except TransferStepError as error:
        message = _failure_message(error, transfer)
        logger.warning(
            "Transfer %s failed on %s (%s): %s",
            transfer_id,
            error.side,
            type(error.error).__name__,
            message,
        )
        transfers.fail(transfer_id, message)
        return
    except Exception:
        logger.exception("Unexpected failure in transfer %s.", transfer_id)
        transfers.fail(transfer_id, "Transfer failed.")
        return
    transfers.complete(transfer_id, byte_count)
    logger.info("Transfer %s completed (%d bytes).", transfer_id, byte_count)


def _run_transfer_safely(
    transfers: TransferController,
    transfer_id: str,
    transfer: TransferRequest,
    source: Connector,
    destination: Connector,
    stop: threading.Event,
) -> None:
    try:
        _run_transfer(transfers, transfer_id, transfer, source, destination, stop)
    except Exception:
        # Recording the outcome failed; the record stays in progress until the
        # next startup marks it as interrupted.
        logger.exception("Could not record the outcome of transfer %s.", transfer_id)


def _check_source(source: Connector, path: str) -> None:
    """
    Fail fast, before accepting the transfer, if the source file is missing or
    its connection is unreachable.
    """
    try:
        source.head(path, lines=1, max_bytes=1)
    except ConnectorFileNotFoundError:
        raise ConnectorFileNotFoundError("Source file not found.") from None
    except ConnectorError as error:
        raise type(error)(f"Source: {error}") from None


@router.post("", status_code=202)
def create_transfer(
    transfer: TransferRequest,
    request: Request,
) -> JSONResponse:
    connections = get_connection_controller(request)
    transfers = get_transfer_controller(request)
    manager: TransferManager = request.app.state.transfer_manager
    source: Connector = resolve_connector(connections, transfer.source)
    destination: Connector = resolve_connector(connections, transfer.destination)
    _check_source(source, transfer.source_file)
    record = transfers.create(
        source=transfer.source,
        source_file=transfer.source_file,
        destination=transfer.destination,
        destination_file=transfer.destination_file,
    )
    manager.submit(
        record.id,
        partial(
            _run_transfer_safely,
            transfers,
            record.id,
            transfer,
            source,
            destination,
            manager.stop_event,
        ),
    )
    return JSONResponse(
        status_code=202,
        content=record.model_dump(mode="json"),
        headers={"Location": f"/transfer?transfer_id={record.id}"},
    )


@router.get("")
def get_transfer(
    request: Request,
    transfer_id: Annotated[str, Query(min_length=1, max_length=36)],
) -> dict[str, object]:
    record = get_transfer_controller(request).get(transfer_id)
    return record.model_dump(mode="json")
