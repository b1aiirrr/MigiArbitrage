"""
MigiArbitrage v2.0 — Arbitrage Scanner
========================================
Core spot scanning engine using unified CCXT engine.
Adds arb_type field for frontend filtering.
"""

from __future__ import annotations
import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from config import (
    SPOT_SYMBOLS,
    EXCHANGE_FEES,
    WITHDRAWAL_FEES,
    PREFERRED_NETWORKS,
    MIN_NET_PROFIT_USD,
    MAX_CAPITAL_USD,
    SCAN_INTERVAL_MS,
)
from orderbook import OrderBookManager, OrderBook
from preflight import PreFlightChecker, PreFlightResult
from alerter import TelegramAlerter

logger = logging.getLogger("migi.scanner")


@dataclass
class SpreadOpportunity:
    """Detected spot arbitrage spread."""
    __slots__ = [
        "pair", "base", "quote", "arb_type",
        "buy_exchange", "sell_exchange",
        "ask_price", "bid_price", "raw_spread_pct", "volume",
        "fee_maker", "fee_taker", "fee_withdrawal", "fee_network",
        "net_profit", "preflight", "timestamp",
    ]

    pair: str
    base: str
    quote: str
    arb_type: str
    buy_exchange: str
    sell_exchange: str
    ask_price: float
    bid_price: float
    raw_spread_pct: float
    volume: float
    fee_maker: float
    fee_taker: float
    fee_withdrawal: float
    fee_network: float
    net_profit: float
    preflight: Optional[PreFlightResult]
    timestamp: float

    def to_dict(self) -> dict:
        return {
            "pair": self.pair,
            "base": self.base,
            "quote": self.quote,
            "arb_type": self.arb_type,
            "buy_exchange": self.buy_exchange,
            "sell_exchange": self.sell_exchange,
            "ask_price": round(self.ask_price, 8),
            "bid_price": round(self.bid_price, 8),
            "raw_spread_pct": round(self.raw_spread_pct, 4),
            "volume": round(self.volume, 8),
            "fee_maker": round(self.fee_maker, 8),
            "fee_taker": round(self.fee_taker, 8),
            "fee_withdrawal": round(self.fee_withdrawal, 8),
            "fee_network": round(self.fee_network, 8),
            "net_profit": round(self.net_profit, 4),
            "preflight": self.preflight.to_dict() if self.preflight else None,
            "payment_method": "",
            "payment_label": "",
            "risk_level": self.preflight.risk_level if self.preflight else "low",
            "timestamp": self.timestamp,
        }


class ArbitrageScanner:
    """
    Scans all exchange pair combinations for spot ↔ spot arbitrage.
    Uses exchange IDs from CCXT engine.
    """

    def __init__(
        self,
        book_manager: OrderBookManager,
        preflight: PreFlightChecker,
        alerter: TelegramAlerter,
        exchange_ids: list[str] = None,
        on_spread=None,
    ) -> None:
        self.book_manager = book_manager
        self.preflight = preflight
        self.alerter = alerter
        self.exchange_ids = exchange_ids or []
        self.on_spread = on_spread
        self._running = True
        self._scan_count = 0
        self._opportunity_count = 0

    async def run(self) -> None:
        interval = SCAN_INTERVAL_MS / 1000.0
        combos = len(list(itertools.combinations(self.exchange_ids, 2)))
        logger.info(
            "Spot scanner started — %d symbols × %d exchange combos (interval=%.1fs)",
            len(SPOT_SYMBOLS), combos, interval,
        )

        while self._running:
            try:
                await self._scan_all()
                self._scan_count += 1
            except Exception as exc:
                logger.error("Scan error: %s", exc, exc_info=True)
            await asyncio.sleep(interval)

    async def _scan_all(self) -> None:
        for symbol in SPOT_SYMBOLS:
            books = self.book_manager.all_books_for_symbol(symbol)
            if len(books) < 2:
                continue
            for book_a, book_b in itertools.combinations(books, 2):
                await self._evaluate_spread(symbol, book_a, book_b)
                await self._evaluate_spread(symbol, book_b, book_a)

    async def _evaluate_spread(
        self, symbol: str, buy_book: OrderBook, sell_book: OrderBook
    ) -> None:
        ask = buy_book.best_ask()
        bid = sell_book.best_bid()
        if not ask or not bid or ask.price >= bid.price:
            return

        buy_volume = buy_book.executable_volume("ask", ask.price)
        sell_volume = sell_book.executable_volume("bid", bid.price)
        volume = min(buy_volume, sell_volume)
        if volume <= 0:
            return

        # Cap volume by MAX_CAPITAL_USD
        volume_usd = volume * ask.price
        if volume_usd > MAX_CAPITAL_USD:
            volume = MAX_CAPITAL_USD / ask.price

        base = symbol.split("/")[0]
        quote = symbol.split("/")[1]
        buy_ex = buy_book.exchange
        sell_ex = sell_book.exchange

        fee_taker = ask.price * volume * EXCHANGE_FEES.get(buy_ex, {}).get("taker", 0.001)
        fee_maker = bid.price * volume * EXCHANGE_FEES.get(sell_ex, {}).get("maker", 0.001)

        network = PREFERRED_NETWORKS.get(base, base)
        fee_withdrawal_units = WITHDRAWAL_FEES.get(base, {}).get(network, 0.0)
        fee_withdrawal = fee_withdrawal_units * ask.price
        fee_network = 0.0

        gross = (bid.price * volume) - (ask.price * volume)
        net_profit = gross - fee_maker - fee_taker - fee_withdrawal - fee_network

        if net_profit < MIN_NET_PROFIT_USD:
            return

        raw_spread_pct = ((bid.price - ask.price) / ask.price) * 100

        logger.info(
            "💰 Spot spread: %s | Buy %s@%.4f → Sell %s@%.4f | Net=$%.2f",
            symbol, buy_ex, ask.price, sell_ex, bid.price, net_profit,
        )

        preflight_result = await self.preflight.check(base, buy_ex, sell_ex)

        opportunity = SpreadOpportunity(
            pair=symbol,
            base=base,
            quote=quote,
            arb_type="spot",
            buy_exchange=buy_ex,
            sell_exchange=sell_ex,
            ask_price=ask.price,
            bid_price=bid.price,
            raw_spread_pct=raw_spread_pct,
            volume=volume,
            fee_maker=fee_maker,
            fee_taker=fee_taker,
            fee_withdrawal=fee_withdrawal,
            fee_network=fee_network,
            net_profit=net_profit,
            preflight=preflight_result,
            timestamp=time.time(),
        )

        if self.on_spread:
            await self.on_spread(opportunity)

        if preflight_result.passed:
            await self.alerter.send_opportunity(opportunity)
        else:
            await self.alerter.send_opportunity(opportunity, warning=True)

        self._opportunity_count += 1

    def stop(self) -> None:
        self._running = False

    @property
    def stats(self) -> dict:
        return {"scans": self._scan_count, "opportunities": self._opportunity_count}
