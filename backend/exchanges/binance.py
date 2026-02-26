"""
MigiArbitrage — Binance Exchange Client
========================================
WebSocket: Partial Depth streams (@depth20@100ms)
REST: /sapi/v1/capital/config/getall for wallet status
"""

from __future__ import annotations
import asyncio
import logging
from typing import Optional

import aiohttp
import orjson
import websockets
import websockets.asyncio.client as ws_client

from backend.config import (
    BINANCE_API_KEY,
    BINANCE_API_SECRET,
    MONITORED_PAIRS,
)
from backend.orderbook import OrderBookManager
from backend.exchanges.base import ExchangeClient, WalletStatus

logger = logging.getLogger("migi.binance")

WS_BASE = "wss://stream.binance.com:9443/stream"
REST_BASE = "https://api.binance.com"


class BinanceClient(ExchangeClient):
    """Binance L2 order book via combined WebSocket streams."""

    def __init__(self, book_manager: OrderBookManager) -> None:
        super().__init__("binance", book_manager)
        # Build subscribe list from config
        self._symbols: list[str] = [
            p["binance"] for p in MONITORED_PAIRS if "binance" in p
        ]

    # ── WebSocket ────────────────────────────────

    async def connect(self) -> None:
        """Connect to Binance combined stream for all monitored pairs."""
        streams = "/".join(f"{s}@depth20@100ms" for s in self._symbols)
        url = f"{WS_BASE}?streams={streams}"

        async with ws_client.connect(
            url,
            ping_interval=20,
            ping_timeout=10,
            max_size=2**20,  # 1 MB max frame
            close_timeout=5,
        ) as ws:
            self._connected = True
            self._reconnect_delay = 1.0
            logger.info("[binance] Connected — streaming %d pairs", len(self._symbols))

            async for raw in ws:
                data = orjson.loads(raw)
                await self._handle_message(data)

    async def _handle_message(self, data: dict) -> None:
        """Parse Binance combined stream message."""
        if "stream" not in data or "data" not in data:
            return

        payload = data["data"]
        # Stream format: "btcusdt@depth20@100ms"
        stream_name: str = data["stream"]
        symbol_raw = stream_name.split("@")[0].upper()

        # Normalize to BASE/QUOTE
        pair_cfg = next(
            (p for p in MONITORED_PAIRS if p.get("binance", "").lower() == symbol_raw.lower()),
            None,
        )
        if not pair_cfg:
            return

        normalized = f"{pair_cfg['base']}/{pair_cfg['quote']}"
        book = self.book_manager.get_or_create("binance", normalized)

        bids = payload.get("bids", [])
        asks = payload.get("asks", [])
        book.update_snapshot(bids, asks)

    # ── REST: Wallet Status ──────────────────────

    async def check_wallet_status(
        self, asset: str, network: str
    ) -> Optional[WalletStatus]:
        """
        Query Binance /sapi/v1/capital/config/getall to check
        deposit/withdrawal status for a specific asset + network.
        Requires a READ-ONLY API key.
        """
        if not BINANCE_API_KEY:
            logger.warning("[binance] No API key configured — skipping wallet check")
            return None

        import hashlib
        import hmac
        import time as _time

        timestamp = int(_time.time() * 1000)
        query = f"timestamp={timestamp}"
        signature = hmac.new(
            BINANCE_API_SECRET.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()

        url = f"{REST_BASE}/sapi/v1/capital/config/getall?{query}&signature={signature}"
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.warning("[binance] Wallet status HTTP %d", resp.status)
                        return None

                    coins = await resp.json()
                    for coin in coins:
                        if coin.get("coin", "").upper() != asset.upper():
                            continue
                        for net in coin.get("networkList", []):
                            net_name = net.get("network", "")
                            if net_name.upper() == network.upper():
                                return WalletStatus(
                                    asset=asset,
                                    network=network,
                                    deposit_enabled=net.get("depositEnable", False),
                                    withdraw_enabled=net.get("withdrawEnable", False),
                                    withdraw_fee=float(net.get("withdrawFee", 0)),
                                    min_withdraw=float(net.get("withdrawMin", 0)),
                                )
        except Exception as exc:
            logger.error("[binance] Wallet check failed: %s", exc)

        return None
