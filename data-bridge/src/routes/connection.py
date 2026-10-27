import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from connection_store import ConnectionStore
from connector.registry import public_settings, validate_configuration
from db.models import ConnectionName, ConnectionRecord

router = APIRouter(prefix="/connection")


def get_store(request: Request) -> ConnectionStore:
    return request.app.state.connection_store


Store = Annotated[ConnectionStore, Depends(get_store)]
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


@router.post("", status_code=201)
def create_connection(record: Annotated[ConnectionRecord, Depends(get_create_record)], store: Store) -> dict[str, str]:
    store.create(record)
    return _summary(record)


@router.get("")
def get_connection(
    store: Store, name: Annotated[ConnectionName | None, Query()] = None
) -> dict[str, str] | list[dict[str, object]]:
    if name is None:
        return [
            {**_summary(record), "settings": public_settings(record.type, json.loads(record.settings))}
            for record in store.list()
        ]
    return {"name": name, "type": store.get_type(name)}


@router.put("")
def replace_connection(record: Record, store: Store) -> dict[str, str]:
    store.replace(record)
    return _summary(record)


@router.delete("", status_code=204)
def delete_connection(name: Name, store: Store) -> Response:
    store.delete(name)
    return Response(status_code=204)
