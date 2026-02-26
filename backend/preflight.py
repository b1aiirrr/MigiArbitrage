"""
MigiArbitrage — Pre-Flight Check Module
========================================
Validates wallet deposit/withdrawal status and network congestion
before an arbitrage alert is dispatched. Prevents "ghost spreads".
"""

from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from backend.config import (
    PREFERRED_NETWORKS,
    WITHDRAWAL_FEES,
    NETWORK_CONGESTION_WARN_SECS,
    NETWORK_CONGESTION_BLOCK_SECS,
)
from backend.exchanges.base import ExchangeClient, WalletStatus

logger = logging.getLogger("migi.preflight")


@dataclass
class PreFlightResult:
    """Result of pre-flight validation checks."""
    passed: bool = False
    buy_wallet: Optional[WalletStatus] = None
    sell_wallet: Optional[WalletStatus] = None
    network: str = ""
    withdrawal_fee: float = 0.0
    risk_level: str = "low"  # low | medium | high
    risk_notes: list[str] = field(default_factory=list)
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "network": self.network,
            "withdrawal_fee": self.withdrawal_fee,
            "risk_level": self.risk_level,
            "risk_notes": self.risk_notes,
            "buy_wallet": self.buy_wallet.to_dict() if self.buy_wallet else None,
            "sell_wallet": self.sell_wallet.to_dict() if self.sell_wallet else None,
        }


class PreFlightChecker:
    """
    Performs deposit/withdrawal viability checks before alert dispatch.

    Checks:
    1. Wallet status on both buy and sell exchanges
    2. Network congestion estimation
    3. Withdrawal fee accuracy
    """

    def __init__(self, exchange_clients: dict[str, ExchangeClient]) -> None:
        self._clients = exchange_clients
        # Cache wallet statuses for 60 seconds to avoid excessive API calls
        self._cache: dict[str, tuple[WalletStatus, float]] = {}
        self._cache_ttl: float = 60.0

    async def check(
        self,
        asset: str,
        buy_exchange: str,
        sell_exchange: str,
    ) -> PreFlightResult:
        """
        Run full pre-flight checks for an arbitrage opportunity.

        Args:
            asset: The cryptocurrency symbol (e.g., "BTC")
            buy_exchange: Exchange to buy from (e.g., "binance")
            sell_exchange: Exchange to sell on (e.g., "kraken")

        Returns:
            PreFlightResult with pass/fail status and risk assessment.
        """
        result = PreFlightResult()
        network = PREFERRED_NETWORKS.get(asset, asset)
        result.network = network

        # ── 1. Check wallet status on both exchanges ──

        buy_client = self._clients.get(buy_exchange)
        sell_client = self._clients.get(sell_exchange)

        if not buy_client or not sell_client:
            result.risk_notes.append(f"Exchange client not available")
            result.risk_level = "high"
            return result

        # Run both checks concurrently
        buy_status, sell_status = await asyncio.gather(
            self._cached_wallet_check(buy_client, asset, network),
            self._cached_wallet_check(sell_client, asset, network),
            return_exceptions=True,
        )

        # Handle buy exchange wallet
        if isinstance(buy_status, Exception) or buy_status is None:
            result.risk_notes.append(f"Could not verify {buy_exchange} wallet status")
            result.risk_level = "high"
        else:
            result.buy_wallet = buy_status
            if not buy_status.withdraw_enabled:
                result.risk_notes.append(f"Withdrawal DISABLED on {buy_exchange} for {asset}/{network}")
                result.risk_level = "high"

        # Handle sell exchange wallet
        if isinstance(sell_status, Exception) or sell_status is None:
            result.risk_notes.append(f"Could not verify {sell_exchange} wallet status")
            result.risk_level = "high"
        else:
            result.sell_wallet = sell_status
            if not sell_status.deposit_enabled:
                result.risk_notes.append(f"Deposit DISABLED on {sell_exchange} for {asset}/{network}")
                result.risk_level = "high"

        # ── 2. Get withdrawal fee ──

        if result.buy_wallet and result.buy_wallet.withdraw_fee > 0:
            result.withdrawal_fee = result.buy_wallet.withdraw_fee
        else:
            # Fall back to configured defaults
            result.withdrawal_fee = WITHDRAWAL_FEES.get(asset, {}).get(network, 0.0)

        # ── 3. Network congestion check ──

        congestion = await self._estimate_network_congestion(asset, network)
        if congestion is not None:
            if congestion >= NETWORK_CONGESTION_BLOCK_SECS:
                result.risk_notes.append(
                    f"Network {network} heavily congested (~{congestion // 60:.0f}min avg tx time)"
                )
                result.risk_level = "high"
            elif congestion >= NETWORK_CONGESTION_WARN_SECS:
                result.risk_notes.append(
                    f"Network {network} moderately congested (~{congestion // 60:.0f}min avg tx time)"
                )
                if result.risk_level == "low":
                    result.risk_level = "medium"

        # ── Final verdict ──

        result.passed = result.risk_level != "high"
        return result

    async def _cached_wallet_check(
        self, client: ExchangeClient, asset: str, network: str
    ) -> Optional[WalletStatus]:
        """Check wallet status with 60s cache to avoid API rate limits."""
        cache_key = f"{client.name}:{asset}:{network}"
        now = time.time()

        if cache_key in self._cache:
            status, cached_at = self._cache[cache_key]
            if now - cached_at < self._cache_ttl:
                return status

        status = await client.check_wallet_status(asset, network)
        if status:
            self._cache[cache_key] = (status, now)
        return status

    async def _estimate_network_congestion(
        self, asset: str, network: str
    ) -> Optional[float]:
        """
        Estimate network congestion for a blockchain.
        Returns average transaction time in seconds, or None if unavailable.

        Note: In production, this would query blockchain explorers or
        exchange-reported network stats. Here we return None (no congestion data)
        which the checker treats as "unknown / assume OK".
        """
        # TODO: Integrate with blockchain explorer APIs for live congestion data
        # e.g., Etherscan gas tracker, Solana TPS monitor, etc.
        return None
