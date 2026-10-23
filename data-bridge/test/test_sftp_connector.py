import errno
from io import BytesIO
from stat import S_IFDIR, S_IFREG
from typing import Any

import paramiko
import pytest

from connector.sftp import SFTPConnector
from exceptions import (
    ConnectorFileExistsError,
    ConnectorFileNotFoundError,
    InvalidConnectorArgumentError,
)


class FakeSFTP:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {"file": b"first\nsecond\n"}
        self.open_modes: list[tuple[str, str]] = []
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
        if mode == "x" and path in self.files:
            raise FileExistsError(errno.EEXIST, "File exists", path)
        if path not in self.files and mode != "x":
            raise FileNotFoundError(errno.ENOENT, "No such file", path)
        return FakeRemoteFile(self, path, self.files.get(path, b""))

    def close(self) -> None:
        self.closed = True


class FakeRemoteFile(BytesIO):
    def __init__(self, sftp: FakeSFTP, path: str, contents: bytes) -> None:
        super().__init__(contents)
        self.sftp = sftp
        self.path = path

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

    def load_system_host_keys(self) -> None:
        pass

    def set_missing_host_key_policy(
        self, policy: paramiko.MissingHostKeyPolicy
    ) -> None:
        self.policy = policy

    def connect(self, **kwargs: Any) -> None:
        self.connection = kwargs

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
    assert isinstance(ssh.policy, paramiko.RejectPolicy)
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
