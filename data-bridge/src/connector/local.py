from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from stat import S_ISDIR, S_ISLNK, S_ISREG
from typing import Annotated, Literal

from connector import Connector, FileEntry, WriteMode
from exceptions import (
    ConnectorFileExistsError,
    ConnectorFileNotFoundError,
    ConnectorOperationError,
    InvalidConnectorArgumentError,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

ConnectionName = Annotated[str, Field(min_length=1, max_length=128)]


class LocalConnectionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Path must be nonblank and contain no null characters.")
        return value


class LocalConnectionConfiguration(LocalConnectionSettings):
    name: ConnectionName
    type: Literal["local"]


@contextmanager
def _filesystem_errors() -> Iterator[None]:
    try:
        yield
    except FileNotFoundError:
        raise ConnectorFileNotFoundError("File not found.") from None
    except FileExistsError:
        raise ConnectorFileExistsError("File already exists.") from None
    except OSError:
        raise ConnectorOperationError("Local file operation failed.") from None


class LocalConnector(Connector):
    def __init__(self, path: str | Path) -> None:
        if (
            not isinstance(path, (str, Path))
            or not str(path).strip()
            or "\x00" in str(path)
        ):
            raise InvalidConnectorArgumentError("A home directory path is required.")
        with _filesystem_errors():
            self.path = Path(path).resolve(strict=True)
            if not self.path.is_dir():
                raise InvalidConnectorArgumentError("Path must be a directory.")

    def _resolve(self, path: str) -> Path:
        if not isinstance(path, str) or not path or "\x00" in path:
            raise InvalidConnectorArgumentError("A relative file path is required.")
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise InvalidConnectorArgumentError(
                "Path must stay within the home directory."
            )
        candidate = self.path / relative
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.path):
            raise InvalidConnectorArgumentError(
                "Path must stay within the home directory."
            )
        return candidate

    def list(self, recursive: bool = False) -> list[FileEntry]:
        # TODO: pagination/limits to avoid loading too many entries into memory at once
        # TODO: list from subpath

        if not isinstance(recursive, bool):
            raise InvalidConnectorArgumentError("Recursive must be a boolean.")

        def entries(directory: Path) -> Iterator[FileEntry]:
            for entry in directory.iterdir():
                metadata = entry.lstat()
                entry_type: Literal["directory", "file", "symlink"]
                if S_ISLNK(metadata.st_mode):
                    entry_type = "symlink"
                elif S_ISREG(metadata.st_mode):
                    entry_type = "file"
                elif S_ISDIR(metadata.st_mode):
                    if recursive:
                        yield from entries(entry)
                        continue
                    entry_type = "directory"
                else:
                    continue
                yield {
                    "path": entry.relative_to(self.path).as_posix(),
                    "size": metadata.st_size,
                    "type": entry_type,
                }

        with _filesystem_errors():
            return sorted(entries(self.path), key=lambda entry: entry["path"])

    def read(self, path: str) -> bytes:
        # TODO: add streamed reading for large files

        with _filesystem_errors():
            return self._resolve(path).read_bytes()

    def write(self, path: str, data: bytes, *, mode: WriteMode = "create") -> None:
        # TODO: add streamed writing from Transfer-Encoding: chunked

        if not isinstance(data, bytes):
            raise InvalidConnectorArgumentError("File data must be bytes.")
        if mode not in ("create", "replace"):
            raise InvalidConnectorArgumentError("Invalid file write mode.")
        with _filesystem_errors():
            with self._resolve(path).open(
                "xb" if mode == "create" else "r+b"
            ) as stream:
                stream.write(data)
                if mode == "replace":
                    stream.truncate()

    def head(self, path: str, lines: int = 10, max_bytes: int = 65536) -> bytes:
        # TODO: determine file type and pass along to appropriate implementation of file handler

        if any(type(limit) is not int or limit <= 0 for limit in (lines, max_bytes)):
            raise InvalidConnectorArgumentError(
                "Head limits must be positive integers."
            )
        with _filesystem_errors():
            with self._resolve(path).open("rb") as stream:
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
