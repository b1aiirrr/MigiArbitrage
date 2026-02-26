"""
MigiArbitrage — Base Exchange Client
=====================================
Abstract base class defining the interface all exchange clients must implement.
"""

from __future__ import annotations
import abc
import asyncio
import logging
from typing import Optional

from orderbook import OrderBookManager

logger = logging.getLogger("migi.exchange")


class WalletStatus:
    """Result of a deposit/withdrawal status check."""
    __slots__ = (
        "asset", "network", "deposit_enabled",
        "withdraw_enabled", "withdraw_fee", "min_withdraw",
    )

    def __init__(
        self,
        asset: str,
        network: str,
        deposit_enabled: bool = False,
        withdraw_enabled: bool = False,
        withdraw_fee: float = 0.0,
        min_withdraw: float = 0.0,
    ) -> None:
        self.asset = asset
        self.network = network
        self.deposit_enabled = deposit_enabled
        self.withdraw_enabled = withdraw_enabled
        self.withdraw_fee = withdraw_fee
        self.min_withdraw = min_withdraw

    def is_active(self) -> bool:
        return self.deposit_enabled and self.withdraw_enabled

    def to_dict(self) -> dict:
        return {
            "asset": self.asset,
            "network": self.network,
            "deposit_enabled": self.deposit_enabled,
            "withdraw_enabled": self.withdraw_enabled,
            "withdraw_fee": self.withdraw_fee,
            "min_withdraw": self.min_withdraw,
        }


class ExchangeClient(abc.ABC):
    """
    Abstract base for all exchange WebSocket + REST clients.
    Subclasses must implement connection, subscription, and wallet checks.
    """

    def __init__(self, name: str, book_manager: OrderBookManager) -> None:
        self.name: str = name
        self.book_manager: OrderBookManager = book_manager
        self._connected: bool = False
        self._reconnect_delay: float = 1.0
        self._max_reconnect_delay: float = 60.0
        self._running: bool = True

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Abstract Methods ─────────────────────────

    @abc.abstractmethod
    async def connect(self) -> None:
        """Establish WebSocket connection and subscribe to order book streams."""
        ...

    @abc.abstractmethod
    async def _handle_message(self, data: dict) -> None:
        """Parse an incoming WebSocket message and update the order book."""
        ...

    @abc.abstractmethod
    async def check_wallet_status(
        self, asset: str, network: str
    ) -> Optional[WalletStatus]:
        """Query REST API to verify deposit/withdrawal is enabled."""
        ...

    # ── Reconnection Logic ───────────────────────

    async def run_forever(self) -> None:
        """Main loop with exponential backoff reconnection."""
        while self._running:
            try:
                logger.info("[%s] Connecting...", self.name)
                await self.connect()
            except asyncio.CancelledError:
                logger.info("[%s] Cancelled, shutting down.", self.name)
                break
            except Exception as exc:
                logger.warning(
                    "[%s] Disconnected: %s — reconnecting in %.0fs",
                    self.name, exc, self._reconnect_delay,
                )
                self._connected = False
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )

    def stop(self) -> None:
        self._running = False
