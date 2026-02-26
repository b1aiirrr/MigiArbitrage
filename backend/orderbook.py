"""
MigiArbitrage — Order Book Manager
====================================
Memory-efficient order book using __slots__ and fixed-depth arrays.
Maintains the top N levels (bids/asks) per exchange per pair.
"""

from __future__ import annotations
import time
from typing import Optional


class OrderBookLevel:
    """Single price level with __slots__ for memory efficiency."""
    __slots__ = ("price", "quantity")

    def __init__(self, price: float, quantity: float) -> None:
        self.price = price
        self.quantity = quantity


class OrderBook:
    """
    Top-of-book order book for a single exchange + pair.
    Stores up to `max_depth` levels on each side.
    Uses __slots__ to minimize per-instance memory footprint.
    """
    __slots__ = (
        "exchange", "symbol", "bids", "asks",
        "max_depth", "last_update", "_valid",
    )

    def __init__(self, exchange: str, symbol: str, max_depth: int = 20) -> None:
        self.exchange: str = exchange
        self.symbol: str = symbol
        self.max_depth: int = max_depth
        self.bids: list[OrderBookLevel] = []  # Sorted descending by price
        self.asks: list[OrderBookLevel] = []  # Sorted ascending by price
        self.last_update: float = 0.0
        self._valid: bool = False

    # ── Updates ──────────────────────────────────

    def update_snapshot(
        self,
        bids: list[list[float]],
        asks: list[list[float]],
    ) -> None:
        """
        Replace the entire book with a new snapshot.
        `bids` / `asks` are lists of [price, quantity].
        """
        self.bids = [
            OrderBookLevel(float(b[0]), float(b[1]))
            for b in bids[: self.max_depth]
        ]
        self.asks = [
            OrderBookLevel(float(a[0]), float(a[1]))
            for a in asks[: self.max_depth]
        ]
        self.last_update = time.time()
        self._valid = True

    # ── Queries ──────────────────────────────────

    @property
    def is_valid(self) -> bool:
        """Book is valid if it has data and was updated within the last 10s."""
        if not self._valid:
            return False
        return (time.time() - self.last_update) < 10.0

    def best_bid(self) -> Optional[OrderBookLevel]:
        """Highest bid (the price a seller receives)."""
        return self.bids[0] if self.bids else None

    def best_ask(self) -> Optional[OrderBookLevel]:
        """Lowest ask (the price a buyer pays)."""
        return self.asks[0] if self.asks else None

    def executable_volume(self, side: str, target_price: float) -> float:
        """
        Calculate the executable volume at or better than `target_price`.
        For bids: sum quantities where bid.price >= target_price
        For asks: sum quantities where ask.price <= target_price
        """
        volume = 0.0
        if side == "bid":
            for level in self.bids:
                if level.price >= target_price:
                    volume += level.quantity
                else:
                    break
        elif side == "ask":
            for level in self.asks:
                if level.price <= target_price:
                    volume += level.quantity
                else:
                    break
        return volume


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

    def stats(self) -> dict:
        """Return current status of all managed books."""
        return {
            f"{ex}:{sym}": {
                "valid": book.is_valid,
                "bid": book.best_bid().price if book.best_bid() else None,
                "ask": book.best_ask().price if book.best_ask() else None,
                "age_s": round(time.time() - book.last_update, 1) if book.last_update else None,
            }
            for (ex, sym), book in self._books.items()
        }
