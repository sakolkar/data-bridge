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
    body = {"type": "custom", "settings": "password=private-value;port=22"}
    expected = {"name": "source", "type": "custom"}

    assert client.get("/connection").json() == []
    created = client.post("/connection", params=params, json=body)
    assert created.status_code == 201
    assert created.json() == expected
    assert client.get("/connection", params=params).json() == expected
    assert client.get("/connection").json() == [expected]
    assert client.post("/connection", params=params, json=body).status_code == 409

    replaced = client.put(
        "/connection",
        params=params,
        json={"type": "another", "settings": "replacement"},
    )
    assert replaced.status_code == 200
    assert replaced.json() == {"name": "source", "type": "another"}
    assert app.state.connection_store.get("source").settings == "replacement"

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
        "/connection", params={"name": name}, json={"type": "custom", "settings": ""}
    )
    assert response.status_code == 201
    assert response.json()["name"] == name
    detail = client.get("/connection", params={"name": name})
    assert detail.status_code == 200
    assert detail.json()["name"] == name


@pytest.mark.parametrize("method", ["post", "get", "put", "delete"])
@pytest.mark.parametrize("name", ["", " \t", "x" * 129, "a\x00b", "a\nb"])
def test_invalid_query_names_are_rejected(
    client: TestClient, method: str, name: str
) -> None:
    response = client.request(
        method,
        "/connection",
        params={"name": name},
        json={"type": "custom", "settings": "password=private-value"},
    )
    assert response.status_code == 422
    assert "private-value" not in response.text
    assert client.get("/connection").json() == []


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_name_is_required_for_mutations(client: TestClient, method: str) -> None:
    response = client.request(
        method,
        "/connection",
        json={"type": "custom", "settings": "password=private-value"},
    )
    assert response.status_code == 422
    assert "private-value" not in response.text


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_unknown_connections_return_not_found(client: TestClient, method: str) -> None:
    response = client.request(
        method,
        "/connection",
        params={"name": "missing"},
        json={"type": "custom", "settings": ""},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Connection not found."}


@pytest.mark.parametrize(
    "body",
    [
        {"settings": "password=private-value"},
        {"type": "", "settings": "password=private-value"},
        {"type": " ", "settings": "password=private-value"},
        {"type": "x" * 65, "settings": "password=private-value"},
        {"type": "custom"},
        {"type": "custom", "settings": {"password": "private-value"}},
        {"type": "custom", "settings": ["private-value"]},
        {"type": "custom", "settings": None},
        {"type": "custom", "settings": 22},
        {
            "type": "custom",
            "settings": "password=private-value",
            "name": "body-name",
        },
    ],
)
def test_body_validation_does_not_echo_settings(client: TestClient, body: dict) -> None:
    response = client.post("/connection", params={"name": "source"}, json=body)
    assert response.status_code == 422
    assert "private-value" not in response.text
    assert client.get("/connection").json() == []


def test_invalid_json_does_not_echo_secrets(client: TestClient) -> None:
    response = client.post(
        "/connection",
        params={"name": "source"},
        content='{"type":"custom","settings":{"password":"private-value",}}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert "private-value" not in response.text


def test_nonfinite_settings_are_rejected(client: TestClient) -> None:
    response = client.post(
        "/connection",
        params={"name": "source"},
        content='{"type":"custom","settings":{"password":"private-value","number":NaN}}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert "private-value" not in response.text


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
            params={"name": "persisted"},
            json={"type": "custom", "settings": "password=private-value"},
        )
        assert response.status_code == 201
    with TestClient(app) as restarted:
        response = restarted.get("/connection", params={"name": "persisted"})
        assert response.status_code == 200
        assert response.json() == {"name": "persisted", "type": "custom"}
        assert (
            app.state.connection_store.get("persisted").settings
            == "password=private-value"
        )
