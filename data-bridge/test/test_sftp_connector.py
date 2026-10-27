import errno
from collections.abc import Iterator
from io import BytesIO
from stat import S_IFDIR, S_IFREG
from typing import Any

import paramiko
import pytest
from paramiko.ssh_exception import NoValidConnectionsError

from connector.sftp import SFTPConnector
from exceptions import (
    ConnectorFileExistsError,
    ConnectorFileNotFoundError,
    ConnectorOperationError,
    ConnectorPermissionError,
    InvalidConnectorArgumentError,
)


class FakeSFTP:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {"file": b"first\nsecond\n"}
        self.open_modes: list[tuple[str, str]] = []
        self.denied: set[str] = set()
        self.closed = False

    def listdir_attr(self, path: str) -> list[paramiko.SFTPAttributes]:
        if path == ".":
            return [
                self._attributes("folder", S_IFDIR, 0),
                self._attributes("file", S_IFREG, len(self.files["file"])),
            ]
        if path == "folder":
            return [self._attributes("child", S_IFREG, 5)]
        raise FileNotFoundError(path)

    def stat(self, path: str) -> paramiko.SFTPAttributes:
        if path not in self.files:
            raise FileNotFoundError(errno.ENOENT, "No such file", path)
        return self._attributes(path, S_IFREG, len(self.files[path]))

    @staticmethod
    def _attributes(
        filename: str, file_type: int, size: int
    ) -> paramiko.SFTPAttributes:
        attributes = paramiko.SFTPAttributes()
        attributes.filename = filename
        attributes.st_mode = file_type | 0o644
        attributes.st_size = size
        return attributes

    def open(self, path: str, mode: str) -> BytesIO:
        self.open_modes.append((path, mode))
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
        self.closed = True


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
        return super().write(data)

    def close(self) -> None:
        if not self.closed:
            self.sftp.files[self.path] = self.getvalue()
        super().close()


class FakeSSH:
    def __init__(self, sftp: FakeSFTP) -> None:
        self.sftp = sftp
        self.connection: dict[str, Any] = {}
        self.policy: paramiko.MissingHostKeyPolicy | None = None
        self.closed = False
        self.connection_error: Exception | None = None

    def load_system_host_keys(self) -> None:
        pass

    def set_missing_host_key_policy(
        self, policy: paramiko.MissingHostKeyPolicy
    ) -> None:
        self.policy = policy

    def connect(self, **kwargs: Any) -> None:
        self.connection = kwargs
        if self.connection_error is not None:
            raise self.connection_error

    def open_sftp(self) -> FakeSFTP:
        return self.sftp

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def sftp(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[SFTPConnector, FakeSFTP, FakeSSH]:
    remote = FakeSFTP()
    ssh = FakeSSH(remote)
    monkeypatch.setattr(paramiko, "SSHClient", lambda: ssh)
    return SFTPConnector("localhost", 8822, "testuser", "testpass"), remote, ssh


def test_sftp_operations_use_trusted_connection_and_relative_paths(
    sftp: tuple[SFTPConnector, FakeSFTP, FakeSSH],
) -> None:
    connector, remote, ssh = sftp

    assert connector.list() == [
        {"path": "file", "size": 13, "type": "file"},
        {"path": "folder", "size": 0, "type": "directory"},
    ]
    assert connector.list(recursive=True) == [
        {"path": "file", "size": 13, "type": "file"},
        {"path": "folder/child", "size": 5, "type": "file"},
    ]
    assert connector.read("file") == b"first\nsecond\n"
    assert connector.head("file", lines=1) == b"first\n"
    connector.write("new", b"created")
    connector.write("file", b"short", mode="replace")
    assert connector.read("new") == b"created"
    assert connector.read("file") == b"short"
    assert ssh.connection == {
        "hostname": "localhost",
        "port": 8822,
        "username": "testuser",
        "password": "testpass",
        "timeout": 10,
        "banner_timeout": 10,
        "auth_timeout": 10,
    }
    assert isinstance(ssh.policy, paramiko.AutoAddPolicy)
    assert remote.closed
    assert ssh.closed


@pytest.mark.parametrize(
    "path", ["", "/absolute", "../outside", "folder/../file", "bad\x00path"]
)
def test_sftp_paths_must_be_relative(path: str) -> None:
    with pytest.raises(InvalidConnectorArgumentError):
        SFTPConnector._resolve(path)


def test_sftp_missing_file_is_translated(
    sftp: tuple[SFTPConnector, FakeSFTP, FakeSSH],
) -> None:
    connector, _, _ = sftp
    with pytest.raises(ConnectorFileNotFoundError):
        connector.read("missing")


def test_sftp_create_does_not_overwrite_existing_file(
    sftp: tuple[SFTPConnector, FakeSFTP, FakeSSH],
) -> None:
    connector, _, _ = sftp
    with pytest.raises(ConnectorFileExistsError):
        connector.write("file", b"overwrite")
    assert connector.read("file") == b"first\nsecond\n"


def test_sftp_permission_denial_names_the_path(
    sftp: tuple[SFTPConnector, FakeSFTP, FakeSSH],
) -> None:
    connector, remote, _ = sftp
    remote.denied.add("upload.bin")
    with pytest.raises(ConnectorPermissionError, match="access to 'upload.bin'"):
        connector.write("upload.bin", b"data")


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (
            paramiko.AuthenticationException("secret detail"),
            "SFTP authentication failed. Check the configured username and password.",
        ),
        (
            NoValidConnectionsError(
                {("localhost", 8822): OSError(errno.ECONNREFUSED, "refused")}
            ),
            "Could not connect to the SFTP server. Check the host, port, and "
            "whether the server is running and reachable.",
        ),
        (
            TimeoutError("private timeout detail"),
            "The SFTP connection timed out. Check server availability and network reachability.",
        ),
    ],
)
def test_sftp_connection_errors_are_actionable_and_safe(
    sftp: tuple[SFTPConnector, FakeSFTP, FakeSSH],
    failure: Exception,
    message: str,
) -> None:
    connector, _, ssh = sftp
    ssh.connection_error = failure

    with pytest.raises(ConnectorOperationError, match=message) as error:
        connector.list()

    assert str(failure) not in str(error.value)
