from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from connector import Connector
from connector.registry import resolve_connector
from controllers.file_transfer import run_transfer
from controllers.transfer import TransferController
from controllers.transfer_manager import TransferManager
from db.models import ConnectionName
from exceptions import ConnectorError, ConnectorFileNotFoundError
from routes.connection import Controller

router = APIRouter(prefix="/transfer")


def _validate_file_path(value: str) -> str:
    if not value.strip() or "\x00" in value:
        raise ValueError("File path must be nonblank and contain no null characters.")
    return value


FilePath = Annotated[str, Field(min_length=1), AfterValidator(_validate_file_path)]


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: ConnectionName
    source_file: FilePath
    destination: ConnectionName
    destination_file: FilePath


def get_transfer_controller(request: Request) -> TransferController:
    return request.app.state.transfer_controller


def get_transfer_manager(request: Request) -> TransferManager:
    return request.app.state.transfer_manager


Transfers = Annotated[TransferController, Depends(get_transfer_controller)]
Manager = Annotated[TransferManager, Depends(get_transfer_manager)]


def _check_source(source: Connector, path: str) -> None:
    """Fail fast if the source file is missing or its connection is unreachable."""
    try:
        source.head(path, lines=1, max_bytes=1)
    except ConnectorFileNotFoundError:
        raise ConnectorFileNotFoundError("Source file not found.") from None
    except ConnectorError as error:
        raise type(error)(f"Source: {error}") from None


@router.post("", status_code=202)
def create_transfer(
    transfer: TransferRequest,
    response: Response,
    connections: Controller,
    transfers: Transfers,
    manager: Manager,
) -> dict[str, object]:
    source = resolve_connector(connections, transfer.source)
    destination = resolve_connector(connections, transfer.destination)
    _check_source(source, transfer.source_file)
    record = transfers.create(
        source=transfer.source,
        source_file=transfer.source_file,
        destination=transfer.destination,
        destination_file=transfer.destination_file,
    )
    manager.submit(
        record.id,
        partial(run_transfer, transfers, record, source, destination, manager.stop_event),
    )
    response.headers["Location"] = f"/transfer?transfer_id={record.id}"
    return record.model_dump(mode="json")


@router.get("")
def get_transfer(
    transfers: Transfers,
    transfer_id: Annotated[str, Query(min_length=1, max_length=36)],
) -> dict[str, object]:
    return transfers.get(transfer_id).model_dump(mode="json")
