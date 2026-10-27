import asyncio
import signal
from types import FrameType

from controllers.transfer_manager import TransferManager


def test_second_interrupt_stops_transfers_and_chains_to_server() -> None:
    received: list[int] = []

    def server_handler(sig: int, frame: FrameType | None) -> None:
        received.append(sig)

    async def scenario(manager: TransferManager) -> None:
        manager.install_signal_handlers()
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(0.05)
        assert manager.interrupted
        assert not manager.stopping

        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(0.05)
        assert manager.stopping

    manager = TransferManager(1)
    previous = signal.signal(signal.SIGINT, server_handler)
    try:
        asyncio.run(scenario(manager))
    finally:
        manager.restore_signal_handlers()
        assert signal.getsignal(signal.SIGINT) == server_handler
        signal.signal(signal.SIGINT, previous)
        manager.wait()

    assert received == [signal.SIGINT, signal.SIGINT]
