"""
MigiArbitrage v3.0 — Arbitrage Scanner
========================================
Core spot scanning engine using VWAP depth sweeps, dynamic capital
optimization, smart gas routing, and nanosecond staleness gating.

v3.0 changes:
- VWAP replaces Level-1 best_bid/best_ask for realistic execution pricing
- Dynamic capital optimizer finds peak profit size ($10-$1000)
- Smart gas router selects cheapest viable withdrawal network
- Nanosecond timestamps for data freshness tracking
- Sub-500ms staleness gate aborts evaluations on stale books
- asyncio.sleep(0) yields between symbols to prevent loop starvation
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
from orderbook import OrderBookManager, OrderBook, MAX_STALENESS_MS
from preflight import PreFlightChecker, PreFlightResult
from alerter import TelegramAlerter
from optimizer import find_optimal_size
from gas_router import find_cheapest_route, format_route_for_alert

logger = logging.getLogger("migi.scanner")


@dataclass
class SpreadOpportunity:
    """Detected spot arbitrage spread with VWAP-based pricing."""
    __slots__ = [
        "pair", "base", "quote", "arb_type",
        "buy_exchange", "sell_exchange",
        "ask_price", "bid_price", "raw_spread_pct", "volume",
        "fee_maker", "fee_taker", "fee_withdrawal", "fee_network",
        "net_profit", "preflight", "timestamp",
        # v3.0 additions
        "vwap_buy", "vwap_sell", "spread_bps",
        "optimal_capital", "levels_buy", "levels_sell",
        "ingestion_ns", "evaluation_ns", "data_age_ms",
        "route_network", "route_fee_usd", "route_savings_usd",
    ]

    pair: str
    base: str
    quote: str
    arb_type: str
    buy_exchange: str
    sell_exchange: str
    ask_price: float          # VWAP buy price (replaces Level-1 ask)
    bid_price: float          # VWAP sell price (replaces Level-1 bid)
    raw_spread_pct: float
    volume: float
    fee_maker: float
    fee_taker: float
    fee_withdrawal: float
    fee_network: float
    net_profit: float
    preflight: Optional[PreFlightResult]
    timestamp: float
    # v3.0 additions
    vwap_buy: float           # Volume-weighted average buy price
    vwap_sell: float          # Volume-weighted average sell price
    spread_bps: float         # Spread in basis points
    optimal_capital: float    # Dynamically optimized capital size
    levels_buy: int           # Book levels consumed on buy side
    levels_sell: int          # Book levels consumed on sell side
    ingestion_ns: int         # When the newest book data arrived
    evaluation_ns: int        # When the scanner evaluated this spread
    data_age_ms: float        # Staleness of the data at evaluation time
    route_network: str        # Cheapest viable network for withdrawal
    route_fee_usd: float      # Withdrawal fee on the optimal route
    route_savings_usd: float  # Savings vs. default network

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
            # v3.0 additions
            "vwap_buy": round(self.vwap_buy, 8),
            "vwap_sell": round(self.vwap_sell, 8),
            "spread_bps": round(self.spread_bps, 2),
            "optimal_capital": round(self.optimal_capital, 2),
            "levels_buy": self.levels_buy,
            "levels_sell": self.levels_sell,
            "data_age_ms": round(self.data_age_ms, 1),
            "route_network": self.route_network,
            "route_fee_usd": round(self.route_fee_usd, 4),
            "route_savings_usd": round(self.route_savings_usd, 4),
        }


class ArbitrageScanner:
    """
    Scans all exchange pair combinations for spot ↔ spot arbitrage.

    v3.0 pipeline:
    1. Staleness gate: skip books older than 500ms
    2. Quick spread check: best_bid > best_ask (Level-1 pre-filter)
    3. VWAP sweep + capital optimizer: find peak net profit size
    4. Gas router: select cheapest viable withdrawal network
    5. Pre-flight check: verify wallets are open
    6. Alert dispatch: rich Telegram notification
    """

    def __init__(
        self,
        book_manager: OrderBookManager,
        preflight: PreFlightChecker,
        alerter: TelegramAlerter,
        exchange_ids: list[str] = None,
        on_spread=None,
        ccxt_engine=None,
    ) -> None:
        self.book_manager = book_manager
        self.preflight = preflight
        self.alerter = alerter
        self.exchange_ids = exchange_ids or []
        self.on_spread = on_spread
        self.ccxt_engine = ccxt_engine
        self._running = True
        self._scan_count = 0
        self._opportunity_count = 0
        self._stale_skips = 0

    async def run(self) -> None:
        interval = SCAN_INTERVAL_MS / 1000.0
        combos = len(list(itertools.combinations(self.exchange_ids, 2)))
        logger.info(
            "Spot scanner started — %d symbols × %d exchange combos "
            "(interval=%.1fs, staleness_gate=%.0fms, max_capital=$%.0f)",
            len(SPOT_SYMBOLS), combos, interval,
            MAX_STALENESS_MS, MAX_CAPITAL_USD,
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
            # Use fresh_books_for_symbol to auto-filter stale data
            books = self.book_manager.fresh_books_for_symbol(symbol)
            if len(books) < 2:
                continue
            for book_a, book_b in itertools.combinations(books, 2):
                await self._evaluate_spread(symbol, book_a, book_b)
                await self._evaluate_spread(symbol, book_b, book_a)
            # Yield to event loop between symbols to prevent starvation
            await asyncio.sleep(0)

    async def _evaluate_spread(
        self, symbol: str, buy_book: OrderBook, sell_book: OrderBook
    ) -> None:
        # ── Step 1: Staleness gate ──
        buy_age = buy_book.staleness_ms
        sell_age = sell_book.staleness_ms
        if buy_age > MAX_STALENESS_MS or sell_age > MAX_STALENESS_MS:
            self._stale_skips += 1
            return

        # ── Step 2: Level-1 pre-filter (fast reject) ──
        ask = buy_book.best_ask()
        bid = sell_book.best_bid()
        if not ask or not bid or ask.price >= bid.price:
            return

        eval_ns = time.time_ns()
        base = symbol.split("/")[0]
        quote = symbol.split("/")[1]
        buy_ex = buy_book.exchange
        sell_ex = sell_book.exchange

        # ── Step 3: Gas routing — find cheapest withdrawal path ──
        route_network = PREFERRED_NETWORKS.get(base, base)
        route_fee_usd = 0.0
        route_savings = 0.0

        if self.ccxt_engine:
            route = find_cheapest_route(
                asset=base,
                buy_exchange=buy_ex,
                sell_exchange=sell_ex,
                asset_price_usd=ask.price,
                ccxt_engine=self.ccxt_engine,
            )
            if route and route.is_viable:
                route_network = route.network
                route_fee_usd = route.fee_usd_est
                route_savings = route.alternative_savings_usd

        # ── Step 4: Fee lookup ──
        if self.ccxt_engine:
            _, fee_taker_rate = self.ccxt_engine.get_cached_trading_fees(buy_ex, base)
            fee_maker_rate, _ = self.ccxt_engine.get_cached_trading_fees(sell_ex, base)
        else:
            fee_taker_rate = EXCHANGE_FEES.get(buy_ex, {}).get("taker", 0.001)
            fee_maker_rate = EXCHANGE_FEES.get(sell_ex, {}).get("maker", 0.001)

        # Withdrawal fee in asset units
        if self.ccxt_engine:
            fee_withdrawal_units = self.ccxt_engine.get_cached_withdrawal_fee(
                buy_ex, base, route_network
            )
        else:
            fee_withdrawal_units = WITHDRAWAL_FEES.get(base, {}).get(route_network, 0.0)

        fee_withdrawal_usd = fee_withdrawal_units * ask.price

        # ── Step 5: VWAP + Capital Optimizer ──
        optimal = find_optimal_size(
            buy_book=buy_book,
            sell_book=sell_book,
            fee_maker=fee_maker_rate,
            fee_taker=fee_taker_rate,
            fee_withdrawal=fee_withdrawal_usd,
            min_capital=10.0,
            max_capital=MAX_CAPITAL_USD,
        )

        if not optimal or optimal.net_profit < MIN_NET_PROFIT_USD:
            return

        # Calculate fees at optimal size for reporting
        fee_taker_abs = optimal.vwap_buy * optimal.volume * fee_taker_rate
        fee_maker_abs = optimal.vwap_sell * optimal.volume * fee_maker_rate

        raw_spread_pct = (
            (optimal.vwap_sell - optimal.vwap_buy) / optimal.vwap_buy
        ) * 100

        data_age_ms = max(buy_age, sell_age)

        logger.info(
            "💰 Spot spread: %s | Buy %s@VWAP$%.4f → Sell %s@VWAP$%.4f | "
            "Capital=$%.0f | Net=$%.2f | Route=%s | Age=%.0fms",
            symbol, buy_ex, optimal.vwap_buy,
            sell_ex, optimal.vwap_sell,
            optimal.capital_usd, optimal.net_profit,
            route_network, data_age_ms,
        )

        # ── Step 6: Pre-flight check ──
        preflight_result = await self.preflight.check(base, buy_ex, sell_ex)

        ingestion_ns = max(buy_book.last_update_ns, sell_book.last_update_ns)

        opportunity = SpreadOpportunity(
            pair=symbol,
            base=base,
            quote=quote,
            arb_type="spot",
            buy_exchange=buy_ex,
            sell_exchange=sell_ex,
            ask_price=optimal.vwap_buy,
            bid_price=optimal.vwap_sell,
            raw_spread_pct=raw_spread_pct,
            volume=optimal.volume,
            fee_maker=fee_maker_abs,
            fee_taker=fee_taker_abs,
            fee_withdrawal=fee_withdrawal_usd,
            fee_network=0.0,
            net_profit=optimal.net_profit,
            preflight=preflight_result,
            timestamp=time.time(),
            # v3.0 fields
            vwap_buy=optimal.vwap_buy,
            vwap_sell=optimal.vwap_sell,
            spread_bps=optimal.spread_bps,
            optimal_capital=optimal.capital_usd,
            levels_buy=optimal.levels_buy,
            levels_sell=optimal.levels_sell,
            ingestion_ns=ingestion_ns,
            evaluation_ns=eval_ns,
            data_age_ms=data_age_ms,
            route_network=route_network,
            route_fee_usd=route_fee_usd,
            route_savings_usd=route_savings,
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
        return {
            "scans": self._scan_count,
            "opportunities": self._opportunity_count,
            "stale_skips": self._stale_skips,
        }
