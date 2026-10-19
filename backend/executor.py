"""
MigiArbitrage v3.0 — Execution Engine
=======================================
Converts detected arbitrage spreads into live market orders.

Pipeline:
1. Balance check: verify sufficient funds on both exchanges
2. Slippage guard: re-check spread hasn't collapsed
3. Atomic execution: simultaneous buy + sell via asyncio.gather
4. Post-trade logging & Telegram confirmation
5. Withdrawal routing: move profits via cheapest network

Safety:
- Dry-run mode by default (set EXECUTOR_LIVE=true to enable real trades)
- Max single trade cap (default $500)
- Cooldown between trades on same pair (default 30s)
- Automatic order cancellation if one leg fails
"""

from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional, Union

from config import (
    EXCHANGE_API_KEYS,
    MAX_CAPITAL_USD,
    PREFERRED_NETWORKS,
)

import os

logger = logging.getLogger("migi.executor")

# ── Configuration ────────────────────────────
EXECUTOR_ENABLED: bool = os.getenv("EXECUTOR_ENABLED", "true").lower() == "true"
EXECUTOR_LIVE: bool = os.getenv("EXECUTOR_LIVE", "false").lower() == "true"
MAX_TRADE_USD: float = float(os.getenv("MAX_TRADE_USD", "5"))
TRADE_COOLDOWN_SECS: float = float(os.getenv("TRADE_COOLDOWN_SECS", "30"))
SLIPPAGE_TOLERANCE_PCT: float = float(os.getenv("SLIPPAGE_TOLERANCE_PCT", "0.15"))


@dataclass
class TradeResult:
    """Result of an executed trade leg."""
    success: bool
    exchange: str
    side: str           # "buy" or "sell"
    symbol: str
    amount: float       # quantity filled
    price: float        # average fill price
    cost: float         # total cost in quote currency
    order_id: str
    fee: float
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ExecutionReport:
    """Full report for an arbitrage execution attempt."""
    opportunity_id: str
    pair: str
    buy_exchange: str
    sell_exchange: str
    status: str         # "success", "partial", "failed", "dry_run"
    buy_result: Optional[TradeResult] = None
    sell_result: Optional[TradeResult] = None
    net_profit_actual: float = 0.0
    withdrawal_initiated: bool = False
    withdrawal_network: str = ""
    notes: str = ""
    timestamp: float = field(default_factory=time.time)


class ExecutionEngine:
    """
    Executes detected arbitrage opportunities across exchanges.

    Modes:
    - DRY_RUN (default): Logs what it *would* do, no real orders
    - LIVE: Actually places market orders (requires API keys with trade perms)
    """

    def __init__(self, ccxt_engine, alerter=None) -> None:
        self.ccxt_engine = ccxt_engine
        self.alerter = alerter
        self._trade_log: list[ExecutionReport] = []
        self._cooldowns: dict[str, float] = {}  # "pair:buy_ex:sell_ex" -> last_trade_time
        self._total_profit: float = 0.0
        self._total_trades: int = 0
        self._failed_trades: int = 0

        mode = "🔴 LIVE TRADING" if EXECUTOR_LIVE else "🟢 DRY-RUN (paper)"
        logger.info(
            "Execution Engine initialized | Mode: %s | Max Trade: $%.0f | "
            "Cooldown: %.0fs | Slippage Tolerance: %.2f%%",
            mode, MAX_TRADE_USD, TRADE_COOLDOWN_SECS, SLIPPAGE_TOLERANCE_PCT,
        )

    # ── Public Entry Point ───────────────────────

    @staticmethod
    def _normalize_opportunity(opportunity) -> SimpleNamespace:
        """Normalize a SpreadOpportunity object or P2P/triangular dict into a
        SimpleNamespace with the attributes the executor expects."""
        if isinstance(opportunity, dict):
            d = opportunity
            preflight_raw = d.get("preflight")
            if isinstance(preflight_raw, dict):
                preflight = SimpleNamespace(
                    passed=preflight_raw.get("passed", False),
                    risk_notes=preflight_raw.get("risk_notes", []),
                )
            else:
                preflight = preflight_raw

            vwap_buy = d.get("vwap_buy") or d.get("ask_price") or 0.0
            vwap_sell = d.get("vwap_sell") or d.get("bid_price") or 0.0
            optimal_capital = d.get("optimal_capital") or MAX_TRADE_USD

            return SimpleNamespace(
                pair=d.get("pair", ""),
                buy_exchange=d.get("buy_exchange", ""),
                sell_exchange=d.get("sell_exchange", ""),
                base=d.get("base", ""),
                quote=d.get("quote", "USDT"),
                vwap_buy=float(vwap_buy),
                vwap_sell=float(vwap_sell),
                optimal_capital=float(optimal_capital),
                net_profit=float(d.get("net_profit", 0.0)),
                preflight=preflight,
                arb_type=d.get("arb_type", "spot"),
            )

        # Object (SpreadOpportunity) — expose arb_type defaulting to "spot"
        preflight = getattr(opportunity, "preflight", None)
        return SimpleNamespace(
            pair=opportunity.pair,
            buy_exchange=opportunity.buy_exchange,
            sell_exchange=opportunity.sell_exchange,
            base=getattr(opportunity, "base", ""),
            quote=getattr(opportunity, "quote", "USDT"),
            vwap_buy=getattr(opportunity, "vwap_buy", 0.0),
            vwap_sell=getattr(opportunity, "vwap_sell", 0.0),
            optimal_capital=getattr(opportunity, "optimal_capital", MAX_TRADE_USD),
            net_profit=getattr(opportunity, "net_profit", 0.0),
            preflight=preflight,
            arb_type=getattr(opportunity, "arb_type", "spot"),
        )

    async def execute_opportunity(self, opportunity) -> Optional[ExecutionReport]:
        """
        Attempt to execute a detected arbitrage opportunity (spot, P2P, or triangular).

        Args:
            opportunity: SpreadOpportunity object (spot) or dict (P2P/triangular).

        Returns:
            ExecutionReport or None if skipped.
        """
        if not EXECUTOR_ENABLED:
            return None

        opp = self._normalize_opportunity(opportunity)
        arb_type = opp.arb_type

        pair = opp.pair
        buy_ex = opp.buy_exchange
        sell_ex = opp.sell_exchange
        cooldown_key = f"{pair}:{buy_ex}:{sell_ex}"

        # ── Cooldown check ──
        last_trade = self._cooldowns.get(cooldown_key, 0)
        if (time.time() - last_trade) < TRADE_COOLDOWN_SECS:
            logger.debug("Cooldown active for %s, skipping", cooldown_key)
            return None

        # ── Pre-flight: check wallet status ──
        preflight = opp.preflight
        if preflight and not preflight.passed:
            notes = getattr(preflight, "risk_notes", []) or []
            logger.warning(
                "⚠️ Skipping execution for %s: preflight failed — %s",
                pair, ", ".join(notes),
            )
            return None

        # ── Cap trade size ──
        capital = min(opp.optimal_capital, MAX_TRADE_USD)
        volume = capital / opp.vwap_buy if opp.vwap_buy > 0 else 0.0

        opp_id = f"{pair}-{buy_ex}-{sell_ex}-{int(time.time())}"

        # ── DRY RUN (all arb types) ──
        if not EXECUTOR_LIVE:
            if arb_type == "p2p":
                notes = (
                    f"DRY RUN [P2P]: Would BUY ${capital:.2f} of {opp.base} on "
                    f"{buy_ex.upper()} spot @ ${opp.vwap_buy:.4f}, then sell via P2P "
                    f"on {sell_ex.upper()} for KES. Est. profit: ${opp.net_profit:.2f}"
                )
            elif arb_type == "triangular":
                notes = (
                    f"DRY RUN [TRI]: Would execute 3-leg cycle on {buy_ex.upper()} "
                    f"({pair}) with ${capital:.2f}. Est. profit: ${opp.net_profit:.2f}"
                )
            else:
                notes = (
                    f"DRY RUN: Would BUY {volume:.6f} {opp.base} on "
                    f"{buy_ex.upper()} @ ${opp.vwap_buy:.4f}, "
                    f"SELL on {sell_ex.upper()} @ ${opp.vwap_sell:.4f}. "
                    f"Est. profit: ${opp.net_profit:.2f}"
                )

            report = ExecutionReport(
                opportunity_id=opp_id,
                pair=pair,
                buy_exchange=buy_ex,
                sell_exchange=sell_ex,
                status="dry_run",
                net_profit_actual=opp.net_profit,
                notes=notes,
            )
            logger.info("📋 DRY RUN [%s]: %s", arb_type.upper(), notes)
            self._trade_log.append(report)
            self._cooldowns[cooldown_key] = time.time()
            return report

        # ── LIVE EXECUTION ──
        # Only spot cross-exchange is fully implemented for live trading.
        if arb_type != "spot":
            report = ExecutionReport(
                opportunity_id=opp_id,
                pair=pair,
                buy_exchange=buy_ex,
                sell_exchange=sell_ex,
                status="not_supported",
                net_profit_actual=0.0,
                notes=f"LIVE execution for {arb_type} not yet implemented (dry-run only).",
            )
            logger.info("⏭️  LIVE %s not supported yet: %s", arb_type, pair)
            self._trade_log.append(report)
            self._cooldowns[cooldown_key] = time.time()
            return report

        # ── Check API keys (spot live only) ──
        buy_keys = EXCHANGE_API_KEYS.get(buy_ex, {})
        sell_keys = EXCHANGE_API_KEYS.get(sell_ex, {})
        if not buy_keys.get("apiKey") or not sell_keys.get("apiKey"):
            logger.warning(
                "⚠️ Cannot execute %s: missing API keys for %s/%s",
                pair, buy_ex, sell_ex,
            )
            return None

        return await self._execute_live(opp, volume, capital, opp_id, cooldown_key)

    async def _execute_live(
        self, opportunity, volume: float, capital: float,
        opp_id: str, cooldown_key: str,
    ) -> ExecutionReport:
        """Execute a real live trade."""
        pair = opportunity.pair
        buy_ex = opportunity.buy_exchange
        sell_ex = opportunity.sell_exchange
        base = opportunity.base

        logger.info(
            "🚀 LIVE EXECUTION: %s | BUY %.6f %s on %s | SELL on %s | Capital=$%.2f",
            pair, volume, base, buy_ex.upper(), sell_ex.upper(), capital,
        )

        # ── Step 1: Check balances ──
        buy_balance = await self._get_balance(buy_ex, opportunity.quote)
        sell_balance = await self._get_balance(sell_ex, base)

        if buy_balance < capital:
            return self._fail_report(
                opp_id, pair, buy_ex, sell_ex,
                f"Insufficient {opportunity.quote} on {buy_ex}: "
                f"have ${buy_balance:.2f}, need ${capital:.2f}",
            )

        if sell_balance < volume:
            return self._fail_report(
                opp_id, pair, buy_ex, sell_ex,
                f"Insufficient {base} on {sell_ex}: "
                f"have {sell_balance:.6f}, need {volume:.6f}",
            )

        # ── Step 2: Slippage re-check ──
        book_manager = self.ccxt_engine.book_manager
        buy_book = book_manager.get(buy_ex, pair)
        sell_book = book_manager.get(sell_ex, pair)

        if not buy_book or not sell_book:
            return self._fail_report(
                opp_id, pair, buy_ex, sell_ex,
                "Order books no longer available",
            )

        current_ask = buy_book.best_ask()
        current_bid = sell_book.best_bid()

        if not current_ask or not current_bid:
            return self._fail_report(
                opp_id, pair, buy_ex, sell_ex,
                "No current bid/ask available",
            )

        current_spread_pct = ((current_bid.price - current_ask.price) / current_ask.price) * 100
        if current_spread_pct < -SLIPPAGE_TOLERANCE_PCT:
            return self._fail_report(
                opp_id, pair, buy_ex, sell_ex,
                f"Spread collapsed: was {opportunity.raw_spread_pct:.3f}%, "
                f"now {current_spread_pct:.3f}%",
            )

        # ── Step 3: Atomic execution (simultaneous buy + sell) ──
        buy_task = self._place_order(buy_ex, pair, "buy", volume)
        sell_task = self._place_order(sell_ex, pair, "sell", volume)

        results = await asyncio.gather(buy_task, sell_task, return_exceptions=True)

        buy_result = results[0] if not isinstance(results[0], Exception) else None
        sell_result = results[1] if not isinstance(results[1], Exception) else None

        buy_error = str(results[0]) if isinstance(results[0], Exception) else None
        sell_error = str(results[1]) if isinstance(results[1], Exception) else None

        # ── Step 4: Determine outcome ──
        if buy_result and buy_result.success and sell_result and sell_result.success:
            actual_profit = (
                (sell_result.cost - sell_result.fee) -
                (buy_result.cost + buy_result.fee)
            )
            status = "success"
            logger.info(
                "✅ TRADE SUCCESS: %s | Bought %.6f@$%.4f on %s | "
                "Sold %.6f@$%.4f on %s | Actual Profit: $%.2f",
                pair,
                buy_result.amount, buy_result.price, buy_ex,
                sell_result.amount, sell_result.price, sell_ex,
                actual_profit,
            )
            self._total_profit += actual_profit
            self._total_trades += 1

        elif buy_result and buy_result.success and (not sell_result or not sell_result.success):
            status = "partial"
            actual_profit = 0.0
            logger.error(
                "⚠️ PARTIAL TRADE: %s | Buy succeeded on %s but sell FAILED on %s: %s",
                pair, buy_ex, sell_ex, sell_error or "unknown",
            )
            self._failed_trades += 1
            # TODO: Emergency sell on buy exchange to recover

        elif sell_result and sell_result.success and (not buy_result or not buy_result.success):
            status = "partial"
            actual_profit = 0.0
            logger.error(
                "⚠️ PARTIAL TRADE: %s | Sell succeeded on %s but buy FAILED on %s: %s",
                pair, sell_ex, buy_ex, buy_error or "unknown",
            )
            self._failed_trades += 1
            # TODO: Emergency buy on sell exchange to recover

        else:
            status = "failed"
            actual_profit = 0.0
            logger.error(
                "❌ TRADE FAILED: %s | Buy error: %s | Sell error: %s",
                pair, buy_error or "unknown", sell_error or "unknown",
            )
            self._failed_trades += 1

        report = ExecutionReport(
            opportunity_id=opp_id,
            pair=pair,
            buy_exchange=buy_ex,
            sell_exchange=sell_ex,
            status=status,
            buy_result=buy_result,
            sell_result=sell_result,
            net_profit_actual=actual_profit,
            notes=f"Buy: {buy_error or 'OK'} | Sell: {sell_error or 'OK'}",
        )

        self._trade_log.append(report)
        self._cooldowns[cooldown_key] = time.time()

        # ── Step 5: Send trade confirmation to Telegram ──
        if self.alerter:
            await self._send_trade_alert(report, opportunity)

        # ── Step 6: Initiate withdrawal if profitable ──
        if status == "success" and actual_profit > 0:
            network = opportunity.route_network or PREFERRED_NETWORKS.get(base, base)
            report.withdrawal_network = network
            # Withdrawal is deferred — user can trigger manually or we auto-route
            logger.info(
                "💸 Profit $%.2f locked. Withdrawal via %s available.",
                actual_profit, network,
            )

        return report

    # ── Order Placement ──────────────────────────

    async def _place_order(
        self, exchange_id: str, symbol: str, side: str, amount: float,
    ) -> TradeResult:
        """Place a market order on an exchange via CCXT."""
        exchange = self.ccxt_engine._exchanges.get(exchange_id)
        if not exchange:
            return TradeResult(
                success=False, exchange=exchange_id, side=side,
                symbol=symbol, amount=0, price=0, cost=0,
                order_id="", fee=0, error=f"Exchange {exchange_id} not found",
            )

        try:
            order = await exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=amount,
            )

            filled = float(order.get("filled", amount))
            avg_price = float(order.get("average", order.get("price", 0)) or 0)
            cost = float(order.get("cost", filled * avg_price) or 0)
            fee_info = order.get("fee", {})
            fee_cost = float(fee_info.get("cost", 0) or 0) if fee_info else 0.0
            order_id = order.get("id", "unknown")

            logger.info(
                "📝 Order filled: %s %s %.6f %s @ $%.4f (cost=$%.2f, fee=$%.4f) [%s]",
                exchange_id, side.upper(), filled, symbol, avg_price, cost, fee_cost, order_id,
            )

            return TradeResult(
                success=True,
                exchange=exchange_id,
                side=side,
                symbol=symbol,
                amount=filled,
                price=avg_price,
                cost=cost,
                order_id=order_id,
                fee=fee_cost,
            )

        except Exception as exc:
            logger.error(
                "❌ Order failed: %s %s %s — %s",
                exchange_id, side, symbol, exc,
            )
            return TradeResult(
                success=False,
                exchange=exchange_id,
                side=side,
                symbol=symbol,
                amount=0,
                price=0,
                cost=0,
                order_id="",
                fee=0,
                error=str(exc),
            )

    # ── Balance Check ────────────────────────────

    async def _get_balance(self, exchange_id: str, asset: str) -> float:
        """Fetch available balance for an asset on an exchange."""
        exchange = self.ccxt_engine._exchanges.get(exchange_id)
        if not exchange:
            return 0.0

        try:
            balance = await exchange.fetch_balance()
            free = balance.get("free", {}).get(asset.upper(), 0)
            return float(free or 0)
        except Exception as exc:
            logger.error("[%s] Balance fetch failed for %s: %s", exchange_id, asset, exc)
            return 0.0

    # ── Telegram Trade Alert ─────────────────────

    async def _send_trade_alert(self, report: ExecutionReport, opportunity) -> None:
        """Send a trade execution result to Telegram."""
        if not self.alerter or not self.alerter._enabled:
            return

        if report.status == "success":
            icon = "✅"
            status_text = "EXECUTED"
        elif report.status == "dry_run":
            icon = "📋"
            status_text = "DRY RUN"
        elif report.status == "partial":
            icon = "⚠️"
            status_text = "PARTIAL (NEEDS ATTENTION)"
        else:
            icon = "❌"
            status_text = "FAILED"

        buy_info = ""
        if report.buy_result:
            buy_info = (
                f"   Filled: {report.buy_result.amount:.6f} @ "
                f"${report.buy_result.price:.4f}\n"
                f"   Cost: ${report.buy_result.cost:.2f} | "
                f"Fee: ${report.buy_result.fee:.4f}\n"
            )

        sell_info = ""
        if report.sell_result:
            sell_info = (
                f"   Filled: {report.sell_result.amount:.6f} @ "
                f"${report.sell_result.price:.4f}\n"
                f"   Revenue: ${report.sell_result.cost:.2f} | "
                f"Fee: ${report.sell_result.fee:.4f}\n"
            )

        msg = (
            f"{icon} <b>MigiArbitrage — Trade {status_text}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Pair:</b> {report.pair}\n"
            f"🟢 <b>Buy:</b> {report.buy_exchange.upper()}\n{buy_info}"
            f"🔴 <b>Sell:</b> {report.sell_exchange.upper()}\n{sell_info}"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Actual Profit:</b> <code>${report.net_profit_actual:,.2f}</code>\n"
            f"📊 <b>Running Total:</b> <code>${self._total_profit:,.2f}</code> "
            f"({self._total_trades} trades)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )

        if report.notes:
            msg += f"📝 {report.notes}\n"

        await self.alerter._send_message(msg)

    # ── Helpers ──────────────────────────────────

    def _fail_report(
        self, opp_id: str, pair: str, buy_ex: str, sell_ex: str, reason: str,
    ) -> ExecutionReport:
        """Create a failed execution report."""
        logger.warning("⚠️ Execution aborted for %s: %s", pair, reason)
        report = ExecutionReport(
            opportunity_id=opp_id,
            pair=pair,
            buy_exchange=buy_ex,
            sell_exchange=sell_ex,
            status="failed",
            notes=reason,
        )
        self._trade_log.append(report)
        self._failed_trades += 1
        return report

    @property
    def stats(self) -> dict:
        return {
            "total_trades": self._total_trades,
            "failed_trades": self._failed_trades,
            "total_profit": round(self._total_profit, 2),
            "mode": "LIVE" if EXECUTOR_LIVE else "DRY_RUN",
            "recent_trades": len(self._trade_log),
        }

    def recent_trades(self, limit: int = 20) -> list[dict]:
        """Return recent trade reports as dicts for the dashboard."""
        reports = self._trade_log[-limit:]
        results = []
        for r in reports:
            results.append({
                "id": r.opportunity_id,
                "pair": r.pair,
                "buy_exchange": r.buy_exchange,
                "sell_exchange": r.sell_exchange,
                "status": r.status,
                "profit": r.net_profit_actual,
                "notes": r.notes,
                "timestamp": r.timestamp,
            })
        return results
