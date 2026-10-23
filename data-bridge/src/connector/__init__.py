from abc import ABC, abstractmethod
from typing import Literal, TypedDict


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
    def write(self, path: str, data: bytes) -> None:
        """
        Create or replace a file with the supplied bytes.
        """

    @abstractmethod
    def head(self, path: str, lines: int = 10, max_bytes: int = 65536) -> bytes:
        """
        Return at most `lines` LF-delimited lines and `max_bytes` bytes.

        Both limits must be positive. The byte cap may truncate a line. Read
        only a bounded prefix, without loading the whole file or parsing content.
        """
