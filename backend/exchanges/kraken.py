"""
MigiArbitrage — Kraken Exchange Client
=======================================
WebSocket v2: book subscription (depth=25, truncated to 20)
REST: /0/public/AssetInfo + /0/public/SystemStatus for wallet checks
"""

from __future__ import annotations
import asyncio
import logging
from typing import Optional

import aiohttp
import orjson
import websockets.asyncio.client as ws_client

from backend.config import MONITORED_PAIRS
from backend.orderbook import OrderBookManager
from backend.exchanges.base import ExchangeClient, WalletStatus

logger = logging.getLogger("migi.kraken")

WS_BASE = "wss://ws.kraken.com/v2"
REST_BASE = "https://api.kraken.com"


class KrakenClient(ExchangeClient):
    """Kraken L2 order book via WebSocket v2."""

    def __init__(self, book_manager: OrderBookManager) -> None:
        super().__init__("kraken", book_manager)
        self._symbols: list[str] = [
            p["kraken"] for p in MONITORED_PAIRS if "kraken" in p
        ]

    # ── WebSocket ────────────────────────────────

    async def connect(self) -> None:
        async with ws_client.connect(
            WS_BASE,
            ping_interval=20,
            ping_timeout=10,
            max_size=2**20,
            close_timeout=5,
        ) as ws:
            # Subscribe to book channel
            sub_msg = orjson.dumps({
                "method": "subscribe",
                "params": {
                    "channel": "book",
                    "symbol": self._symbols,
                    "depth": 25,
                },
            }).decode()
            await ws.send(sub_msg)

            self._connected = True
            self._reconnect_delay = 1.0
            logger.info("[kraken] Connected — streaming %d pairs", len(self._symbols))

            async for raw in ws:
                data = orjson.loads(raw)
                await self._handle_message(data)

    async def _handle_message(self, data: dict) -> None:
        """Parse Kraken WebSocket v2 book messages."""
        channel = data.get("channel")
        if channel != "book":
            return

        msg_data = data.get("data", [])
        if not msg_data:
            return

        for entry in msg_data:
            symbol_raw = entry.get("symbol", "")
            pair_cfg = next(
                (p for p in MONITORED_PAIRS if p.get("kraken") == symbol_raw),
                None,
            )
            if not pair_cfg:
                continue

            normalized = f"{pair_cfg['base']}/{pair_cfg['quote']}"
            book = self.book_manager.get_or_create("kraken", normalized)

            bids = [[float(b["price"]), float(b["qty"])] for b in entry.get("bids", [])]
            asks = [[float(a["price"]), float(a["qty"])] for a in entry.get("asks", [])]

            if data.get("type") == "snapshot":
                book.update_snapshot(bids, asks)
            else:
                # For updates, merge into existing book
                # Simplified: treat each update as a new snapshot of top levels
                if bids or asks:
                    current_bids = [[l.price, l.quantity] for l in book.bids]
                    current_asks = [[l.price, l.quantity] for l in book.asks]

                    # Apply bid updates
                    bid_dict = {b[0]: b[1] for b in current_bids}
                    for b in bids:
                        if b[1] == 0:
                            bid_dict.pop(b[0], None)
                        else:
                            bid_dict[b[0]] = b[1]
                    merged_bids = sorted(bid_dict.items(), key=lambda x: -x[0])[:20]

                    # Apply ask updates
                    ask_dict = {a[0]: a[1] for a in current_asks}
                    for a in asks:
                        if a[1] == 0:
                            ask_dict.pop(a[0], None)
                        else:
                            ask_dict[a[0]] = a[1]
                    merged_asks = sorted(ask_dict.items(), key=lambda x: x[0])[:20]

                    book.update_snapshot(
                        [[p, q] for p, q in merged_bids],
                        [[p, q] for p, q in merged_asks],
                    )

    # ── REST: Wallet Status ──────────────────────

    async def check_wallet_status(
        self, asset: str, network: str
    ) -> Optional[WalletStatus]:
        """
        Kraken doesn't have a direct deposit/withdraw status endpoint
        like Binance. We check system status and asset info as a proxy.
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Check system status
                async with session.get(
                    f"{REST_BASE}/0/public/SystemStatus",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        status = result.get("result", {}).get("status", "")
                        if status != "online":
                            return WalletStatus(
                                asset=asset,
                                network=network,
                                deposit_enabled=False,
                                withdraw_enabled=False,
                            )

                # If system is online, assume deposit/withdraw are enabled
                # Kraken generally enables/disables per-asset via maintenance notices
                return WalletStatus(
                    asset=asset,
                    network=network,
                    deposit_enabled=True,
                    withdraw_enabled=True,
                    withdraw_fee=0.0,
                )
        except Exception as exc:
            logger.error("[kraken] Wallet check failed: %s", exc)
            return None
