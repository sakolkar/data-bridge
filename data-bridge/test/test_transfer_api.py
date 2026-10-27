import errno
import threading
import time
from collections.abc import Iterator
from contextlib import closing
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import paramiko
import pytest
from fastapi.testclient import TestClient

from app import app
from db.db import Database
from controllers.transfer import TransferController


class FakeSFTP:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {"remote.bin": b"\x00remote\xff"}
        self.read_sizes: list[int] = []
        self.write_sizes: list[int] = []
        self.denied: set[str] = set()
        self.pipelined = False
        self.prefetched = False

    def stat(self, path: str) -> paramiko.SFTPAttributes:
        if path not in self.files:
            raise FileNotFoundError(errno.ENOENT, "No such file", path)
        return paramiko.SFTPAttributes()

    def open(self, path: str, mode: str) -> BytesIO:
        if path in self.denied:
            raise PermissionError(errno.EACCES, "Permission denied")
        if "x" in mode and path in self.files:
            raise FileExistsError(errno.EEXIST, "File exists", path)
        creating = "w" in mode or "x" in mode
        if path not in self.files and not creating:
            raise FileNotFoundError(errno.ENOENT, "No such file", path)
        contents = b"" if "w" in mode else self.files.get(path, b"")
        # Like paramiko, only "w", "a", or "+" grant write access.
        return FakeRemoteFile(self, path, contents, any(f in mode for f in "wa+"))

    def close(self) -> None:
        pass


class FakeRemoteFile(BytesIO):
    def __init__(
        self, sftp: FakeSFTP, path: str, contents: bytes, writable: bool
    ) -> None:
        super().__init__(contents)
        self.sftp = sftp
        self.path = path
        self.writable_mode = writable
        self.pipelined = False
        self.prefetched = False

    def set_pipelined(self, pipelined: bool = True) -> None:
        self.pipelined = pipelined
        self.sftp.pipelined = pipelined

    def prefetch(self, max_concurrent_requests: int | None = None) -> None:
        self.prefetched = True
        self.sftp.prefetched = True

    def stat(self) -> paramiko.SFTPAttributes:
        attributes = paramiko.SFTPAttributes()
        attributes.st_size = len(self.getvalue())
        return attributes

    def readv(
        self,
        chunks: list[tuple[int, int]],
        max_concurrent_prefetch_requests: int | None = None,
    ) -> Iterator[bytes]:
        self.prefetched = True
        self.sftp.prefetched = True
        for offset, length in chunks:
            self.seek(offset)
            yield self.read(length)

    def write(self, data: bytes) -> int:  # type: ignore[override]
        if not self.writable_mode:
            raise OSError("File not open for writing")
        self.write_chunk_size(data)
        return super().write(data)

    def read(self, size: int = -1) -> bytes:
        self.sftp.read_sizes.append(size)
        return super().read(size)

    def write_chunk_size(self, data: bytes) -> None:
        self.sftp.write_sizes.append(len(data))

    def close(self) -> None:
        if not self.closed:
            self.sftp.files[self.path] = self.getvalue()
        super().close()


class FakeSSH:
    def __init__(self, sftp: FakeSFTP) -> None:
        self.sftp = sftp
        self.fail_connection = False

    def load_system_host_keys(self) -> None:
        pass

    def set_missing_host_key_policy(
        self, policy: paramiko.MissingHostKeyPolicy
    ) -> None:
        pass

    def connect(self, **kwargs: Any) -> None:
        if self.fail_connection:
            raise paramiko.AuthenticationException("private credentials")

    def open_sftp(self) -> FakeSFTP:
        return self.sftp

    def close(self) -> None:
        pass


@pytest.fixture
def transfer_client(
    monkeypatch: pytest.MonkeyPatch,
    secret_key: str,
    sqlite_path: Path,
    tmp_path: Path,
) -> Iterator[tuple[TestClient, Path, FakeSFTP, FakeSSH]]:
    local_home = tmp_path / "local"
    local_home.mkdir()
    remote = FakeSFTP()
    ssh = FakeSSH(remote)
    monkeypatch.setattr(paramiko, "SSHClient", lambda: ssh)

    with TestClient(app) as client:
        local = client.post(
            "/connection",
            json={"name": "local", "type": "local", "path": str(local_home)},
        )
        sftp = client.post(
            "/connection",
            json={
                "name": "remote",
                "type": "sftp",
                "host": "localhost",
                "port": 2222,
                "username": "testuser",
                "password": "testpass",
            },
        )
        assert local.status_code == 201
        assert sftp.status_code == 201
        yield client, local_home, remote, ssh


def _request(
    source: str,
    source_file: str,
    destination: str,
    destination_file: str,
) -> dict[str, str]:
    return {
        "source": source,
        "source_file": source_file,
        "destination": destination,
        "destination_file": destination_file,
    }


def _start(client: TestClient, body: dict[str, str]) -> dict[str, Any]:
    """
    Start a transfer, check it is accepted immediately, and wait for the outcome.
    """
    response = client.post("/transfer", json=body)
    assert response.status_code == 202
    accepted = response.json()
    assert accepted["state"] == "in_progress"
    assert accepted["completed_at"] is None
    assert accepted["bytes_transferred"] == 0
    assert response.headers["location"] == f"/transfer?transfer_id={accepted['id']}"
    return _wait(client, accepted["id"])


def _wait(client: TestClient, transfer_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = client.get("/transfer", params={"transfer_id": transfer_id})
        assert status.status_code == 200
        if status.json()["state"] != "in_progress":
            return status.json()
        time.sleep(0.01)
    raise AssertionError("Transfer did not finish.")


def test_transfer_local_to_sftp_reports_status_and_bytes(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
) -> None:
    client, local_home, remote, _ = transfer_client
    content = b"\x00csv,data\nbinary\xff"
    (local_home / "source.bin").write_bytes(content)

    transfer = _start(client, _request("local", "source.bin", "remote", "copied.bin"))

    assert transfer["state"] == "completed"
    assert transfer["bytes_transferred"] == len(content)
    assert transfer["completed_at"] is not None
    assert transfer["error"] is None
    assert remote.files["copied.bin"] == content
    started = datetime.fromisoformat(transfer["started_at"])
    completed = datetime.fromisoformat(transfer["completed_at"])
    assert completed >= started


def test_transfer_sftp_to_local_copies_bytes(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
) -> None:
    client, local_home, _, _ = transfer_client

    transfer = _start(client, _request("remote", "remote.bin", "local", "received.bin"))

    assert (local_home / "received.bin").read_bytes() == b"\x00remote\xff"
    assert transfer["bytes_transferred"] == len(b"\x00remote\xff")


def test_large_local_to_sftp_transfer_uses_bounded_chunks(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
) -> None:
    client, local_home, remote, _ = transfer_client
    content = bytes(range(256)) * 8193
    (local_home / "large.bin").write_bytes(content)

    transfer = _start(
        client, _request("local", "large.bin", "remote", "large-copy.bin")
    )

    assert transfer["bytes_transferred"] == len(content)
    assert remote.files["large-copy.bin"] == content
    assert remote.write_sizes
    assert max(remote.write_sizes) <= 64 * 1024
    assert remote.pipelined


def test_large_sftp_to_local_transfer_uses_bounded_chunks(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
) -> None:
    client, local_home, remote, _ = transfer_client
    content = bytes(range(256)) * 8193
    remote.files["large.bin"] = content

    transfer = _start(
        client, _request("remote", "large.bin", "local", "large-copy.bin")
    )

    assert transfer["bytes_transferred"] == len(content)
    assert (local_home / "large-copy.bin").read_bytes() == content
    assert remote.read_sizes
    assert min(remote.read_sizes) > 0
    assert max(remote.read_sizes) <= 64 * 1024
    assert remote.prefetched


def test_missing_source_is_rejected_before_starting(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
) -> None:
    client, _, remote, _ = transfer_client

    response = client.post(
        "/transfer", json=_request("local", "missing.bin", "remote", "destination.bin")
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Source file not found."}
    assert "destination.bin" not in remote.files


def test_source_connection_failure_is_rejected_before_starting(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
) -> None:
    client, _, _, ssh = transfer_client
    ssh.fail_connection = True

    response = client.post(
        "/transfer", json=_request("remote", "remote.bin", "local", "copy.bin")
    )

    assert response.status_code == 502
    assert response.json()["detail"].startswith("Source: SFTP authentication failed.")
    assert "testpass" not in response.text


def test_connection_failure_is_recorded_without_exposing_credentials(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
) -> None:
    client, local_home, _, ssh = transfer_client
    (local_home / "source.bin").write_bytes(b"payload")
    ssh.fail_connection = True

    transfer = _start(
        client, _request("local", "source.bin", "remote", "destination.bin")
    )

    assert transfer["state"] == "failed"
    assert transfer["error"] == (
        "Failed writing destination 'remote:destination.bin'. "
        "SFTP authentication failed. Check the configured username and password."
    )
    assert "private credentials" not in str(transfer)


def test_unknown_connection_is_not_started_as_a_transfer(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
) -> None:
    client, _, _, _ = transfer_client
    response = client.post(
        "/transfer",
        json=_request("missing", "source.bin", "remote", "destination.bin"),
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Connection not found."}


def test_existing_destination_is_not_overwritten(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
) -> None:
    client, local_home, remote, _ = transfer_client
    content = b"replacement"
    (local_home / "source.bin").write_bytes(content)
    original = remote.files["remote.bin"]

    transfer = _start(client, _request("local", "source.bin", "remote", "remote.bin"))

    assert transfer["state"] == "failed"
    assert remote.files["remote.bin"] == original


def test_transfer_status_survives_database_restart(
    secret_key: str, sqlite_path: Path
) -> None:
    with TestClient(app):
        controller: TransferController = app.state.transfer_controller
        record = controller.create(
            source="local",
            source_file="source.bin",
            destination="remote",
            destination_file="destination.bin",
        )
        controller.complete(record.id, 7)

    with closing(Database(sqlite_path)) as database:
        database.initialize()
        restarted_controller = TransferController(database.session)
        restarted_controller.initialize()
        restored = restarted_controller.get(record.id)
        assert restored.state == "completed"
        assert restored.bytes_transferred == 7
        assert restored.completed_at is not None


def test_unknown_transfer_returns_not_found(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
) -> None:
    client, _, _, _ = transfer_client
    response = client.get("/transfer", params={"transfer_id": "unknown"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Transfer not found."}


@pytest.mark.parametrize(
    "body",
    [
        {},
        _request("local", " ", "remote", "destination"),
        {**_request("local", "source", "remote", "destination"), "extra": "value"},
    ],
)
def test_invalid_transfer_requests_are_rejected(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
    body: dict[str, str],
) -> None:
    client, _, _, _ = transfer_client
    response = client.post("/transfer", json=body)
    assert response.status_code == 422


def test_sftp_destination_permission_denial_is_recorded_and_logged(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, local_home, remote, _ = transfer_client
    (local_home / "source.bin").write_bytes(b"data")
    remote.denied.add("upload.bin")

    transfer = _start(client, _request("local", "source.bin", "remote", "upload.bin"))

    assert transfer["state"] == "failed"
    assert transfer["error"].startswith(
        "Permission denied writing destination 'remote:upload.bin'. "
        "The SFTP server denied access to 'upload.bin'."
    )
    assert "upload.bin" not in remote.files
    assert f"Transfer {transfer['id']} failed on destination" in caplog.text
    assert "errno=13" in caplog.text
    assert "testpass" not in caplog.text


def test_sftp_source_permission_denial_is_rejected_before_starting(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
) -> None:
    client, local_home, remote, _ = transfer_client
    remote.denied.add("remote.bin")

    response = client.post(
        "/transfer", json=_request("remote", "remote.bin", "local", "copy.bin")
    )

    assert response.status_code == 403
    assert response.json()["detail"].startswith(
        "Source: The SFTP server denied access to 'remote.bin'."
    )
    assert not (local_home / "copy.bin").exists()


def test_transfer_responds_before_the_copy_finishes(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
) -> None:
    client, local_home, remote, _ = transfer_client
    (local_home / "source.bin").write_bytes(b"payload")
    release = threading.Event()
    open_remote = remote.open

    def blocking_open(path: str, mode: str) -> BytesIO:
        assert release.wait(5)
        return open_remote(path, mode)

    remote.open = blocking_open  # type: ignore[method-assign]
    response = client.post(
        "/transfer", json=_request("local", "source.bin", "remote", "copy.bin")
    )
    status = client.get("/transfer", params={"transfer_id": response.json()["id"]})
    release.set()

    assert response.status_code == 202
    assert status.json()["state"] == "in_progress"
    assert "copy.bin" not in remote.files


def test_transfers_left_in_progress_are_failed_on_startup(
    secret_key: str, sqlite_path: Path
) -> None:
    with TestClient(app):
        controller: TransferController = app.state.transfer_controller
        record = controller.create(
            source="local",
            source_file="source.bin",
            destination="remote",
            destination_file="destination.bin",
        )

    with TestClient(app) as client:
        status = client.get("/transfer", params={"transfer_id": record.id}).json()

    assert status["state"] == "failed"
    assert status["completed_at"] is not None
    assert "interrupted by a service restart" in status["error"]


def test_progress_is_reported_while_the_transfer_runs(
    transfer_client: tuple[TestClient, Path, FakeSFTP, FakeSSH],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, local_home, _, _ = transfer_client
    content = bytes(range(256)) * 1024
    (local_home / "large.bin").write_bytes(content)
    monkeypatch.setattr("controllers.file_transfer.PROGRESS_INTERVAL", 0)
    second_write = threading.Event()
    release = threading.Event()
    record_write = FakeRemoteFile.write_chunk_size

    def blocking_write(self: FakeRemoteFile, data: bytes) -> None:
        record_write(self, data)
        if len(self.sftp.write_sizes) == 2:
            second_write.set()
            assert release.wait(5)

    monkeypatch.setattr(FakeRemoteFile, "write_chunk_size", blocking_write)
    response = client.post(
        "/transfer", json=_request("local", "large.bin", "remote", "copy.bin")
    )
    assert second_write.wait(5)
    status = client.get("/transfer", params={"transfer_id": response.json()["id"]})
    release.set()

    assert status.json()["state"] == "in_progress"
    assert status.json()["bytes_transferred"] == 64 * 1024
    final = _wait(client, response.json()["id"])
    assert final["state"] == "completed"
    assert final["bytes_transferred"] == len(content)


def test_shutdown_stops_running_and_cancels_queued_transfers(
    monkeypatch: pytest.MonkeyPatch,
    secret_key: str,
    sqlite_path: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("app.TRANSFER_WORKERS", 1)
    local_home = tmp_path / "local"
    local_home.mkdir()
    (local_home / "large.bin").write_bytes(bytes(range(256)) * 1024)
    remote = FakeSFTP()
    monkeypatch.setattr(paramiko, "SSHClient", lambda: FakeSSH(remote))
    blocked = threading.Event()
    release = threading.Event()
    record_write = FakeRemoteFile.write_chunk_size

    def blocking_write(self: FakeRemoteFile, data: bytes) -> None:
        record_write(self, data)
        if len(self.sftp.write_sizes) == 1:
            blocked.set()
            assert release.wait(5)

    monkeypatch.setattr(FakeRemoteFile, "write_chunk_size", blocking_write)

    with TestClient(app) as client:
        client.post(
            "/connection",
            json={"name": "local", "type": "local", "path": str(local_home)},
        )
        client.post(
            "/connection",
            json={
                "name": "remote",
                "type": "sftp",
                "host": "localhost",
                "port": 2222,
                "username": "testuser",
                "password": "testpass",
            },
        )
        running = client.post(
            "/transfer", json=_request("local", "large.bin", "remote", "a.bin")
        ).json()
        queued = client.post(
            "/transfer", json=_request("local", "large.bin", "remote", "b.bin")
        ).json()
        assert blocked.wait(5)
        manager = app.state.transfer_manager

        def interrupt_twice() -> None:
            manager.stop()
            release.set()

        # Fires once shutdown is already waiting on the running transfer.
        threading.Timer(0.5, interrupt_twice).start()

    with TestClient(app) as client:
        stopped = client.get("/transfer", params={"transfer_id": running["id"]}).json()
        cancelled = client.get("/transfer", params={"transfer_id": queued["id"]}).json()

    assert stopped["state"] == "failed"
    assert stopped["bytes_transferred"] == 64 * 1024
    assert "stopped because the service shut down" in stopped["error"]
    assert cancelled["state"] == "failed"
    assert "before it started" in cancelled["error"]
    assert "Cancelled 1 queued transfer(s)" in caplog.text
    assert "Waiting for 1 running transfer(s) to finish" in caplog.text
    assert f"Transfer {running['id']} stopped by shutdown after 65536 bytes" in (
        caplog.text
    )
