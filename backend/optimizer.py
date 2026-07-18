"""
MigiArbitrage v3.0 — Dynamic Capital Sizing Optimizer
======================================================
Finds the exact position size that maximizes net profit by
searching across the VWAP curve for the peak of the concave
profit function.

The net profit curve is typically concave:
- Rises as capital increases (more volume, more gross profit)
- Falls as VWAP slippage erodes the spread on thinner book levels

This module finds the inflection point — the "sweet spot" where
every additional dollar of capital starts degrading returns.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from orderbook import OrderBook


@dataclass
class OptimalSize:
    """Result of the capital optimization search."""
    __slots__ = (
        "capital_usd", "net_profit", "vwap_buy", "vwap_sell",
        "volume", "spread_bps", "levels_buy", "levels_sell",
        "fee_total",
    )

    capital_usd: float      # Optimal capital deployment
    net_profit: float       # Peak net profit at this size
    vwap_buy: float         # VWAP execution price (buy side)
    vwap_sell: float        # VWAP execution price (sell side)
    volume: float           # Optimal volume (base units)
    spread_bps: float       # Spread in basis points at this size
    levels_buy: int         # Book levels consumed (buy side)
    levels_sell: int        # Book levels consumed (sell side)
    fee_total: float        # Total fees at optimal size

    def __init__(
        self,
        capital_usd: float,
        net_profit: float,
        vwap_buy: float,
        vwap_sell: float,
        volume: float,
        spread_bps: float,
        levels_buy: int,
        levels_sell: int,
        fee_total: float,
    ) -> None:
        self.capital_usd = capital_usd
        self.net_profit = net_profit
        self.vwap_buy = vwap_buy
        self.vwap_sell = vwap_sell
        self.volume = volume
        self.spread_bps = spread_bps
        self.levels_buy = levels_buy
        self.levels_sell = levels_sell
        self.fee_total = fee_total


def find_optimal_size(
    buy_book: OrderBook,
    sell_book: OrderBook,
    fee_maker: float,
    fee_taker: float,
    fee_withdrawal: float,
    min_capital: float = 10.0,
    max_capital: float = 1000.0,
    coarse_steps: int = 20,
    fine_steps: int = 10,
) -> Optional[OptimalSize]:
    """
    Two-phase search for peak net profit across capital sizes.

    Phase 1 (Coarse): Scan the full range in `coarse_steps` increments
    to find the approximate peak region.

    Phase 2 (Fine): Narrow into the ±1 step region around the coarse
    peak and search with `fine_steps` granularity.

    This avoids the full cost of testing 50+ capital sizes while still
    achieving sub-dollar precision on the optimal size.

    Args:
        buy_book: Order book to buy from (asks will be swept)
        sell_book: Order book to sell on (bids will be swept)
        fee_maker: Maker fee rate (decimal, e.g., 0.001 = 0.1%)
        fee_taker: Taker fee rate (decimal)
        fee_withdrawal: Absolute withdrawal fee in USD
        min_capital: Minimum capital to test
        max_capital: Maximum capital to test (soft ceiling)
        coarse_steps: Number of steps in the coarse search phase
        fine_steps: Number of steps in the fine refinement phase

    Returns:
        OptimalSize at peak net profit, or None if no profitable size exists.
    """
    # ── Phase 1: Coarse search ──
    best_coarse_idx = -1
    best_coarse_profit = 0.0
    coarse_step_size = (max_capital - min_capital) / coarse_steps

    coarse_results: list[tuple[float, float]] = []  # (capital, net_profit)

    for i in range(coarse_steps + 1):
        capital = min_capital + i * coarse_step_size
        net = _evaluate_capital(
            capital, buy_book, sell_book, fee_maker, fee_taker, fee_withdrawal
        )
        coarse_results.append((capital, net))

        if net > best_coarse_profit:
            best_coarse_profit = net
            best_coarse_idx = i

    if best_coarse_idx < 0 or best_coarse_profit <= 0:
        return None

    # ── Phase 2: Fine refinement around coarse peak ──
    fine_lo = max(
        min_capital,
        min_capital + (best_coarse_idx - 1) * coarse_step_size,
    )
    fine_hi = min(
        max_capital,
        min_capital + (best_coarse_idx + 1) * coarse_step_size,
    )
    fine_step = (fine_hi - fine_lo) / fine_steps

    best: Optional[OptimalSize] = None

    for i in range(fine_steps + 1):
        capital = fine_lo + i * fine_step

        buy_vwap = buy_book.vwap_buy(capital)
        if not buy_vwap or not buy_vwap.fully_filled:
            continue

        sell_vwap = sell_book.vwap_sell(buy_vwap.filled_qty)
        if not sell_vwap or not sell_vwap.fully_filled:
            continue

        gross = sell_vwap.filled_usd - buy_vwap.filled_usd
        fees = (
            buy_vwap.filled_usd * fee_taker
            + sell_vwap.filled_usd * fee_maker
            + fee_withdrawal
        )
        net = gross - fees

        if net <= 0:
            continue

        spread_bps = (
            (sell_vwap.vwap - buy_vwap.vwap) / buy_vwap.vwap
        ) * 10_000

        if best is None or net > best.net_profit:
            best = OptimalSize(
                capital_usd=round(capital, 2),
                net_profit=net,
                vwap_buy=buy_vwap.vwap,
                vwap_sell=sell_vwap.vwap,
                volume=buy_vwap.filled_qty,
                spread_bps=spread_bps,
                levels_buy=buy_vwap.levels_consumed,
                levels_sell=sell_vwap.levels_consumed,
                fee_total=fees,
            )

    return best


def _evaluate_capital(
    capital: float,
    buy_book: OrderBook,
    sell_book: OrderBook,
    fee_maker: float,
    fee_taker: float,
    fee_withdrawal: float,
) -> float:
    """Quick profit evaluation at a given capital size. Returns net profit or 0."""
    buy_vwap = buy_book.vwap_buy(capital)
    if not buy_vwap or not buy_vwap.fully_filled:
        return 0.0

    sell_vwap = sell_book.vwap_sell(buy_vwap.filled_qty)
    if not sell_vwap or not sell_vwap.fully_filled:
        return 0.0

    gross = sell_vwap.filled_usd - buy_vwap.filled_usd
    fees = (
        buy_vwap.filled_usd * fee_taker
        + sell_vwap.filled_usd * fee_maker
        + fee_withdrawal
    )
    return max(0.0, gross - fees)
