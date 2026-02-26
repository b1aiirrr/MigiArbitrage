"""
MigiArbitrage v2.0 — Pre-Flight Checker
=========================================
Ghost spread prevention using CCXT unified wallet status checks.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional

from config import (
    PREFERRED_NETWORKS,
    NETWORK_CONGESTION_WARN_SECS,
    NETWORK_CONGESTION_BLOCK_SECS,
)

logger = logging.getLogger("migi.preflight")


@dataclass
class PreFlightResult:
    """Result of a pre-flight wallet/network viability check."""
    __slots__ = ["passed", "network", "risk_level", "risk_notes",
                 "deposit_enabled", "withdraw_enabled", "congestion_level"]

    passed: bool
    network: str
    risk_level: str          # "low", "medium", "high"
    risk_notes: list[str]
    deposit_enabled: bool
    withdraw_enabled: bool
    congestion_level: str    # "none", "moderate", "severe"

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "network": self.network,
            "risk_level": self.risk_level,
            "risk_notes": self.risk_notes,
            "deposit_enabled": self.deposit_enabled,
            "withdraw_enabled": self.withdraw_enabled,
            "congestion_level": self.congestion_level,
        }


class PreFlightChecker:
    """
    Validates withdrawal/deposit viability using CCXT engine.
    Prevents ghost spreads by checking wallet statuses before alerting.
    """

    def __init__(self, ccxt_engine=None) -> None:
        """
        Args:
            ccxt_engine: CCXTEngine instance for wallet status checks
        """
        self.ccxt_engine = ccxt_engine

    async def check(
        self,
        asset: str,
        buy_exchange: str,
        sell_exchange: str,
    ) -> PreFlightResult:
        """
        Run pre-flight checks for a cross-exchange transfer.

        Checks:
        1. Withdrawal enabled on buy exchange
        2. Deposit enabled on sell exchange
        3. Network congestion estimate

        Args:
            asset: The crypto asset (e.g., "BTC")
            buy_exchange: Exchange to buy on (withdraw from)
            sell_exchange: Exchange to sell on (deposit to)
        """
        network = PREFERRED_NETWORKS.get(asset, asset)
        risk_notes = []
        risk_level = "low"
        deposit_ok = True
        withdraw_ok = True

        # ── Check wallet status via CCXT ──
        if self.ccxt_engine:
            # Check withdrawal on buy exchange
            buy_status = await self.ccxt_engine.check_wallet_status(
                buy_exchange, asset, network
            )
            if buy_status:
                withdraw_ok = buy_status.get("withdraw_enabled", True)
                if not withdraw_ok:
                    risk_notes.append(f"Withdrawal DISABLED on {buy_exchange}")
                    risk_level = "high"
            else:
                risk_notes.append(f"Unable to verify {buy_exchange} wallet status")
                risk_level = "medium"

            # Check deposit on sell exchange
            sell_status = await self.ccxt_engine.check_wallet_status(
                sell_exchange, asset, network
            )
            if sell_status:
                deposit_ok = sell_status.get("deposit_enabled", True)
                if not deposit_ok:
                    risk_notes.append(f"Deposit DISABLED on {sell_exchange}")
                    risk_level = "high"
            else:
                risk_notes.append(f"Unable to verify {sell_exchange} wallet status")
                if risk_level != "high":
                    risk_level = "medium"
        else:
            risk_notes.append("No CCXT engine — wallet status unchecked")
            risk_level = "medium"

        passed = deposit_ok and withdraw_ok and risk_level != "high"

        return PreFlightResult(
            passed=passed,
            network=network,
            risk_level=risk_level,
            risk_notes=risk_notes,
            deposit_enabled=deposit_ok,
            withdraw_enabled=withdraw_ok,
            congestion_level="none",
        )
