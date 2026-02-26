"""
MigiArbitrage — Arbitrage Scanner
==================================
Core scanning engine that compares order books across exchanges,
calculates true net profit including all fees, and triggers
pre-flight checks + alerts on profitable spreads.
"""

from __future__ import annotations
import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from backend.config import (
    MONITORED_PAIRS,
    EXCHANGE_FEES,
    WITHDRAWAL_FEES,
    PREFERRED_NETWORKS,
    MIN_NET_PROFIT_USD,
    SCAN_INTERVAL_MS,
)
from backend.orderbook import OrderBookManager, OrderBook
from backend.preflight import PreFlightChecker, PreFlightResult
from backend.alerter import TelegramAlerter

logger = logging.getLogger("migi.scanner")

EXCHANGES = ["binance", "kraken", "kucoin"]


@dataclass
class SpreadOpportunity:
    """Detected arbitrage spread before and after fee deductions."""
    __slots__ = [
        "pair", "base", "quote", "buy_exchange", "sell_exchange",
        "ask_price", "bid_price", "raw_spread_pct", "volume",
        "fee_maker", "fee_taker", "fee_withdrawal", "fee_network",
        "net_profit", "preflight", "timestamp",
    ]

    pair: str
    base: str
    quote: str
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
            "timestamp": self.timestamp,
        }


class ArbitrageScanner:
    """
    Continuously scans all exchange pair combinations for arbitrage.

    Net Profit = (P_bid × V) - (P_ask × V) - F_maker - F_taker - F_withdrawal - F_network

    Where:
    - P_bid = highest bid on the sell exchange
    - P_ask = lowest ask on the buy exchange
    - V = executable volume (min of available depth on both sides)
    - F_maker = maker fee on the buy exchange
    - F_taker = taker fee on the sell exchange
    - F_withdrawal = exchange withdrawal fee
    - F_network = blockchain network fee (estimated from withdrawal fees)
    """

    def __init__(
        self,
        book_manager: OrderBookManager,
        preflight: PreFlightChecker,
        alerter: TelegramAlerter,
        on_spread: Optional[callable] = None,
    ) -> None:
        self.book_manager = book_manager
        self.preflight = preflight
        self.alerter = alerter
        self.on_spread = on_spread  # Callback for WS server broadcast
        self._running = True
        self._scan_count = 0
        self._opportunity_count = 0

    async def run(self) -> None:
        """Main scan loop — runs at configured interval."""
        interval = SCAN_INTERVAL_MS / 1000.0
        logger.info(
            "Scanner started — %d pairs × %d exchange combos, interval=%.1fs",
            len(MONITORED_PAIRS),
            len(list(itertools.combinations(EXCHANGES, 2))),
            interval,
        )

        while self._running:
            try:
                await self._scan_all()
                self._scan_count += 1
            except Exception as exc:
                logger.error("Scan error: %s", exc, exc_info=True)

            await asyncio.sleep(interval)

    async def _scan_all(self) -> None:
        """Scan all pairs across all exchange combinations."""
        for pair_cfg in MONITORED_PAIRS:
            normalized = f"{pair_cfg['base']}/{pair_cfg['quote']}"

            # Get all valid books for this pair
            books = self.book_manager.all_books_for_symbol(normalized)
            if len(books) < 2:
                continue

            # Compare every pair of exchanges
            for book_a, book_b in itertools.combinations(books, 2):
                # Direction 1: Buy on A, sell on B
                await self._evaluate_spread(pair_cfg, book_a, book_b)
                # Direction 2: Buy on B, sell on A
                await self._evaluate_spread(pair_cfg, book_b, book_a)

    async def _evaluate_spread(
        self,
        pair_cfg: dict,
        buy_book: OrderBook,
        sell_book: OrderBook,
    ) -> None:
        """
        Evaluate a single directional spread between two exchanges.
        Buy on buy_book.exchange, sell on sell_book.exchange.
        """
        ask = buy_book.best_ask()
        bid = sell_book.best_bid()

        if not ask or not bid:
            return

        # No arbitrage if ask >= bid
        if ask.price >= bid.price:
            return

        # ── Calculate executable volume ──
        # Volume is the minimum of what we can buy and sell
        buy_volume = buy_book.executable_volume("ask", ask.price)
        sell_volume = sell_book.executable_volume("bid", bid.price)
        volume = min(buy_volume, sell_volume)

        if volume <= 0:
            return

        # ── Calculate all fees ──
        base = pair_cfg["base"]
        buy_ex = buy_book.exchange
        sell_ex = sell_book.exchange

        # Fee on the buy side (taker - we're lifting the ask)
        fee_taker = ask.price * volume * EXCHANGE_FEES.get(buy_ex, {}).get("taker", 0.001)

        # Fee on the sell side (maker - we're hitting the bid)
        fee_maker = bid.price * volume * EXCHANGE_FEES.get(sell_ex, {}).get("maker", 0.001)

        # Withdrawal fee (to move asset from buy exchange to sell exchange)
        network = PREFERRED_NETWORKS.get(base, base)
        fee_withdrawal_units = WITHDRAWAL_FEES.get(base, {}).get(network, 0.0)
        fee_withdrawal = fee_withdrawal_units * ask.price  # Convert to quote currency

        # Network fee (already included in withdrawal fee for most exchanges)
        fee_network = 0.0  # Subsumed into withdrawal fee

        # ── Net Profit Formula ──
        gross = (bid.price * volume) - (ask.price * volume)
        net_profit = gross - fee_maker - fee_taker - fee_withdrawal - fee_network

        if net_profit < MIN_NET_PROFIT_USD:
            return

        # ── Raw spread percentage ──
        raw_spread_pct = ((bid.price - ask.price) / ask.price) * 100

        normalized = f"{pair_cfg['base']}/{pair_cfg['quote']}"

        logger.info(
            "💰 Spread found: %s | Buy %s@%.4f → Sell %s@%.4f | "
            "Vol=%.6f | Net=$%.2f | Spread=%.3f%%",
            normalized, buy_ex, ask.price, sell_ex, bid.price,
            volume, net_profit, raw_spread_pct,
        )

        # ── Pre-flight check (ghost spread prevention) ──
        preflight_result = await self.preflight.check(base, buy_ex, sell_ex)

        opportunity = SpreadOpportunity(
            pair=normalized,
            base=base,
            quote=pair_cfg["quote"],
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

        # Broadcast to WebSocket clients
        if self.on_spread:
            await self.on_spread(opportunity)

        # Send Telegram alert
        if preflight_result.passed:
            await self.alerter.send_opportunity(opportunity)
        else:
            logger.warning(
                "⚠️ Spread failed pre-flight: %s",
                ", ".join(preflight_result.risk_notes),
            )
            # Still alert but with warning flag
            await self.alerter.send_opportunity(opportunity, warning=True)

        self._opportunity_count += 1

    def stop(self) -> None:
        self._running = False

    @property
    def stats(self) -> dict:
        return {
            "scans": self._scan_count,
            "opportunities": self._opportunity_count,
        }
