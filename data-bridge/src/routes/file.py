from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from connector import Connector
from connector.registry import resolve_connector
from routes.connection import Name, Controller

router = APIRouter(prefix="/connection/file")


def get_connector(name: Name, controller: Controller) -> Connector:
    return resolve_connector(controller, name)


FileConnector = Annotated[Connector, Depends(get_connector)]


@router.get("")
def get_file(
    connector: FileConnector,
    action: Annotated[Literal["list", "read", "head"], Query()],
    path: Annotated[str | None, Query(min_length=1)] = None,
    recursive: bool = False,
    lines: Annotated[int, Query(gt=0)] = 10,
    max_bytes: Annotated[int, Query(gt=0)] = 65536,
) -> Response:
    if action == "list":
        if path is not None:
            raise HTTPException(422, "Path is not supported for list.")
        return JSONResponse(connector.list(recursive=recursive))
    if path is None:
        raise HTTPException(422, "Path is required for read and head.")
    if action == "read":
        data = connector.read(path)
    else:
        data = connector.head(path, lines=lines, max_bytes=max_bytes)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers=_file_headers(path),
    )


def _file_headers(path: str) -> dict[str, str]:
    name = PurePosixPath(path.replace("\\", "/")).name or path
    return {
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}",
        "X-File-Path": quote(path, safe="/"),
    }


# TODO: support Transfer-Encoding: chunked
@router.post("", status_code=201)
@router.put("", status_code=204)
async def write_file(
    request: Request,
    connector: FileConnector,
    action: Annotated[Literal["write"], Query()],
    path: Annotated[str, Query(min_length=1)],
) -> Response:
    data = await request.body()
    await run_in_threadpool(
        connector.write,
        path,
        data,
        mode="create" if request.method == "POST" else "replace",
    )
    return Response(status_code=201 if request.method == "POST" else 204)
