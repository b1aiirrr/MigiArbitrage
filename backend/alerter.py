"""
MigiArbitrage — Telegram Alert Module
======================================
Sends formatted arbitrage opportunity alerts via Telegram Bot API.
Includes rate limiting to avoid Telegram throttling.
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
)

logger = logging.getLogger("migi.alerter")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramAlerter:
    """
    Dispatches formatted alerts to a Telegram chat.
    Rate-limited to TELEGRAM_RATE_LIMIT messages per minute.
    """

    def __init__(self) -> None:
        self._enabled = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
        self._send_times: deque[float] = deque(maxlen=TELEGRAM_RATE_LIMIT)
        self._alert_count = 0

        if not self._enabled:
            logger.warning(
                "Telegram alerter DISABLED — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
            )

    async def send_opportunity(self, opp, warning: bool = False) -> None:
        """
        Format and send an arbitrage opportunity alert.

        Args:
            opp: SpreadOpportunity instance
            warning: If True, alert is flagged as high-risk (failed pre-flight)
        """
        if not self._enabled:
            return

        # Rate limit check
        if not self._check_rate_limit():
            logger.debug("Rate limited — skipping Telegram alert")
            return

        # Format the message
        status_icon = "⚠️" if warning else "✅"
        status_text = "HIGH RISK" if warning else "ACTIVE"

        preflight = opp.preflight
        wallet_status = "Unknown"
        if preflight:
            if preflight.passed:
                wallet_status = f"✅ Active ({preflight.network})"
            else:
                wallet_status = f"🚫 BLOCKED — {', '.join(preflight.risk_notes)}"

        risk_badge = ""
        if preflight and preflight.risk_level == "high":
            risk_badge = "\n🔴 <b>HIGH RISK — Ghost spread likely!</b>"
        elif preflight and preflight.risk_level == "medium":
            risk_badge = "\n🟡 <b>MEDIUM RISK — Proceed with caution</b>"

        msg = (
            f"{status_icon} <b>MigiArbitrage Alert</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
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
            f"🔒 <b>Wallet Status:</b> {wallet_status}\n"
            f"📡 <b>Network:</b> {preflight.network if preflight else 'N/A'}"
            f"{risk_badge}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ <i>Alert-only mode — no trades executed</i>"
        )

        await self._send_message(msg)

    async def send_startup_message(self) -> None:
        """Send a startup notification."""
        if not self._enabled:
            return

        msg = (
            "🚀 <b>MigiArbitrage Scanner Started</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📡 Monitoring: Binance, Kraken, KuCoin\n"
            "🔒 Mode: Alert-only (no trade execution)\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Scanning for arbitrage opportunities...</i>"
        )
        await self._send_message(msg)

    async def _send_message(self, text: str) -> None:
        """Send a single message to the configured Telegram chat."""
        url = TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN)
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

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
                        logger.warning(
                            "Telegram API error %d: %s", resp.status, body[:200]
                        )
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)

    def _check_rate_limit(self) -> bool:
        """Return True if we're within the rate limit."""
        now = time.time()

        # Remove timestamps older than 60 seconds
        while self._send_times and (now - self._send_times[0]) > 60:
            self._send_times.popleft()

        if len(self._send_times) >= TELEGRAM_RATE_LIMIT:
            return False

        self._send_times.append(now)
        return True
