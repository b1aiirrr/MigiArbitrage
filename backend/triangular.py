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
    TRIANGULAR_SYMBOLS,
    TRIANGULAR_POLL_INTERVAL,
    TRIANGULAR_AUTO_DISCOVER,
    SPOT_SYMBOLS,
    EXCHANGE_FEES,
    MIN_NET_PROFIT_USD,
    MAX_CAPITAL_USD,
)
from orderbook import OrderBookManager
from tri_graph import discover_triangles_for_exchange

logger = logging.getLogger("migi.triangular")

# Only triangles whose every leg is actually subscribed to the order book
# manager can be evaluated. Any discovered path referencing an unwatched
# symbol would short-circuit in _evaluate_triangle, so we drop those upfront.
WATCHED_SYMBOLS: set[str] = set(SPOT_SYMBOLS) | set(TRIANGULAR_SYMBOLS)


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
            "pair": " -> ".join(self.path),
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


def resolve_triangular_cycles(pairs: list[str]) -> list[list[dict]]:
    """
    Given 3 pairs, resolve the 2 directional 3-hop cycles (forward & reverse).
    Each cycle is a list of 3 steps:
      {'from': curr1, 'to': curr2, 'pair': pair, 'side': 'buy'|'sell'}
    Cycles start from a prioritized currency: USDT, USDC, USD, BTC, ETH.
    """
    if len(pairs) != 3:
        return []

    # Map quote -> base ("buy") and base -> quote ("sell")
    edges: dict[str, list[tuple[str, str, str]]] = {}
    for p in pairs:
        if "/" not in p:
            return []
        base, quote = p.split("/")
        edges.setdefault(quote, []).append((base, p, "buy"))
        edges.setdefault(base, []).append((quote, p, "sell"))

    all_assets = list(edges.keys())
    if len(all_assets) != 3:
        return []

    # Choose best starting asset: prioritize USDT -> USDC -> USD -> BTC -> ETH
    preferred = ("USDT", "USDC", "USD", "BTC", "ETH")
    start_asset = all_assets[0]
    for pref in preferred:
        if pref in edges:
            start_asset = pref
            break

    # Discover the 2 directed cycles starting from start_asset
    found_cycles: list[list[dict]] = []
    seen_directions: set[tuple[str, str, str]] = set()

    for mid, p1, s1 in edges.get(start_asset, []):
        for end, p2, s2 in edges.get(mid, []):
            if p2 == p1 or end == start_asset:
                continue
            for final, p3, s3 in edges.get(end, []):
                if p3 in (p1, p2) or final != start_asset:
                    continue

                sig = (p1, p2, p3)
                if sig not in seen_directions:
                    seen_directions.add(sig)
                    found_cycles.append([
                        {"from": start_asset, "to": mid, "pair": p1, "side": s1},
                        {"from": mid, "to": end, "pair": p2, "side": s2},
                        {"from": end, "to": start_asset, "pair": p3, "side": s3},
                    ])

    return found_cycles


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
                    try:
                        await exchange.load_markets()
                    except Exception:
                        exchange.apiKey = None
                        exchange.secret = None
                        exchange.password = None
                        if hasattr(exchange, "options"):
                            exchange.options["fetchCurrencies"] = False
                        await exchange.load_markets()

                paths = discover_triangles_for_exchange(exchange.markets)
                # Keep only triangles whose legs are all subscribed, otherwise
                # they can never be evaluated (missing order books).
                valid_paths = [p for p in paths if all(leg in WATCHED_SYMBOLS for leg in p)]
                if not valid_paths:
                    valid_paths = [p for p in TRIANGULAR_PATHS if all(leg in WATCHED_SYMBOLS for leg in p)]

                self._discovered_paths[exchange_id] = valid_paths

                logger.info(
                    "🔺 [%s] Discovered %d triangular paths (was %d hardcoded)",
                    exchange_id, len(valid_paths), len(TRIANGULAR_PATHS),
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
        Evaluate a single triangular path in both forward and reverse directions
        with exact buy/sell math and currency conversions.
        """
        # Ensure all 3 books exist and are valid
        books = {}
        for pair in path:
            b = self.book_manager.get(exchange, pair)
            if not b or not b.is_valid:
                return
            books[pair] = b

        cycles = resolve_triangular_cycles(path)
        if not cycles:
            return

        taker_fee = fees.get("taker", 0.001)

        for cycle in cycles:
            start_asset = cycle[0]["from"]

            # Conversion rate to USD for non-USD starting assets
            asset_to_usd = 1.0
            if start_asset in ("USDT", "USDC", "USD"):
                start_amount = MAX_CAPITAL_USD
            elif start_asset == "BTC":
                btc_book = self.book_manager.get(exchange, "BTC/USDT")
                btc_price = btc_book.mid_price() if (btc_book and btc_book.is_valid) else None
                if not btc_price or btc_price <= 0:
                    continue
                asset_to_usd = btc_price
                start_amount = MAX_CAPITAL_USD / btc_price
            elif start_asset == "ETH":
                eth_book = self.book_manager.get(exchange, "ETH/USDT")
                eth_price = eth_book.mid_price() if (eth_book and eth_book.is_valid) else None
                if not eth_price or eth_price <= 0:
                    continue
                asset_to_usd = eth_price
                start_amount = MAX_CAPITAL_USD / eth_price
            else:
                continue

            curr_amount = start_amount
            executed_steps = []
            cycle_valid = True

            for step in cycle:
                pair = step["pair"]
                side = step["side"]
                bk = books[pair]

                if side == "buy":
                    ask = bk.best_ask()
                    if not ask or ask.price <= 0:
                        cycle_valid = False
                        break
                    price = ask.price
                    curr_amount = (curr_amount / price) * (1 - taker_fee)
                else:
                    bid = bk.best_bid()
                    if not bid or bid.price <= 0:
                        cycle_valid = False
                        break
                    price = bid.price
                    curr_amount = (curr_amount * price) * (1 - taker_fee)

                executed_steps.append({
                    "pair": pair,
                    "side": side,
                    "price": price,
                })

            if not cycle_valid or len(executed_steps) != 3:
                continue

            profit_asset = curr_amount - start_amount
            profit_pct = (profit_asset / start_amount) * 100
            net_profit_usd = profit_asset * asset_to_usd

            if net_profit_usd > MIN_NET_PROFIT_USD:
                total_fee = (MAX_CAPITAL_USD * taker_fee * 3)
                cycle_pairs = [s["pair"] for s in executed_steps]

                opportunity = TriangularOpportunity(
                    exchange=exchange,
                    path=cycle_pairs,
                    step1_pair=executed_steps[0]["pair"],
                    step1_side=executed_steps[0]["side"],
                    step1_price=executed_steps[0]["price"],
                    step2_pair=executed_steps[1]["pair"],
                    step2_side=executed_steps[1]["side"],
                    step2_price=executed_steps[1]["price"],
                    step3_pair=executed_steps[2]["pair"],
                    step3_side=executed_steps[2]["side"],
                    step3_price=executed_steps[2]["price"],
                    volume=MAX_CAPITAL_USD,
                    fee_total=total_fee,
                    net_profit=net_profit_usd,
                    profit_pct=profit_pct,
                    timestamp=time.time(),
                )

                logger.info(
                    "🔺 Triangular found: %s on %s | Net=$%.2f (%.3f%%)",
                    " -> ".join(cycle_pairs), exchange, net_profit_usd, profit_pct,
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
