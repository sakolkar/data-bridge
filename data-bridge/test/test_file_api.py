from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app
from connector.local import LocalConnector
from db.models import ConnectionRecord
from exceptions import ConnectorOperationError, ConnectorUnavailableError


@pytest.fixture
def home(tmp_path: Path) -> Path:
    directory = tmp_path / "home"
    directory.mkdir()
    return directory


@pytest.fixture
def client(secret_key: str, sqlite_path: Path, home: Path) -> Iterator[TestClient]:
    with TestClient(app) as client:
        response = client.post(
            "/connection",
            json={"name": "local-files", "type": "local", "path": str(home)},
        )
        assert response.status_code == 201
        yield client


@pytest.mark.parametrize(
    ("content_type", "data"),
    [
        ("application/octet-stream", b"\x00\xff\r\nbinary"),
        ("application/json", b'{"not valid json":\xff}\n'),
        ("text/plain", b"first\r\nsecond\n"),
        ("application/octet-stream", b""),
    ],
)
def test_raw_file_create_read_replace(
    client: TestClient, home: Path, content_type: str, data: bytes
) -> None:
    path = "file + données.bin"
    params = {"name": "local-files", "action": "write", "path": path}
    created = client.post(
        "/connection/file",
        params=params,
        content=data,
        headers={"Content-Type": content_type},
    )
    assert created.status_code == 201
    assert (home / path).read_bytes() == data
    read_params = {**params, "action": "read"}
    read = client.get("/connection/file", params=read_params)
    assert read.status_code == 200
    assert read.headers["content-type"] == "application/octet-stream"
    assert read.content == data

    conflict = client.post(
        "/connection/file", params=params, content=b"do not overwrite"
    )
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "File already exists."}
    assert (home / path).read_bytes() == data

    replacement = b"\xff\x00"
    replaced = client.put("/connection/file", params=params, content=replacement)
    assert replaced.status_code == 204
    assert replaced.content == b""
    assert client.get("/connection/file", params=read_params).content == replacement
    emptied = client.put("/connection/file", params=params, content=b"")
    assert emptied.status_code == 204
    assert (home / path).read_bytes() == b""


def test_list_metadata_and_recursive_scope(client: TestClient, home: Path) -> None:
    (home / "nested").mkdir()
    (home / "nested" / "child").write_bytes(b"child")
    (home / "file").write_bytes(b"data")
    (home / "link").symlink_to("missing")
    params = {"name": "local-files", "action": "list"}
    response = client.get("/connection/file", params=params)
    assert response.status_code == 200
    assert response.json() == [
        {"path": "file", "size": 4, "type": "file"},
        {"path": "link", "size": 7, "type": "symlink"},
        {
            "path": "nested",
            "size": (home / "nested").stat().st_size,
            "type": "directory",
        },
    ]
    recursive = client.get("/connection/file", params={**params, "recursive": "true"})
    assert recursive.status_code == 200
    assert recursive.json() == [
        {"path": "file", "size": 4, "type": "file"},
        {"path": "link", "size": 7, "type": "symlink"},
        {"path": "nested/child", "size": 5, "type": "file"},
    ]


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        ({}, b"first\nsecond\nlast"),
        ({"lines": 1}, b"first\n"),
        ({"max_bytes": 8}, b"first\nse"),
    ],
)
def test_head_returns_bounded_raw_bytes(
    client: TestClient, home: Path, limits: dict[str, int], expected: bytes
) -> None:
    (home / "file").write_bytes(b"first\nsecond\nlast")
    response = client.get(
        "/connection/file",
        params={"name": "local-files", "action": "head", "path": "file", **limits},
    )
    assert response.status_code == 200
    assert response.content == expected


def test_read_and_head_identify_the_file(client: TestClient, home: Path) -> None:
    (home / "dir").mkdir()
    (home / "dir" / "données.txt").write_bytes(b"data\n")
    for action in ("read", "head"):
        response = client.get(
            "/connection/file",
            params={"name": "local-files", "action": action, "path": "dir/données.txt"},
        )
        assert response.status_code == 200
        assert response.headers["content-disposition"] == (
            "inline; filename*=UTF-8''donn%C3%A9es.txt"
        )
        assert response.headers["x-file-path"] == "dir/donn%C3%A9es.txt"


@pytest.mark.parametrize(
    ("method", "action"), [("get", "read"), ("get", "head"), ("put", "write")]
)
def test_missing_files_return_not_found(
    client: TestClient, home: Path, method: str, action: str
) -> None:
    response = client.request(
        method,
        "/connection/file",
        params={"name": "local-files", "action": action, "path": "missing"},
        content=b"data" if method == "put" else None,
    )
    assert response.status_code == 404
    assert not (home / "missing").exists()


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("get", {"action": "write", "path": "file"}),
        ("post", {"action": "read", "path": "file"}),
        ("put", {"action": "list", "path": "file"}),
        ("get", {"action": "unknown"}),
        ("get", {}),
        ("get", {"action": "read"}),
        ("get", {"action": "head"}),
        ("post", {"action": "write"}),
        ("get", {"action": "list", "recursive": "not-a-boolean"}),
        ("get", {"action": "head", "path": "file", "lines": "0"}),
        ("get", {"action": "head", "path": "file", "max_bytes": "0"}),
    ],
)
def test_invalid_file_requests(
    client: TestClient, home: Path, method: str, params: dict[str, str]
) -> None:
    response = client.request(
        method,
        "/connection/file",
        params={"name": "local-files", **params},
        content=b"private-value",
    )
    assert response.status_code == 422
    assert "private-value" not in response.text
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("method", ["get", "post", "put"])
@pytest.mark.parametrize("path", ["../outside", "/outside", "link"])
def test_file_paths_stay_within_home(
    client: TestClient, home: Path, method: str, path: str
) -> None:
    outside = home.parent / "outside"
    outside.write_bytes(b"original")
    (home / "link").symlink_to(outside)
    response = client.request(
        method,
        "/connection/file",
        params={
            "name": "local-files",
            "action": "read" if method == "get" else "write",
            "path": path,
        },
        content=b"replacement" if method != "get" else None,
    )
    assert response.status_code == 422
    assert outside.read_bytes() == b"original"


def test_unknown_connection_returns_not_found(client: TestClient) -> None:
    response = client.get(
        "/connection/file", params={"name": "missing", "action": "list"}
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Connection not found."}


def test_unsupported_connector_type_is_rejected(client: TestClient) -> None:
    app.state.connection_controller.create(
        ConnectionRecord(name="future", type="future", settings="{}")
    )
    response = client.get(
        "/connection/file", params={"name": "future", "action": "list"}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Unsupported connection type."}


@pytest.mark.parametrize("settings", ["private-value", "[]", '{"path":22}'])
def test_invalid_saved_configuration_is_not_exposed(
    client: TestClient, settings: str, caplog: pytest.LogCaptureFixture
) -> None:
    record = app.state.connection_controller.get("local-files")
    record.settings = settings
    app.state.connection_controller.replace(record)
    response = client.get(
        "/connection/file", params={"name": "local-files", "action": "list"}
    )
    assert response.status_code == 500
    assert response.json() == {"detail": "File operation failed."}
    assert "Connector operation failed." in caplog.text
    assert "private-value" not in caplog.text


def test_file_operation_errors_are_not_exposed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def unavailable(connector: LocalConnector, path: str) -> bytes:
        raise ConnectorOperationError("private-value")

    monkeypatch.setattr(LocalConnector, "read", unavailable)
    response = client.get(
        "/connection/file",
        params={"name": "local-files", "action": "read", "path": "file"},
    )
    assert response.status_code == 500
    assert response.json() == {"detail": "File operation failed."}
    assert "Connector operation failed." in caplog.text
    assert "private-value" not in caplog.text


def test_unavailable_backend_returns_descriptive_bad_gateway(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(connector: LocalConnector, recursive: bool = False) -> list[str]:
        raise ConnectorUnavailableError("Could not connect to the SFTP server.")

    monkeypatch.setattr(LocalConnector, "list", unavailable)
    response = client.get(
        "/connection/file", params={"name": "local-files", "action": "list"}
    )
    assert response.status_code == 502
    assert response.json() == {"detail": "Could not connect to the SFTP server."}
