"""
MigiArbitrage v2.0 — Telegram Alert Module
============================================
Sends formatted alerts with inline keyboard buttons for P2P trades.
Supports spot, P2P, and triangular opportunity types.
Rate-limited to avoid Telegram throttling.
"""

from __future__ import annotations
import asyncio
import logging
import time
from collections import deque

import aiohttp

from backend.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_RATE_LIMIT,
    PAYMENT_METHODS,
    ENABLED_EXCHANGES,
)
from backend.p2p_assistant import P2PAssistant

logger = logging.getLogger("migi.alerter")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramAlerter:
    """
    Dispatches formatted alerts to Telegram with inline action buttons.
    """

    def __init__(self) -> None:
        self._enabled = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
        self._send_times: deque[float] = deque(maxlen=TELEGRAM_RATE_LIMIT)
        self._alert_count = 0
        self._p2p_assistant = P2PAssistant()

        if not self._enabled:
            logger.warning("Telegram alerter DISABLED — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")

    # ── Spot Arbitrage Alert ─────────────────────

    async def send_opportunity(self, opp, warning: bool = False) -> None:
        """Send a spot arbitrage alert."""
        if not self._enabled or not self._check_rate_limit():
            return

        status_icon = "⚠️" if warning else "✅"

        preflight = opp.preflight
        wallet_status = "Unknown"
        if preflight:
            wallet_status = f"✅ Active ({preflight.network})" if preflight.passed else \
                f"🚫 BLOCKED — {', '.join(preflight.risk_notes)}"

        risk_badge = ""
        if preflight and preflight.risk_level == "high":
            risk_badge = "\n🔴 <b>HIGH RISK — Ghost spread likely!</b>"
        elif preflight and preflight.risk_level == "medium":
            risk_badge = "\n🟡 <b>MEDIUM RISK — Proceed with caution</b>"

        msg = (
            f"{status_icon} <b>MigiArbitrage — Spot Alert</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 <b>Type:</b> Spot ↔ Spot\n"
            f"🪙 <b>Pair:</b> {opp.pair}\n"
            f"🟢 <b>Buy:</b> {opp.buy_exchange.upper()} @ <code>${opp.ask_price:,.4f}</code>\n"
            f"🔴 <b>Sell:</b> {opp.sell_exchange.upper()} @ <code>${opp.bid_price:,.4f}</code>\n"
            f"📊 <b>Spread:</b> <code>{opp.raw_spread_pct:.3f}%</code>\n"
            f"📦 <b>Volume:</b> <code>{opp.volume:.6f} {opp.base}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Est. Net Profit:</b> <code>${opp.net_profit:,.2f}</code>\n"
            f"📋 Fees: maker=${opp.fee_maker:.4f} | taker=${opp.fee_taker:.4f} | "
            f"withdraw=${opp.fee_withdrawal:.4f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔒 <b>Wallet Status:</b> {wallet_status}"
            f"{risk_badge}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ <i>Alert-only mode — no trades executed</i>"
        )

        await self._send_message(msg)

    # ── P2P Arbitrage Alert (with inline buttons) ─

    async def send_p2p_opportunity(self, opp_dict: dict) -> None:
        """Send a P2P arbitrage alert with payment method, risk badge, and action buttons."""
        if not self._enabled or not self._check_rate_limit():
            return

        risk = opp_dict.get("risk_level", "low")
        risk_icon = "🔴" if risk == "high" else "🟢"
        risk_label = "HIGH RISK ⚠️" if risk == "high" else "LOW RISK ✅"
        pm_label = opp_dict.get("payment_label", "N/A")

        # Risk warning for high-risk methods
        risk_warning = ""
        if risk == "high":
            risk_warning = (
                "\n\n⚠️ <b>CHARGEBACK WARNING:</b> "
                f"<i>{pm_label} is susceptible to chargebacks. "
                "Verify counterparty history before proceeding.</i>"
            )

        msg = (
            f"💱 <b>MigiArbitrage — P2P Alert</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 <b>Type:</b> Spot → P2P\n"
            f"🪙 <b>Pair:</b> {opp_dict.get('pair', 'USDT/KES')}\n"
            f"🟢 <b>Buy:</b> Spot @ <code>${opp_dict.get('buy_price_usd', 1):.4f}</code>\n"
            f"🔴 <b>Sell:</b> {opp_dict.get('sell_exchange', '').upper()} P2P @ "
            f"<code>KES {opp_dict.get('sell_price_kes', 0):,.2f}</code>\n"
            f"📦 <b>Volume:</b> <code>{opp_dict.get('volume', 0):.2f} USDT</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💳 <b>Payment:</b> {pm_label}\n"
            f"{risk_icon} <b>Risk:</b> {risk_label}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Net Profit:</b> <code>KES {opp_dict.get('net_profit_kes', 0):,.0f}</code> "
            f"(<code>${opp_dict.get('net_profit', 0):,.2f}</code>)\n"
            f"📊 <b>Margin:</b> <code>{opp_dict.get('margin_pct', 0):.2f}%</code>\n"
            f"⏱ <b>Transfer:</b> {opp_dict.get('transfer_time_est', 'N/A')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Advertiser:</b> {opp_dict.get('advertiser', 'N/A')} "
            f"({opp_dict.get('advertiser_trades', 0)} trades, "
            f"{opp_dict.get('advertiser_rate', 0) * 100:.0f}% rate)"
            f"{risk_warning}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ <i>Semi-auto mode — manual payment required</i>"
        )

        # Build inline keyboard
        pm_key = opp_dict.get("payment_method", "")
        keyboard = self._p2p_assistant.build_telegram_inline_keyboard(
            payment_method=pm_key,
        )

        await self._send_message_with_buttons(msg, keyboard)

    # ── Triangular Arbitrage Alert ────────────────

    async def send_triangular_opportunity(self, opp_dict: dict) -> None:
        """Send a triangular arbitrage alert."""
        if not self._enabled or not self._check_rate_limit():
            return

        steps = opp_dict.get("triangular_steps", [])
        steps_text = ""
        for i, step in enumerate(steps, 1):
            steps_text += f"   {i}. {step['side'].upper()} {step['pair']} @ <code>{step['price']:,.4f}</code>\n"

        msg = (
            f"🔺 <b>MigiArbitrage — Triangular Alert</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 <b>Type:</b> Triangular (intra-exchange)\n"
            f"🏦 <b>Exchange:</b> {opp_dict.get('buy_exchange', '').upper()}\n"
            f"🔄 <b>Path:</b> {opp_dict.get('pair', '')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 <b>Steps:</b>\n{steps_text}"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Net Profit:</b> <code>${opp_dict.get('net_profit', 0):,.2f}</code>\n"
            f"📊 <b>Return:</b> <code>{opp_dict.get('raw_spread_pct', 0):.3f}%</code>\n"
            f"🔒 <b>Risk:</b> 🟢 LOW (no withdrawals, zero network fees)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ <i>Alert-only mode — no trades executed</i>"
        )

        await self._send_message(msg)

    # ── Generic Alert Router ─────────────────────

    async def send_any_opportunity(self, opp_dict: dict) -> None:
        """Route alert to the correct formatter based on arb_type."""
        arb_type = opp_dict.get("arb_type", "spot")
        if arb_type == "p2p":
            await self.send_p2p_opportunity(opp_dict)
        elif arb_type == "triangular":
            await self.send_triangular_opportunity(opp_dict)
        # Spot alerts use the object-based send_opportunity method

    # ── Startup Message ──────────────────────────

    async def send_startup_message(self) -> None:
        if not self._enabled:
            return

        exchanges = ", ".join(ex.capitalize() for ex in ENABLED_EXCHANGES[:5])
        remaining = len(ENABLED_EXCHANGES) - 5
        ex_text = exchanges + (f" +{remaining} more" if remaining > 0 else "")

        msg = (
            "🚀 <b>MigiArbitrage v2.0 Scanner Started</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 Exchanges: {ex_text}\n"
            "🔄 Modes: Spot ↔ Spot | Spot → P2P | Triangular\n"
            "💳 P2P: KES/USDT (M-Pesa, Bank, PayPal, Skrill, GPay)\n"
            "🔒 Mode: Alert + Semi-auto (no full auto-execution)\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Scanning for arbitrage opportunities...</i>"
        )
        await self._send_message(msg)

    # ── Send Methods ─────────────────────────────

    async def _send_message(self, text: str) -> None:
        """Send a plain HTML message."""
        url = TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN)
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        await self._do_send(url, payload)

    async def _send_message_with_buttons(self, text: str, keyboard: dict) -> None:
        """Send an HTML message with inline keyboard buttons."""
        url = TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN)
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": keyboard,
        }
        await self._do_send(url, payload)

    async def _do_send(self, url: str, payload: dict) -> None:
        """Execute the HTTP POST to Telegram."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        self._alert_count += 1
                        logger.info("Telegram alert sent (#%d)", self._alert_count)
                    else:
                        body = await resp.text()
                        logger.warning("Telegram API error %d: %s", resp.status, body[:200])
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)

    def _check_rate_limit(self) -> bool:
        now = time.time()
        while self._send_times and (now - self._send_times[0]) > 60:
            self._send_times.popleft()
        if len(self._send_times) >= TELEGRAM_RATE_LIMIT:
            return False
        self._send_times.append(now)
        return True
