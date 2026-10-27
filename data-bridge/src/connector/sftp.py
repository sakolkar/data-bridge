import errno
import logging
import socket
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import PurePosixPath
from stat import S_ISDIR, S_ISLNK, S_ISREG
from typing import Annotated, Literal

import paramiko
from paramiko.ssh_exception import NoValidConnectionsError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from connector import CHUNK_SIZE, Connector, FileEntry, WriteMode
from exceptions import (
    ConnectorFileExistsError,
    ConnectorFileNotFoundError,
    ConnectorOperationError,
    ConnectorPermissionError,
    ConnectorUnavailableError,
    InvalidConnectorArgumentError,
)

logger = logging.getLogger(__name__)

_PREFETCH_REQUESTS = 64
_READ_AHEAD_CHUNKS = 32


class SFTPConnectionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    port: Annotated[int, Field(ge=1, le=65535)]
    username: str
    password: str = Field(repr=False)

    @field_validator("host", "username")
    @classmethod
    def validate_nonblank(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Value must be nonblank and contain no null characters.")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not value:
            raise ValueError("Password must be nonblank.")
        return value


class SFTPConnectionConfiguration(SFTPConnectionSettings):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    type: Literal["sftp"]


@contextmanager
def _sftp_errors(path: str | None = None) -> Iterator[None]:
    try:
        yield
    except paramiko.AuthenticationException:
        raise ConnectorUnavailableError(
            "SFTP authentication failed. Check the configured username and password."
        ) from None
    except paramiko.BadHostKeyException:
        raise ConnectorUnavailableError(
            "SFTP host key verification failed. Verify the server host key and the service user's known_hosts file."
        ) from None
    except NoValidConnectionsError:
        raise ConnectorUnavailableError(
            "Could not connect to the SFTP server. Check the host, port, and "
            "whether the server is running and reachable."
        ) from None
    except socket.gaierror:
        raise ConnectorUnavailableError(
            "Could not resolve the SFTP host. Check the hostname and DNS/network configuration."
        ) from None
    except (socket.timeout, TimeoutError):
        raise ConnectorUnavailableError(
            "The SFTP connection timed out. Check server availability and network reachability."
        ) from None
    except OSError as error:
        if error.errno == errno.ENOENT:
            raise ConnectorFileNotFoundError() from None
        if error.errno == errno.EEXIST:
            raise ConnectorFileExistsError() from None
        if error.errno in (errno.EACCES, errno.EPERM):
            logger.warning(
                "SFTP server denied access to %r (errno=%s: %s).",
                path,
                error.errno,
                error.strerror,
            )
            target = f" to '{path}'" if path else ""
            raise ConnectorPermissionError(f"The SFTP server denied access{target}.") from None
        logger.warning(
            "SFTP file operation on %r failed (errno=%s): %s",
            path,
            error.errno,
            error.strerror or error,
        )
        raise ConnectorOperationError("SFTP file operation failed. Check the remote path and permissions.") from None
    except paramiko.SSHException:
        raise ConnectorUnavailableError(
            "The SSH handshake failed. Verify that the server supports SSH/SFTP and that its host key is trusted."
        ) from None
    except EOFError:
        raise ConnectorUnavailableError(
            "The SFTP server closed the connection unexpectedly. Check server availability."
        ) from None
    except paramiko.SFTPError:
        raise ConnectorOperationError(
            "The SFTP server rejected the operation. Check the remote path and permissions."
        ) from None


class SFTPConnector(Connector):
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        timeout: float = 10,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout

    @contextmanager
    def _client(self) -> Iterator[paramiko.SFTPClient]:
        with _sftp_errors():
            ssh = paramiko.SSHClient()
            ssh.load_system_host_keys()
            ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
            try:
                ssh.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    timeout=self.timeout,
                    banner_timeout=self.timeout,
                    auth_timeout=self.timeout,
                )
                sftp = ssh.open_sftp()
                try:
                    yield sftp
                finally:
                    sftp.close()
            finally:
                ssh.close()

    @staticmethod
    def _resolve(path: str) -> str:
        if not isinstance(path, str) or not path or "\x00" in path:
            raise InvalidConnectorArgumentError("A relative file path is required.")
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise InvalidConnectorArgumentError("Path must stay within the home directory.")
        return relative.as_posix()

    @staticmethod
    def _entry(path: str, attributes: paramiko.SFTPAttributes) -> FileEntry | None:
        mode = attributes.st_mode
        if mode is None:
            return None
        if S_ISLNK(mode):
            entry_type: Literal["directory", "file", "symlink"] = "symlink"
        elif S_ISREG(mode):
            entry_type = "file"
        elif S_ISDIR(mode):
            entry_type = "directory"
        else:
            return None
        return {"path": path, "size": attributes.st_size, "type": entry_type}

    def list(self, recursive: bool = False) -> list[FileEntry]:
        if not isinstance(recursive, bool):
            raise InvalidConnectorArgumentError("Recursive must be a boolean.")

        with self._client() as sftp:

            def entries(directory: str) -> Iterator[FileEntry]:
                for attributes in sftp.listdir_attr(directory):
                    name = attributes.filename
                    if name in (".", ".."):
                        continue
                    path = f"{directory}/{name}" if directory != "." else name
                    entry = self._entry(path, attributes)
                    if entry is None:
                        continue
                    if entry["type"] == "directory" and recursive:
                        yield from entries(path)
                    else:
                        yield entry

            return sorted(entries("."), key=lambda entry: entry["path"])

    def read_chunks(self, path: str, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
        resolved = self._resolve(path)
        if type(chunk_size) is not int or chunk_size <= 0:
            raise InvalidConnectorArgumentError("Chunk size must be a positive integer.")
        with self._client() as sftp:
            with _sftp_errors(resolved), sftp.open(resolved, "rb") as stream:
                size = stream.stat().st_size
                if size is None:
                    while chunk := stream.read(chunk_size):
                        yield chunk
                    return
                # Read ahead in bounded windows of concurrent requests. Each
                # window is fully received before it is yielded, so paramiko's
                # prefetch thread is idle if the caller stops early and the
                # connection is closed.
                window = chunk_size * _READ_AHEAD_CHUNKS
                for start in range(0, size, window):
                    end = min(start + window, size)
                    blocks = list(
                        stream.readv(
                            [(offset, min(chunk_size, end - offset)) for offset in range(start, end, chunk_size)],
                            max_concurrent_prefetch_requests=_PREFETCH_REQUESTS,
                        )
                    )
                    yield from blocks
                # Files that grew after stat() are read to their current end.
                stream.seek(size)
                while chunk := stream.read(chunk_size):
                    yield chunk

    def write_chunks(
        self,
        path: str,
        chunks: Iterable[bytes],
        *,
        mode: WriteMode = "create",
    ) -> int:
        resolved = self._resolve(path)
        if mode not in ("create", "replace"):
            raise InvalidConnectorArgumentError("Invalid file write mode.")
        total = 0
        with self._client() as sftp:
            with _sftp_errors(resolved):
                if mode == "create":
                    # Some servers report an exclusive-create conflict as a generic failure.
                    try:
                        sftp.stat(resolved)
                    except OSError as error:
                        if error.errno != errno.ENOENT:
                            raise
                    else:
                        raise ConnectorFileExistsError()
                with sftp.open(resolved, "wx" if mode == "create" else "r+b") as stream:
                    # Don't wait for a server acknowledgement after every packet.
                    stream.set_pipelined(True)
                    for chunk in chunks:
                        if not isinstance(chunk, bytes):
                            raise InvalidConnectorArgumentError("File data must be bytes.")
                        stream.write(chunk)
                        total += len(chunk)
                    if mode == "replace":
                        stream.truncate(total)
        return total

    def head(self, path: str, lines: int = 10, max_bytes: int = 65536) -> bytes:
        resolved = self._resolve(path)
        if any(type(limit) is not int or limit <= 0 for limit in (lines, max_bytes)):
            raise InvalidConnectorArgumentError("Head limits must be positive integers.")
        with self._client() as sftp:
            with _sftp_errors(resolved), sftp.open(resolved, "rb") as stream:
                chunks = []
                remaining = max_bytes
                for _ in range(lines):
                    chunk = stream.readline(remaining)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                    if remaining == 0:
                        break
                return b"".join(chunks)
