import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from controllers.connection import ConnectionController
from connector.registry import validate_configuration
from db.models import ConnectionName, ConnectionRecord

router = APIRouter(prefix="/connection")


def get_controller(request: Request) -> ConnectionController:
    return request.app.state.connection_controller


Controller = Annotated[ConnectionController, Depends(get_controller)]
Name = Annotated[ConnectionName, Query()]


def get_create_record(body: dict[str, object]) -> ConnectionRecord:
    try:
        configuration = validate_configuration(body)
        fields = configuration.model_dump()
        return ConnectionRecord.model_validate(
            {
                "name": fields.pop("name"),
                "type": fields.pop("type"),
                "settings": json.dumps(fields),
            }
        )
    except ValidationError as error:
        raise RequestValidationError([{**item, "loc": ("body", *item["loc"])} for item in error.errors()]) from None


def get_record(name: Name, body: dict[str, object]) -> ConnectionRecord:
    if "name" in body:
        raise HTTPException(422, "Name must be supplied as a query parameter.")
    return get_create_record({**body, "name": name})


Record = Annotated[ConnectionRecord, Depends(get_record)]


def _summary(record: ConnectionRecord) -> dict[str, str]:
    return {"name": record.name, "type": record.type}


def _payload(record: ConnectionRecord) -> dict[str, object]:
    return {**_summary(record), "settings": json.loads(record.settings)}


@router.post("", status_code=201)
def create_connection(
    record: Annotated[ConnectionRecord, Depends(get_create_record)], controller: Controller
) -> dict[str, str]:
    controller.create(record)
    return _summary(record)


@router.get("")
def get_connection(
    controller: Controller, name: Annotated[ConnectionName | None, Query()] = None
) -> dict[str, object] | list[dict[str, object]]:
    if name is None:
        return [_payload(record) for record in controller.list()]
    return _payload(controller.get(name))


@router.put("")
def replace_connection(record: Record, controller: Controller) -> dict[str, str]:
    controller.replace(record)
    return _summary(record)


@router.delete("", status_code=204)
def delete_connection(name: Name, controller: Controller) -> Response:
    controller.delete(name)
    return Response(status_code=204)
