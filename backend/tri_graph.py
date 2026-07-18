"""
MigiArbitrage v3.0 — Dynamic Triangular Path Discovery
========================================================
Builds a directed currency graph from all active trading pairs on
an exchange and discovers ALL valid 3-node cycles using DFS.

Replaces the 3 hardcoded TRIANGULAR_PATHS with auto-discovered routes,
expanding from ~3 paths to 20-100+ paths per exchange.
"""

from __future__ import annotations
import logging
from collections import defaultdict

logger = logging.getLogger("migi.tri_graph")


def discover_triangles(
    symbols: list[str],
    start_assets: tuple[str, ...] = ("USDT", "BTC", "ETH", "BNB", "USDC"),
    max_triangles: int = 200,
) -> list[list[str]]:
    """
    Build a currency graph and find all 3-node cycles that start
    and end on the same quote asset.

    Algorithm:
    1. Parse all trading pairs into a directed graph (currency → currency)
    2. For each start asset, do a depth-3 DFS
    3. If we return to the start in exactly 3 hops, record the triangle
    4. Deduplicate using frozenset of pairs

    Args:
        symbols: List of trading pair symbols (e.g., ["BTC/USDT", "ETH/BTC", ...])
        start_assets: Which currencies to use as triangle entry points
        max_triangles: Safety cap to prevent runaway on exchanges with 1000+ pairs

    Returns:
        List of [pair1, pair2, pair3] triangular paths.
    """
    # ── Build adjacency graph ──
    # currency -> list of (target_currency, pair_symbol, side)
    graph: dict[str, list[tuple[str, str, str]]] = defaultdict(list)

    valid_symbols: set[str] = set()

    for symbol in symbols:
        if "/" not in symbol:
            continue
        parts = symbol.split("/")
        if len(parts) != 2:
            continue

        base, quote = parts
        valid_symbols.add(symbol)

        # Edge: quote → base via buying the pair
        graph[quote].append((base, symbol, "buy"))
        # Edge: base → quote via selling the pair
        graph[base].append((quote, symbol, "sell"))

    # ── DFS for 3-node cycles ──
    triangles: list[list[str]] = []
    seen: set[frozenset[str]] = set()

    for start in start_assets:
        if start not in graph:
            continue

        # Hop 1: start → mid
        for mid, pair1, side1 in graph[start]:
            if mid == start:
                continue  # Self-loop

            # Hop 2: mid → end
            for end, pair2, side2 in graph[mid]:
                if end == start:
                    continue  # 2-hop loop back (not a triangle, just a round-trip)
                if end == mid:
                    continue  # Self-loop

                # Hop 3: end → start (must return home)
                for final, pair3, side3 in graph[end]:
                    if final != start:
                        continue

                    # Validate: 3 distinct pairs
                    pair_set = frozenset([pair1, pair2, pair3])
                    if len(pair_set) != 3:
                        continue

                    if pair_set in seen:
                        continue

                    seen.add(pair_set)
                    triangles.append([pair1, pair2, pair3])

                    if len(triangles) >= max_triangles:
                        logger.info(
                            "Triangle discovery capped at %d paths",
                            max_triangles,
                        )
                        return triangles

    logger.info(
        "Discovered %d triangular paths from %d symbols (start assets: %s)",
        len(triangles), len(valid_symbols),
        ", ".join(start_assets),
    )

    return triangles


def discover_triangles_for_exchange(
    exchange_markets: dict,
    start_assets: tuple[str, ...] = ("USDT", "BTC", "ETH", "BNB", "USDC"),
) -> list[list[str]]:
    """
    Convenience wrapper: extract spot symbols from a ccxt exchange.markets
    dict and run triangle discovery.

    Args:
        exchange_markets: ccxt exchange.markets dict
        start_assets: Which currencies to use as triangle entry points

    Returns:
        List of triangular paths.
    """
    spot_symbols = [
        symbol
        for symbol, market in exchange_markets.items()
        if market.get("spot", True) and market.get("active", True) and "/" in symbol
    ]

    return discover_triangles(spot_symbols, start_assets)
