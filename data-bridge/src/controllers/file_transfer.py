import threading
from collections.abc import Callable, Iterator
from itertools import chain
from typing import Literal

from connector import Connector
from exceptions import ConnectorError

TransferSide = Literal["source", "destination"]


class TransferStepError(Exception):
    """
    A connector error raised while reading the source or writing the destination.
    """

    def __init__(self, side: TransferSide, error: ConnectorError) -> None:
        super().__init__(str(error))
        self.side = side
        self.error = error


class TransferCancelledError(Exception):
    """
    The transfer was stopped before completion.
    """

    def __init__(self, bytes_transferred: int) -> None:
        super().__init__(f"Transfer stopped after {bytes_transferred} bytes.")
        self.bytes_transferred = bytes_transferred


def transfer_file(
    source: Connector,
    source_path: str,
    destination: Connector,
    destination_path: str,
    on_progress: Callable[[int], None] | None = None,
    stop: threading.Event | None = None,
) -> int:
    chunks = source.read_chunks(source_path)

    def tagged_source() -> Iterator[bytes]:
        transferred = 0
        try:
            for chunk in chunks:
                if stop is not None and stop.is_set():
                    raise TransferCancelledError(transferred)
                yield chunk
                # Resumed only after the destination has accepted the chunk.
                transferred += len(chunk)
                if on_progress is not None:
                    on_progress(transferred)
        except ConnectorError as error:
            raise TransferStepError("source", error) from error

    reader = tagged_source()
    try:
        # Open the source before touching the destination so a missing or
        # unreadable source does not leave an empty destination file behind.
        first = next(reader, None)
        try:
            return destination.write_chunks(
                destination_path,
                chain(() if first is None else (first,), reader),
            )
        except ConnectorError as error:
            raise TransferStepError("destination", error) from error
    finally:
        reader.close()
