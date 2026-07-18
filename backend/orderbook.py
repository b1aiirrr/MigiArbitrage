"""
MigiArbitrage v3.0 — Order Book Manager
=========================================
High-performance order book with:
- Pre-allocated __slots__ object pooling (zero-alloc updates)
- Nanosecond-precision staleness tracking
- VWAP depth sweep for realistic execution pricing
- Configurable freshness gates
"""

from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional


# ── Configuration ─────────────────────────────
MAX_STALENESS_MS: float = 500.0   # Sub-second freshness gate for scanners
HARD_STALE_MS: float = 10_000.0   # 10s — book is completely invalid


class OrderBookLevel:
    """Single price level with __slots__ for memory efficiency."""
    __slots__ = ("price", "quantity")

    def __init__(self, price: float = 0.0, quantity: float = 0.0) -> None:
        self.price = price
        self.quantity = quantity


@dataclass
class VWAPResult:
    """Result of a VWAP depth sweep across order book levels."""
    __slots__ = ("vwap", "filled_qty", "filled_usd", "levels_consumed", "fully_filled")
    vwap: float           # Volume-weighted average price
    filled_qty: float     # Total quantity filled (base units)
    filled_usd: float     # Total USD value filled
    levels_consumed: int  # Number of book levels swept
    fully_filled: bool    # True if entire requested size was filled

    def __init__(
        self,
        vwap: float,
        filled_qty: float,
        filled_usd: float,
        levels_consumed: int,
        fully_filled: bool,
    ) -> None:
        self.vwap = vwap
        self.filled_qty = filled_qty
        self.filled_usd = filled_usd
        self.levels_consumed = levels_consumed
        self.fully_filled = fully_filled


class OrderBook:
    """
    Top-of-book order book for a single exchange + pair.

    Performance features:
    - Pre-allocated level arrays: update_snapshot() mutates in-place
      instead of allocating new OrderBookLevel objects every frame.
    - Nanosecond timestamps for sub-millisecond staleness detection.
    - VWAP sweep methods for realistic execution price modeling.
    """
    __slots__ = (
        "exchange", "symbol", "bids", "asks",
        "max_depth", "last_update_ns", "_valid",
        "_bid_count", "_ask_count",
    )

    def __init__(self, exchange: str, symbol: str, max_depth: int = 20) -> None:
        self.exchange: str = exchange
        self.symbol: str = symbol
        self.max_depth: int = max_depth
        # Pre-allocate level arrays — these objects are REUSED, never replaced
        self.bids: list[OrderBookLevel] = [OrderBookLevel() for _ in range(max_depth)]
        self.asks: list[OrderBookLevel] = [OrderBookLevel() for _ in range(max_depth)]
        self._bid_count: int = 0
        self._ask_count: int = 0
        self.last_update_ns: int = 0
        self._valid: bool = False

    # ── Updates ──────────────────────────────────

    def update_snapshot(
        self,
        bids: list[list[float]],
        asks: list[list[float]],
    ) -> None:
        """
        Replace the entire book by mutating pre-allocated level objects
        in-place. Zero new allocations per frame.

        `bids` / `asks` are lists of [price, quantity].
        """
        n_bids = min(len(bids), self.max_depth)
        for i in range(n_bids):
            self.bids[i].price = float(bids[i][0])
            self.bids[i].quantity = float(bids[i][1])
        self._bid_count = n_bids

        n_asks = min(len(asks), self.max_depth)
        for i in range(n_asks):
            self.asks[i].price = float(asks[i][0])
            self.asks[i].quantity = float(asks[i][1])
        self._ask_count = n_asks

        self.last_update_ns = time.time_ns()
        self._valid = True

    # ── Staleness ────────────────────────────────

    @property
    def staleness_ms(self) -> float:
        """Milliseconds since the last order book update."""
        if self.last_update_ns == 0:
            return float("inf")
        return (time.time_ns() - self.last_update_ns) / 1_000_000

    @property
    def is_valid(self) -> bool:
        """Book is valid if it has data and was updated within the hard stale ceiling."""
        if not self._valid:
            return False
        return self.staleness_ms < HARD_STALE_MS

    @property
    def is_fresh(self) -> bool:
        """Book is fresh enough for latency-sensitive spread evaluation."""
        if not self._valid:
            return False
        return self.staleness_ms < MAX_STALENESS_MS

    # ── Level-1 Queries ──────────────────────────

    def best_bid(self) -> Optional[OrderBookLevel]:
        """Highest bid (the price a seller receives)."""
        return self.bids[0] if self._bid_count > 0 else None

    def best_ask(self) -> Optional[OrderBookLevel]:
        """Lowest ask (the price a buyer pays)."""
        return self.asks[0] if self._ask_count > 0 else None

    def mid_price(self) -> Optional[float]:
        """Mid-market price between best bid and best ask."""
        bid = self.best_bid()
        ask = self.best_ask()
        if bid and ask:
            return (bid.price + ask.price) / 2.0
        return None

    # ── Volume Queries ───────────────────────────

    def executable_volume(self, side: str, target_price: float) -> float:
        """
        Calculate the executable volume at or better than `target_price`.
        For bids: sum quantities where bid.price >= target_price
        For asks: sum quantities where ask.price <= target_price
        """
        volume = 0.0
        if side == "bid":
            for i in range(self._bid_count):
                level = self.bids[i]
                if level.price >= target_price:
                    volume += level.quantity
                else:
                    break
        elif side == "ask":
            for i in range(self._ask_count):
                level = self.asks[i]
                if level.price <= target_price:
                    volume += level.quantity
                else:
                    break
        return volume

    # ── VWAP Depth Sweep ─────────────────────────

    def vwap_buy(self, capital_usd: float) -> Optional[VWAPResult]:
        """
        Sweep the ask side: what VWAP do we pay to deploy $capital_usd?

        Walks through ask levels from best (lowest) upward, accumulating
        cost until the target capital is exhausted or the book runs out.
        Returns the volume-weighted average execution price.

        Args:
            capital_usd: Total USD to spend buying the base asset.

        Returns:
            VWAPResult with execution details, or None if book is empty.
        """
        if self._ask_count == 0:
            return None

        remaining_usd = capital_usd
        total_qty = 0.0
        total_cost = 0.0
        levels = 0

        for i in range(self._ask_count):
            level = self.asks[i]
            level_usd = level.price * level.quantity

            if level_usd <= remaining_usd:
                # Consume entire level
                total_qty += level.quantity
                total_cost += level_usd
                remaining_usd -= level_usd
                levels += 1
            else:
                # Partial fill on this level
                partial_qty = remaining_usd / level.price
                total_qty += partial_qty
                total_cost += remaining_usd
                remaining_usd = 0.0
                levels += 1
                break

        if total_qty == 0.0:
            return None

        return VWAPResult(
            vwap=total_cost / total_qty,
            filled_qty=total_qty,
            filled_usd=total_cost,
            levels_consumed=levels,
            fully_filled=(remaining_usd <= 0.0),
        )

    def vwap_sell(self, qty: float) -> Optional[VWAPResult]:
        """
        Sweep the bid side: what VWAP do we receive selling `qty` base units?

        Walks through bid levels from best (highest) downward, accumulating
        revenue until the target quantity is exhausted or the book runs out.
        Returns the volume-weighted average execution price.

        Args:
            qty: Total quantity of base asset to sell.

        Returns:
            VWAPResult with execution details, or None if book is empty.
        """
        if self._bid_count == 0:
            return None

        remaining_qty = qty
        total_revenue = 0.0
        total_sold = 0.0
        levels = 0

        for i in range(self._bid_count):
            level = self.bids[i]

            if level.quantity <= remaining_qty:
                # Consume entire level
                total_sold += level.quantity
                total_revenue += level.price * level.quantity
                remaining_qty -= level.quantity
                levels += 1
            else:
                # Partial fill on this level
                total_sold += remaining_qty
                total_revenue += level.price * remaining_qty
                remaining_qty = 0.0
                levels += 1
                break

        if total_sold == 0.0:
            return None

        return VWAPResult(
            vwap=total_revenue / total_sold,
            filled_qty=total_sold,
            filled_usd=total_revenue,
            levels_consumed=levels,
            fully_filled=(remaining_qty <= 0.0),
        )

    # ── Multi-Size VWAP Sweep ────────────────────

    def vwap_buy_multi(
        self, capital_sizes: list[float]
    ) -> list[Optional[VWAPResult]]:
        """
        Run VWAP buy sweep for multiple capital sizes in a single pass.
        Sizes must be sorted ascending for efficiency.

        Args:
            capital_sizes: Sorted list of USD amounts (e.g., [10, 50, 100, 500]).

        Returns:
            List of VWAPResult (or None) for each capital size.
        """
        if self._ask_count == 0:
            return [None] * len(capital_sizes)

        results: list[Optional[VWAPResult]] = []
        sorted_sizes = sorted(capital_sizes)

        for target in sorted_sizes:
            results.append(self.vwap_buy(target))

        return results


class OrderBookManager:
    """
    Central registry of all order books across all exchanges.
    Keyed by (exchange, normalized_symbol).
    """
    __slots__ = ("_books", "_max_depth")

    def __init__(self, max_depth: int = 20) -> None:
        self._books: dict[tuple[str, str], OrderBook] = {}
        self._max_depth = max_depth

    def get_or_create(self, exchange: str, symbol: str) -> OrderBook:
        """Retrieve existing book or create a new one."""
        key = (exchange, symbol)
        if key not in self._books:
            self._books[key] = OrderBook(exchange, symbol, self._max_depth)
        return self._books[key]

    def get(self, exchange: str, symbol: str) -> Optional[OrderBook]:
        """Retrieve an existing book, or None."""
        return self._books.get((exchange, symbol))

    def all_books_for_symbol(self, symbol: str) -> list[OrderBook]:
        """Return all valid books across exchanges for a given symbol."""
        return [
            book
            for (_, sym), book in self._books.items()
            if sym == symbol and book.is_valid
        ]

    def fresh_books_for_symbol(self, symbol: str) -> list[OrderBook]:
        """Return only fresh (sub-500ms) books for latency-sensitive scanning."""
        return [
            book
            for (_, sym), book in self._books.items()
            if sym == symbol and book.is_fresh
        ]

    def stats(self) -> dict:
        """Return current status of all managed books."""
        return {
            f"{ex}:{sym}": {
                "valid": book.is_valid,
                "fresh": book.is_fresh,
                "bid": book.best_bid().price if book.best_bid() else None,
                "ask": book.best_ask().price if book.best_ask() else None,
                "staleness_ms": round(book.staleness_ms, 1) if book.last_update_ns else None,
                "bid_depth": book._bid_count,
                "ask_depth": book._ask_count,
            }
            for (ex, sym), book in self._books.items()
        }
