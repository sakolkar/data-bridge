import logging
import threading
import time
from collections.abc import Callable, Iterator
from itertools import chain
from typing import Literal

from connector import Connector
from controllers.transfer import TransferController
from db.models import TransferRecord
from exceptions import ConnectorError, ConnectorPermissionError, TransferStoreError

logger = logging.getLogger(__name__)

PROGRESS_INTERVAL = 1.0

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


def _failure_message(error: TransferStepError, record: TransferRecord) -> str:
    if error.side == "source":
        action = "reading"
        target = f"source '{record.source}:{record.source_file}'"
    else:
        action = "writing"
        target = f"destination '{record.destination}:{record.destination_file}'"
    if isinstance(error.error, ConnectorPermissionError):
        return f"Permission denied {action} {target}. {error.error}"
    return f"Failed {action} {target}. {error.error}"


def run_transfer(
    transfers: TransferController,
    record: TransferRecord,
    source: Connector,
    destination: Connector,
    stop: threading.Event,
) -> None:
    transfer_id = record.id
    logger.info("Transfer %s started.", transfer_id)
    last_saved = time.monotonic()

    def save_progress(byte_count: int) -> None:
        # Throttled so large transfers don't write to the database per chunk.
        nonlocal last_saved
        now = time.monotonic()
        if now - last_saved >= PROGRESS_INTERVAL:
            try:
                transfers.update_progress(transfer_id, byte_count)
            except TransferStoreError:
                logger.warning("Could not record progress of transfer %s.", transfer_id)
            last_saved = now

    try:
        byte_count = transfer_file(
            source,
            record.source_file,
            destination,
            record.destination_file,
            on_progress=save_progress,
            stop=stop,
        )
    except TransferCancelledError as error:
        logger.warning(
            "Transfer %s stopped by shutdown after %d bytes; destination '%s:%s' may be incomplete.",
            transfer_id,
            error.bytes_transferred,
            record.destination,
            record.destination_file,
        )
        transfers.fail(
            transfer_id,
            "Transfer was stopped because the service shut down. The destination "
            "file may be incomplete; remove it before starting the transfer again.",
            error.bytes_transferred,
        )
        return
    except TransferStepError as error:
        message = _failure_message(error, record)
        logger.warning(
            "Transfer %s failed on %s (%s): %s",
            transfer_id,
            error.side,
            type(error.error).__name__,
            message,
        )
        transfers.fail(transfer_id, message)
        return
    except Exception:
        logger.exception("Unexpected failure in transfer %s.", transfer_id)
        transfers.fail(transfer_id, "Transfer failed.")
        return
    transfers.complete(transfer_id, byte_count)
    logger.info("Transfer %s completed (%d bytes).", transfer_id, byte_count)
