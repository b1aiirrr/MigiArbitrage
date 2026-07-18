"""
MigiArbitrage v3.0 — Triangular Arbitrage Module
==================================================
Scans for 3-way price inefficiencies within a single exchange.
E.g., KES → USDT → BTC → KES on Binance.

Advantage: Zero withdrawal fees, zero network latency —
only trading fees apply since all trades happen intra-exchange.

v3.0 changes:
- Auto-discovers ALL valid triangular paths via graph DFS
- Replaces 3 hardcoded paths with 20-100+ dynamic paths
- Periodic re-discovery (hourly) to catch new listings
- asyncio.sleep(0) yields between exchanges
"""

from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from config import (
    TRIANGULAR_ENABLED,
    TRIANGULAR_EXCHANGES,
    TRIANGULAR_PATHS,
    TRIANGULAR_POLL_INTERVAL,
    TRIANGULAR_AUTO_DISCOVER,
    EXCHANGE_FEES,
    MIN_NET_PROFIT_USD,
    MAX_CAPITAL_USD,
)
from orderbook import OrderBookManager
from tri_graph import discover_triangles_for_exchange

logger = logging.getLogger("migi.triangular")


@dataclass
class TriangularOpportunity:
    """Detected 3-way intra-exchange arbitrage."""
    exchange: str
    path: list[str]          # e.g., ["KES/USDT", "BTC/USDT", "BTC/KES"]
    arb_type: str = "triangular"
    step1_pair: str = ""
    step1_side: str = ""     # "buy" or "sell"
    step1_price: float = 0.0
    step2_pair: str = ""
    step2_side: str = ""
    step2_price: float = 0.0
    step3_pair: str = ""
    step3_side: str = ""
    step3_price: float = 0.0
    volume: float = 0.0      # In initial currency units
    fee_total: float = 0.0
    net_profit: float = 0.0
    profit_pct: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "pair": " → ".join(self.path),
            "arb_type": self.arb_type,
            "buy_exchange": self.exchange,
            "sell_exchange": self.exchange,
            "base": self.path[0].split("/")[0] if self.path else "",
            "quote": self.path[-1].split("/")[-1] if self.path else "",
            "ask_price": self.step1_price,
            "bid_price": self.step3_price,
            "raw_spread_pct": round(self.profit_pct, 4),
            "volume": round(self.volume, 6),
            "fee_maker": round(self.fee_total / 3, 6),
            "fee_taker": round(self.fee_total / 3, 6),
            "fee_withdrawal": 0.0,
            "fee_network": 0.0,
            "net_profit": round(self.net_profit, 4),
            "preflight": {"passed": True, "network": "internal", "risk_level": "low", "risk_notes": []},
            "payment_method": "",
            "payment_label": "",
            "risk_level": "low",
            "triangular_steps": [
                {"pair": self.step1_pair, "side": self.step1_side, "price": self.step1_price},
                {"pair": self.step2_pair, "side": self.step2_side, "price": self.step2_price},
                {"pair": self.step3_pair, "side": self.step3_side, "price": self.step3_price},
            ],
            "timestamp": self.timestamp,
        }


class TriangularScanner:
    """
    Scans for 3-way arbitrage within a single exchange.

    v3.0: Auto-discovers all valid paths via graph-based DFS.

    Algorithm:
    1. Start with currency A
    2. Convert A → B using pair1
    3. Convert B → C using pair2
    4. Convert C → A using pair3
    5. If final_A > initial_A - fees, it's profitable
    """

    # Re-discover paths every hour
    REDISCOVERY_INTERVAL_S: float = 3600.0

    def __init__(
        self,
        book_manager: OrderBookManager,
        ccxt_engine=None,
        on_spread=None,
    ) -> None:
        self.book_manager = book_manager
        self.ccxt_engine = ccxt_engine
        self.on_spread = on_spread
        self._running = True
        self._scan_count = 0
        self._opportunity_count = 0
        # Per-exchange discovered paths
        self._discovered_paths: dict[str, list[list[str]]] = {}
        self._last_discovery_ns: int = 0

    async def run(self) -> None:
        """Main triangular scan loop."""
        if not TRIANGULAR_ENABLED:
            logger.info("Triangular scanner disabled")
            return

        logger.info(
            "Triangular scanner started — exchanges=%s, auto_discover=%s, "
            "fallback_paths=%d (interval=%.0fs)",
            ", ".join(TRIANGULAR_EXCHANGES),
            TRIANGULAR_AUTO_DISCOVER,
            len(TRIANGULAR_PATHS),
            TRIANGULAR_POLL_INTERVAL,
        )

        # Initial path discovery
        if TRIANGULAR_AUTO_DISCOVER and self.ccxt_engine:
            await self._discover_paths()

        while self._running:
            try:
                # Periodic re-discovery
                if TRIANGULAR_AUTO_DISCOVER and self.ccxt_engine:
                    elapsed_s = (time.time_ns() - self._last_discovery_ns) / 1_000_000_000
                    if elapsed_s > self.REDISCOVERY_INTERVAL_S:
                        await self._discover_paths()

                await self._scan_all()
                self._scan_count += 1
            except Exception as exc:
                logger.error("Triangular scan error: %s", exc, exc_info=True)

            await asyncio.sleep(TRIANGULAR_POLL_INTERVAL)

    async def _discover_paths(self) -> None:
        """Discover triangular paths from exchange market data."""
        for exchange_id in TRIANGULAR_EXCHANGES:
            exchange = self.ccxt_engine._exchanges.get(exchange_id)
            if not exchange:
                continue

            try:
                if not exchange.markets:
                    await exchange.load_markets()

                paths = discover_triangles_for_exchange(exchange.markets)
                self._discovered_paths[exchange_id] = paths

                logger.info(
                    "🔺 [%s] Discovered %d triangular paths (was %d hardcoded)",
                    exchange_id, len(paths), len(TRIANGULAR_PATHS),
                )
            except Exception as exc:
                logger.warning("[%s] Triangle discovery failed: %s", exchange_id, exc)
                # Fall back to hardcoded paths
                self._discovered_paths[exchange_id] = TRIANGULAR_PATHS

        self._last_discovery_ns = time.time_ns()

    def _get_paths_for_exchange(self, exchange: str) -> list[list[str]]:
        """Get paths for an exchange — discovered or fallback."""
        if TRIANGULAR_AUTO_DISCOVER and exchange in self._discovered_paths:
            return self._discovered_paths[exchange]
        return TRIANGULAR_PATHS

    async def _scan_all(self) -> None:
        """Scan all configured paths on all exchanges."""
        for exchange in TRIANGULAR_EXCHANGES:
            fees = EXCHANGE_FEES.get(exchange, {"maker": 0.001, "taker": 0.001})
            paths = self._get_paths_for_exchange(exchange)

            for path in paths:
                if len(path) != 3:
                    continue
                await self._evaluate_triangle(exchange, path, fees)

            # Yield to event loop between exchanges
            await asyncio.sleep(0)

    async def _evaluate_triangle(
        self, exchange: str, path: list[str], fees: dict
    ) -> None:
        """
        Evaluate a single triangular path in both forward and reverse directions.
        """
        book1 = self.book_manager.get(exchange, path[0])
        book2 = self.book_manager.get(exchange, path[1])
        book3 = self.book_manager.get(exchange, path[2])

        if not (book1 and book1.is_valid and book2 and book2.is_valid and book3 and book3.is_valid):
            return

        ask1 = book1.best_ask()
        bid1 = book1.best_bid()
        ask2 = book2.best_ask()
        bid2 = book2.best_bid()
        ask3 = book3.best_ask()
        bid3 = book3.best_bid()

        if not all([ask1, bid1, ask2, bid2, ask3, bid3]):
            return

        taker_fee = fees.get("taker", 0.001)

        # ── Forward path: buy pair1, buy pair2, sell pair3 ──
        start_amount = MAX_CAPITAL_USD

        step1_amount = (start_amount / ask1.price) * (1 - taker_fee)
        step2_amount = (step1_amount / ask2.price) * (1 - taker_fee)
        step3_amount = (step2_amount * bid3.price) * (1 - taker_fee)

        profit = step3_amount - start_amount
        profit_pct = (profit / start_amount) * 100

        if profit > MIN_NET_PROFIT_USD:
            total_fee = start_amount * taker_fee * 3

            opportunity = TriangularOpportunity(
                exchange=exchange,
                path=path,
                step1_pair=path[0],
                step1_side="buy",
                step1_price=ask1.price,
                step2_pair=path[1],
                step2_side="buy",
                step2_price=ask2.price,
                step3_pair=path[2],
                step3_side="sell",
                step3_price=bid3.price,
                volume=start_amount,
                fee_total=total_fee,
                net_profit=profit,
                profit_pct=profit_pct,
                timestamp=time.time(),
            )

            logger.info(
                "🔺 Triangular found: %s on %s | Profit=$%.2f (%.3f%%)",
                " → ".join(path), exchange, profit, profit_pct,
            )

            if self.on_spread:
                await self.on_spread(opportunity.to_dict())

            self._opportunity_count += 1

        # ── Reverse path: sell pair3, sell pair2, buy pair1 ──
        r_start = MAX_CAPITAL_USD
        r_step1 = (r_start / ask3.price) * (1 - taker_fee)
        r_step2 = (r_step1 * bid2.price) * (1 - taker_fee)
        r_step3 = (r_step2 * bid1.price) * (1 - taker_fee)

        r_profit = r_step3 - r_start
        r_profit_pct = (r_profit / r_start) * 100

        if r_profit > MIN_NET_PROFIT_USD:
            total_fee = r_start * taker_fee * 3

            opportunity = TriangularOpportunity(
                exchange=exchange,
                path=list(reversed(path)),
                step1_pair=path[2],
                step1_side="buy",
                step1_price=ask3.price,
                step2_pair=path[1],
                step2_side="sell",
                step2_price=bid2.price,
                step3_pair=path[0],
                step3_side="sell",
                step3_price=bid1.price,
                volume=r_start,
                fee_total=total_fee,
                net_profit=r_profit,
                profit_pct=r_profit_pct,
                timestamp=time.time(),
            )

            logger.info(
                "🔺 Triangular (reverse) found: %s on %s | Profit=$%.2f (%.3f%%)",
                " → ".join(reversed(path)), exchange, r_profit, r_profit_pct,
            )

            if self.on_spread:
                await self.on_spread(opportunity.to_dict())

            self._opportunity_count += 1

    def stop(self) -> None:
        self._running = False

    @property
    def stats(self) -> dict:
        total_paths = sum(len(p) for p in self._discovered_paths.values())
        return {
            "triangular_scans": self._scan_count,
            "triangular_opportunities": self._opportunity_count,
            "discovered_paths": total_paths,
        }
