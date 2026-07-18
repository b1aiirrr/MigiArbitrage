"""
MigiArbitrage v3.0 — Smart Cross-Chain Gas Router
===================================================
Dynamically finds the cheapest viable withdrawal path between
two exchanges for any asset, using the TTL fee cache.

Instead of hardcoded PREFERRED_NETWORKS, this module enumerates
all shared networks between the buy and sell exchange, checks
wallet viability on both sides, and selects the route with the
lowest USD-equivalent fee.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from config import WITHDRAWAL_FEES, PREFERRED_NETWORKS

if TYPE_CHECKING:
    from ccxt_engine import CCXTEngine

logger = logging.getLogger("migi.gas_router")


@dataclass
class RouteResult:
    """Result of a cross-chain route evaluation."""
    __slots__ = (
        "network", "withdrawal_fee", "deposit_enabled",
        "withdraw_enabled", "is_viable", "fee_usd_est",
        "alternative_savings_usd",
    )

    network: str
    withdrawal_fee: float       # Fee in asset units
    deposit_enabled: bool
    withdraw_enabled: bool
    is_viable: bool             # Both wallets open
    fee_usd_est: float          # Estimated cost in USD
    alternative_savings_usd: float  # Savings vs. default preferred network

    def __init__(
        self,
        network: str,
        withdrawal_fee: float,
        deposit_enabled: bool,
        withdraw_enabled: bool,
        is_viable: bool,
        fee_usd_est: float,
        alternative_savings_usd: float = 0.0,
    ) -> None:
        self.network = network
        self.withdrawal_fee = withdrawal_fee
        self.deposit_enabled = deposit_enabled
        self.withdraw_enabled = withdraw_enabled
        self.is_viable = is_viable
        self.fee_usd_est = fee_usd_est
        self.alternative_savings_usd = alternative_savings_usd


def find_cheapest_route(
    asset: str,
    buy_exchange: str,
    sell_exchange: str,
    asset_price_usd: float,
    ccxt_engine: CCXTEngine,
) -> Optional[RouteResult]:
    """
    Enumerate all shared networks for `asset` between buy and sell exchange.
    Return the cheapest viable route (lowest USD fee, both wallets open).

    Falls back to config-based PREFERRED_NETWORKS if cache is empty.

    Args:
        asset: The crypto asset (e.g., "BTC", "ETH", "USDT")
        buy_exchange: Exchange ID to withdraw from
        sell_exchange: Exchange ID to deposit into
        asset_price_usd: Current USD price for fee conversion
        ccxt_engine: CCXTEngine instance for cached lookups

    Returns:
        RouteResult for the cheapest viable route, or None if no viable route exists.
    """
    # ── Collect all available networks from both exchanges ──
    buy_networks = ccxt_engine.get_all_cached_networks(buy_exchange, asset)
    sell_networks = ccxt_engine.get_all_cached_networks(sell_exchange, asset)

    # Find shared networks (available on both sides)
    shared_networks = set(buy_networks.keys()) & set(sell_networks.keys())

    # If no shared networks from cache, fall back to config
    if not shared_networks:
        preferred = PREFERRED_NETWORKS.get(asset)
        if preferred:
            shared_networks = {preferred}
        else:
            return None

    # ── Calculate default network cost for savings comparison ──
    default_network = PREFERRED_NETWORKS.get(asset, "")
    default_fee_usd = 0.0
    if default_network:
        default_w_fee = WITHDRAWAL_FEES.get(asset, {}).get(default_network, 0.0)
        default_fee_usd = default_w_fee * asset_price_usd

    # ── Evaluate all shared networks ──
    best: Optional[RouteResult] = None
    all_routes: list[RouteResult] = []

    for network in shared_networks:
        # Get withdrawal fee (from buy exchange)
        w_fee = ccxt_engine.get_cached_withdrawal_fee(buy_exchange, asset, network)

        # Get wallet status on both sides
        buy_wallet = ccxt_engine.get_cached_wallet_status(buy_exchange, asset, network)
        sell_wallet = ccxt_engine.get_cached_wallet_status(sell_exchange, asset, network)

        withdraw_ok = True
        deposit_ok = True

        if buy_wallet:
            withdraw_ok = buy_wallet.get("withdraw_enabled", True)
        if sell_wallet:
            deposit_ok = sell_wallet.get("deposit_enabled", True)

        viable = withdraw_ok and deposit_ok
        fee_usd = w_fee * asset_price_usd

        # Calculate savings vs. default network
        savings = max(0.0, default_fee_usd - fee_usd) if default_fee_usd > 0 else 0.0

        route = RouteResult(
            network=network,
            withdrawal_fee=w_fee,
            deposit_enabled=deposit_ok,
            withdraw_enabled=withdraw_ok,
            is_viable=viable,
            fee_usd_est=fee_usd,
            alternative_savings_usd=savings,
        )

        all_routes.append(route)

        if viable and (best is None or fee_usd < best.fee_usd_est):
            best = route

    if best and best.alternative_savings_usd > 0.01:
        logger.info(
            "🛤 Gas router: %s %s→%s | Best=%s ($%.4f) | Saved $%.4f vs %s",
            asset, buy_exchange, sell_exchange,
            best.network, best.fee_usd_est,
            best.alternative_savings_usd, default_network,
        )

    return best


def format_route_for_alert(route: Optional[RouteResult], asset: str) -> str:
    """
    Format the gas routing result for inclusion in a Telegram alert.

    Returns a one-line string like:
    "🛤 Route: ETH via Arbitrum ($0.01 fee) — saved $3.49 vs ERC-20"
    """
    if not route:
        default = PREFERRED_NETWORKS.get(asset, "unknown")
        return f"🛤 Route: {asset} via {default} (default, uncached)"

    line = f"🛤 Route: {asset} via {route.network} (${route.fee_usd_est:.4f} fee)"

    if route.alternative_savings_usd > 0.01:
        line += f" — saved ${route.alternative_savings_usd:.2f} vs default"

    if not route.is_viable:
        line += " ⚠️ WALLET ISSUE"

    return line
