from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from connection_store import ConnectionStore
from db.models import ConnectionName, ConnectionRecord

router = APIRouter(prefix="/connection")


def get_store(request: Request) -> ConnectionStore:
    return request.app.state.connection_store


Store = Annotated[ConnectionStore, Depends(get_store)]
Name = Annotated[ConnectionName, Query()]


def get_record(name: Name, body: dict[str, object]) -> ConnectionRecord:
    if "name" in body:
        raise HTTPException(422, "Name must be supplied as a query parameter.")
    try:
        return ConnectionRecord.model_validate({**body, "name": name})
    except ValidationError as error:
        raise RequestValidationError(
            [{**item, "loc": ("body", *item["loc"])} for item in error.errors()]
        ) from None


Record = Annotated[ConnectionRecord, Depends(get_record)]


@router.post("", status_code=201)
def create_connection(record: Record, store: Store) -> dict[str, str]:
    return store.create(record)


@router.get("")
def get_connection(
    store: Store, name: Annotated[ConnectionName | None, Query()] = None
) -> dict[str, str] | list[dict[str, str]]:
    if name is None:
        return store.list()
    return store.get_metadata(name)


@router.put("")
def replace_connection(record: Record, store: Store) -> dict[str, str]:
    return store.replace(record)


@router.delete("", status_code=204)
def delete_connection(name: Name, store: Store) -> Response:
    store.delete(name)
    return Response(status_code=204)
