import asyncio
import logging
import signal
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from types import FrameType

from controllers.transfer import TransferController

logger = logging.getLogger(__name__)

_STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class TransferManager:
    """
    Runs transfers in a bounded worker pool and coordinates stopping them.
    """

    def __init__(self, max_workers: int) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="transfer")
        self._lock = threading.Lock()
        self._futures: dict[str, Future[None]] = {}
        self._running: set[str] = set()
        self.stop_event = threading.Event()
        self._interrupts = 0
        self._previous_handlers: dict[int, object] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def submit(self, transfer_id: str, run: Callable[[], None]) -> None:
        def tracked() -> None:
            with self._lock:
                self._running.add(transfer_id)
            try:
                run()
            except Exception:
                # Recording the outcome failed; the next startup marks the transfer as interrupted.
                logger.exception("Could not record the outcome of transfer %s.", transfer_id)
            finally:
                with self._lock:
                    self._running.discard(transfer_id)
                    self._futures.pop(transfer_id, None)

        with self._lock:
            self._futures[transfer_id] = self._executor.submit(tracked)

    @property
    def running(self) -> int:
        with self._lock:
            return len(self._running)

    @property
    def stopping(self) -> bool:
        return self.stop_event.is_set()

    @property
    def interrupted(self) -> bool:
        return self._interrupts > 0

    def stop(self) -> None:
        self.stop_event.set()

    def cancel_queued(self) -> list[str]:
        """
        Cancel transfers that have not started and return their identifiers.
        """
        with self._lock:
            cancelled = [transfer_id for transfer_id, future in self._futures.items() if future.cancel()]
            for transfer_id in cancelled:
                del self._futures[transfer_id]
        return cancelled

    def wait(self) -> None:
        with self._lock:
            futures = list(self._futures.values())
        wait(futures)
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _stop_from_interrupt(self) -> None:
        if self.running:
            logger.warning(
                "Second interrupt received; stopping %d running transfer(s).",
                self.running,
            )
        self.stop()

    def install_signal_handlers(self) -> None:
        """
        Count interrupts while chaining to the server's handlers; the second
        interrupt stops running transfers.
        """
        if threading.current_thread() is not threading.main_thread():
            return
        self._loop = asyncio.get_running_loop()
        for sig in _STOP_SIGNALS:
            previous = signal.getsignal(sig)
            if callable(previous):
                self._previous_handlers[sig] = previous
                signal.signal(sig, self._handle_signal)

    def restore_signal_handlers(self) -> None:
        for sig, previous in self._previous_handlers.items():
            if signal.getsignal(sig) == self._handle_signal:
                signal.signal(sig, previous)  # type: ignore[arg-type]
        self._previous_handlers.clear()

    def _handle_signal(self, sig: int, frame: FrameType | None) -> None:
        self._interrupts += 1
        if self._interrupts == 2 and self._loop is not None:
            # Log from the event loop rather than inside the signal handler,
            # before any transfer observes the stop request.
            self._loop.call_soon_threadsafe(self._stop_from_interrupt)
        previous = self._previous_handlers.get(sig)
        if callable(previous):
            previous(sig, frame)

    async def shutdown(self, transfers: TransferController) -> None:
        cancelled = self.cancel_queued()
        for transfer_id in cancelled:
            transfers.fail(
                transfer_id,
                "Transfer was cancelled because the service shut down before it started. Start it again.",
            )
        if cancelled:
            logger.warning("Cancelled %d queued transfer(s) that had not started.", len(cancelled))

        running = self.running
        if running and not self.stopping:
            hint = " Interrupt again to stop them." if self.interrupted else ""
            logger.warning(
                "Waiting for %d running transfer(s) to finish before shutting down.%s",
                running,
                hint,
            )

        await asyncio.to_thread(self.wait)
        if running:
            logger.warning("All transfers have ended; shutdown can continue.")
