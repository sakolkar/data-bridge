from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from typing import Literal, TypedDict

WriteMode = Literal["create", "replace"]
TRANSFER_CHUNK_SIZE = 64 * 1024


class FileEntry(TypedDict):
    path: str
    size: int
    type: Literal["directory", "file", "symlink"]


class Connector(ABC):
    """
    File operations using connector-relative paths, never host absolute paths.

    Implementations raise InvalidConnectorArgumentError for invalid paths/limits,
    ConnectorFileNotFoundError for missing files, and ConnectorOperationError for
    other operational failures. No HTTP or transport-specific types are exposed.
    """

    @abstractmethod
    def list(self, recursive: bool = False) -> list[FileEntry]:
        """
        Return relative paths, byte sizes, and entry types.

        Recursive listings include descendants but omit directory entries.
        Symlinks are listed without following them; sizes describe the entry,
        not its target.
        """

    @abstractmethod
    def read(self, path: str) -> bytes:
        """
        Return the complete file contents without decoding or transforming.
        """

    @abstractmethod
    def iter_read(
        self, path: str, chunk_size: int = TRANSFER_CHUNK_SIZE
    ) -> Iterator[bytes]:
        """
        Yield file contents in bounded byte chunks without buffering the file.
        """

    @abstractmethod
    def write(self, path: str, data: bytes, *, mode: WriteMode = "create") -> None:
        """
        Create a file, or replace an existing file in replace mode.

        Create mode raises ConnectorFileExistsError if the entry exists.
        Replace mode raises ConnectorFileNotFoundError if the file is missing.
        """

    @abstractmethod
    def write_chunks(
        self,
        path: str,
        chunks: Iterable[bytes],
        *,
        mode: WriteMode = "create",
    ) -> int:
        """
        Write byte chunks to a file and return the number of bytes written.
        """

    @abstractmethod
    def head(self, path: str, lines: int = 10, max_bytes: int = 65536) -> bytes:
        """
        Return at most `lines` LF-delimited lines and `max_bytes` bytes.

        Both limits must be positive. The byte cap may truncate a line. Read
        only a bounded prefix, without loading the whole file or parsing content.
        """
