import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app
from exceptions import ConnectionStoreError
from routes.connection import get_store


@pytest.fixture
def client(secret_key: str, sqlite_path: Path) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def test_configuration_crud(client: TestClient) -> None:
    params = {"name": "source"}
    body = {"type": "local", "path": "password=private-value;port=22"}
    expected = {"name": "source", "type": "local"}
    body["name"] = "source"

    assert client.get("/connection").json() == []
    created = client.post("/connection", json=body)
    assert created.status_code == 201
    assert created.json() == expected
    assert client.get("/connection", params=params).json() == expected
    assert client.get("/connection").json() == [
        {**expected, "settings": {"path": body["path"]}}
    ]
    assert client.post("/connection", json=body).status_code == 409

    replaced = client.put(
        "/connection",
        params=params,
        json={"type": "local", "path": "replacement"},
    )
    assert replaced.status_code == 200
    assert replaced.json() == {"name": "source", "type": "local"}
    assert (
        json.loads(app.state.connection_store.get("source").settings)["path"]
        == "replacement"
    )

    deleted = client.delete("/connection", params=params)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get("/connection", params=params).status_code == 404
    assert client.get("/connection").json() == []


@pytest.mark.parametrize(
    "name",
    [" source name ", "données 数据", "a+b&c#d?e/f", "literal%20name", "quote'; --"],
)
def test_query_names_round_trip_without_double_decoding(
    client: TestClient, name: str
) -> None:
    response = client.post(
        "/connection", json={"name": name, "type": "local", "path": "/data"}
    )
    assert response.status_code == 201
    assert response.json()["name"] == name
    detail = client.get("/connection", params={"name": name})
    assert detail.status_code == 200
    assert detail.json()["name"] == name


@pytest.mark.parametrize("method", ["get", "put", "delete"])
@pytest.mark.parametrize("name", ["", " \t", "x" * 129, "a\x00b", "a\nb"])
def test_invalid_query_names_are_rejected(
    client: TestClient, method: str, name: str
) -> None:
    response = client.request(
        method,
        "/connection",
        params={"name": name},
        json={"type": "local", "path": "password=private-value"},
    )
    assert response.status_code == 422
    assert "private-value" not in response.text
    assert client.get("/connection").json() == []


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_name_is_required_for_mutations(client: TestClient, method: str) -> None:
    response = client.request(
        method,
        "/connection",
        json={"type": "local", "path": "password=private-value"},
    )
    assert response.status_code == 422
    assert "private-value" not in response.text


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_unknown_connections_return_not_found(client: TestClient, method: str) -> None:
    response = client.request(
        method,
        "/connection",
        params={"name": "missing"},
        json={"type": "local", "path": "/data"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Connection not found."}


@pytest.mark.parametrize(
    "body",
    [
        {"path": "password=private-value"},
        {"type": "", "path": "password=private-value"},
        {"type": " ", "path": "password=private-value"},
        {"type": "x" * 65, "path": "password=private-value"},
        {"type": "local"},
        {"type": "local", "path": {"password": "private-value"}},
        {"type": "local", "path": ["private-value"]},
        {"type": "local", "path": None},
        {"type": "local", "path": 22},
        {
            "type": "local",
            "path": "password=private-value",
            "extra": "unexpected",
        },
    ],
)
def test_body_validation_does_not_echo_path(client: TestClient, body: dict) -> None:
    response = client.post("/connection", json={"name": "source", **body})
    assert response.status_code == 422
    assert "private-value" not in response.text
    assert client.get("/connection").json() == []


def test_invalid_json_does_not_echo_secrets(client: TestClient) -> None:
    response = client.post(
        "/connection",
        content='{"name":"source","type":"local","path":{"password":"private-value",}}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert "private-value" not in response.text


def test_nonfinite_path_values_are_rejected(client: TestClient) -> None:
    response = client.post(
        "/connection",
        content='{"name":"source","type":"local","path":{"password":"private-value","number":NaN}}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert "private-value" not in response.text


def test_create_requires_name_in_body(client: TestClient) -> None:
    response = client.post(
        "/connection",
        params={"name": "source"},
        json={"type": "local", "path": "private-value"},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "name"]
    assert "private-value" not in response.text
    assert client.get("/connection").json() == []


@pytest.mark.parametrize("method", ["post", "put"])
@pytest.mark.parametrize(
    "fields",
    [
        {"type": "generic", "settings": "private-value"},
        {"type": "custom", "settings": "private-value"},
        {"type": "LOCAL", "path": "/data"},
    ],
)
def test_unimplemented_connection_types_are_rejected(
    client: TestClient, method: str, fields: dict[str, str]
) -> None:
    original = {"name": "source", "type": "local", "path": "/original"}
    assert client.post("/connection", json=original).status_code == 201
    body = dict(fields)
    if method == "post":
        body["name"] = "unsupported"
    response = client.request(
        method, "/connection", params={"name": "source"}, json=body
    )
    assert response.status_code == 422
    assert any(item["loc"] == ["body", "type"] for item in response.json()["detail"])
    assert "private-value" not in response.text
    assert client.get("/connection").json() == [
        {"name": "source", "type": "local", "settings": {"path": "/original"}}
    ]
    assert json.loads(app.state.connection_store.get("source").settings) == {
        "path": "/original"
    }


def test_local_configuration_crud(client: TestClient, tmp_path: Path) -> None:
    home = str(tmp_path / "local home")
    body = {"name": "local-files", "type": "local", "path": home}
    expected = {"name": "local-files", "type": "local"}
    params = {"name": "local-files"}

    created = client.post("/connection", json=body)
    assert created.status_code == 201
    assert created.json() == expected
    assert json.loads(app.state.connection_store.get("local-files").settings) == {
        "path": home
    }
    assert client.post("/connection", json=body).status_code == 409
    assert client.get("/connection", params=params).json() == expected
    assert client.get("/connection").json() == [
        {**expected, "settings": {"path": home}}
    ]

    replacement = str(tmp_path / "replacement")
    updated = client.put(
        "/connection", params=params, json={"type": "local", "path": replacement}
    )
    assert updated.status_code == 200
    assert updated.json() == expected
    assert json.loads(app.state.connection_store.get("local-files").settings) == {
        "path": replacement
    }
    assert client.delete("/connection", params=params).status_code == 204


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"path": ""},
        {"path": " \t"},
        {"path": "private-value\x00"},
        {"path": None},
        {"path": 22},
        {"path": {"password": "private-value"}},
        {"path": "/data", "settings": "private-value"},
        {"path": "/data", "extra": "private-value"},
        {"settings": "private-value"},
    ],
)
@pytest.mark.parametrize("method", ["post", "put"])
def test_invalid_local_configuration(
    client: TestClient, fields: dict[str, object], method: str
) -> None:
    body = {"type": "local", **fields}
    if method == "post":
        body["name"] = "local-files"
    response = client.request(
        method, "/connection", params={"name": "local-files"}, json=body
    )
    assert response.status_code == 422
    assert "private-value" not in response.text
    assert client.get("/connection").json() == []


def test_local_configuration_persists_encrypted(
    secret_key: str, sqlite_path: Path
) -> None:
    home = "/private-home-for-local-connector"
    with TestClient(app) as client:
        response = client.post(
            "/connection", json={"name": "local-files", "type": "local", "path": home}
        )
        assert response.status_code == 201
    assert home.encode() not in sqlite_path.read_bytes()
    with TestClient(app):
        assert json.loads(app.state.connection_store.get("local-files").settings) == {
            "path": home
        }


def test_sftp_configuration_is_encrypted_and_listed(
    client: TestClient, sqlite_path: Path
) -> None:
    password = "manual-test-password"
    response = client.post(
        "/connection",
        json={
            "name": "sftp-files",
            "type": "sftp",
            "host": "localhost",
            "port": 8822,
            "username": "testuser",
            "password": password,
        },
    )
    assert response.status_code == 201
    assert response.json() == {"name": "sftp-files", "type": "sftp"}
    assert password.encode() not in sqlite_path.read_bytes()
    assert client.get("/connection").json() == [
        {
            "name": "sftp-files",
            "type": "sftp",
            "settings": {
                "host": "localhost",
                "port": 8822,
                "username": "testuser",
                "password": password,
            },
        }
    ]
    assert client.get("/connection", params={"name": "sftp-files"}).json() == {
        "name": "sftp-files",
        "type": "sftp",
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"host": "", "port": 8822, "username": "testuser", "password": "private"},
        {"host": "localhost", "port": 0, "username": "testuser", "password": "private"},
        {"host": "localhost", "port": 8822, "username": "", "password": "private"},
        {"host": "localhost", "port": 8822, "username": "testuser", "password": ""},
    ],
)
def test_invalid_sftp_configuration_does_not_echo_password(
    client: TestClient, fields: dict[str, object]
) -> None:
    response = client.post(
        "/connection", json={"name": "sftp-files", "type": "sftp", **fields}
    )
    assert response.status_code == 422
    assert "private" not in response.text


def test_store_errors_are_logged_without_secrets(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    def unavailable_store() -> None:
        raise ConnectionStoreError("private-value")

    app.dependency_overrides[get_store] = unavailable_store
    try:
        response = client.get("/connection")
    finally:
        del app.dependency_overrides[get_store]
    assert response.status_code == 500
    assert response.json() == {"detail": "Connection storage is unavailable."}
    assert "Connection storage operation failed." in caplog.text
    assert "private-value" not in caplog.text


def test_configuration_persists_through_new_lifespan(
    secret_key: str, sqlite_path: Path
) -> None:
    with TestClient(app) as first:
        response = first.post(
            "/connection",
            json={
                "name": "persisted",
                "type": "local",
                "path": "password=private-value",
            },
        )
        assert response.status_code == 201
    with TestClient(app) as restarted:
        response = restarted.get("/connection", params={"name": "persisted"})
        assert response.status_code == 200
        assert response.json() == {"name": "persisted", "type": "local"}
        assert (
            json.loads(app.state.connection_store.get("persisted").settings)["path"]
            == "password=private-value"
        )
