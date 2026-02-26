"""
MigiArbitrage — KuCoin Exchange Client
=======================================
WebSocket: Requires a token via REST POST, then subscribes to L2 depth.
REST: /api/v1/currencies/{currency} for deposit/withdrawal status.
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

logger = logging.getLogger("migi.kucoin")

REST_BASE = "https://api.kucoin.com"


class KuCoinClient(ExchangeClient):
    """KuCoin L2 order book via WebSocket (public token)."""

    def __init__(self, book_manager: OrderBookManager) -> None:
        super().__init__("kucoin", book_manager)
        self._symbols: list[str] = [
            p["kucoin"] for p in MONITORED_PAIRS if "kucoin" in p
        ]

    # ── WS Token Acquisition ────────────────────

    async def _get_ws_token(self) -> tuple[str, int]:
        """
        Request a public WebSocket token from KuCoin REST API.
        Returns (ws_url, ping_interval_ms).
        """
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{REST_BASE}/api/v1/bullet-public",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                body = await resp.json()
                data = body["data"]
                server = data["instanceServers"][0]
                token = data["token"]
                endpoint = server["endpoint"]
                ping_interval = server.get("pingInterval", 18000)
                url = f"{endpoint}?token={token}"
                return url, ping_interval

    # ── WebSocket ────────────────────────────────

    async def connect(self) -> None:
        ws_url, ping_interval_ms = await self._get_ws_token()
        ping_interval = ping_interval_ms / 1000.0

        async with ws_client.connect(
            ws_url,
            ping_interval=ping_interval,
            ping_timeout=10,
            max_size=2**20,
            close_timeout=5,
        ) as ws:
            # Subscribe to Level 2 depth for all symbols
            topics = ",".join(f"/market/level2Depth5:{s}" for s in self._symbols)
            sub = orjson.dumps({
                "id": "migi-sub",
                "type": "subscribe",
                "topic": topics,
                "privateChannel": False,
                "response": True,
            }).decode()
            await ws.send(sub)

            self._connected = True
            self._reconnect_delay = 1.0
            logger.info("[kucoin] Connected — streaming %d pairs", len(self._symbols))

            # Ping task
            async def send_pings():
                while self._running:
                    await asyncio.sleep(ping_interval)
                    try:
                        ping_msg = orjson.dumps({
                            "id": "migi-ping",
                            "type": "ping",
                        }).decode()
                        await ws.send(ping_msg)
                    except Exception:
                        break

            ping_task = asyncio.create_task(send_pings())

            try:
                async for raw in ws:
                    data = orjson.loads(raw)
                    await self._handle_message(data)
            finally:
                ping_task.cancel()

    async def _handle_message(self, data: dict) -> None:
        """Parse KuCoin Level 2 depth messages."""
        msg_type = data.get("type")
        if msg_type != "message":
            return

        topic: str = data.get("topic", "")
        # topic format: /market/level2Depth5:BTC-USDT
        if ":" not in topic:
            return

        symbol_raw = topic.split(":")[1]
        pair_cfg = next(
            (p for p in MONITORED_PAIRS if p.get("kucoin") == symbol_raw),
            None,
        )
        if not pair_cfg:
            return

        normalized = f"{pair_cfg['base']}/{pair_cfg['quote']}"
        book = self.book_manager.get_or_create("kucoin", normalized)

        payload = data.get("data", {})
        bids = payload.get("bids", [])
        asks = payload.get("asks", [])
        book.update_snapshot(bids, asks)

    # ── REST: Wallet Status ──────────────────────

    async def check_wallet_status(
        self, asset: str, network: str
    ) -> Optional[WalletStatus]:
        """
        Query KuCoin /api/v1/currencies/{currency} to check
        if deposit/withdrawal is enabled for the given chain.
        """
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{REST_BASE}/api/v3/currencies/{asset.upper()}"
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("[kucoin] Currency info HTTP %d", resp.status)
                        return None

                    body = await resp.json()
                    data = body.get("data", {})
                    chains = data.get("chains", [])

                    for chain in chains:
                        chain_name = chain.get("chainName", "")
                        if chain_name.upper() == network.upper():
                            return WalletStatus(
                                asset=asset,
                                network=network,
                                deposit_enabled=chain.get("isDepositEnabled", False),
                                withdraw_enabled=chain.get("isWithdrawEnabled", False),
                                withdraw_fee=float(chain.get("withdrawalMinFee", 0)),
                                min_withdraw=float(chain.get("withdrawalMinSize", 0)),
                            )

                    # If specific network not found, try first chain
                    if chains:
                        chain = chains[0]
                        return WalletStatus(
                            asset=asset,
                            network=chain.get("chainName", network),
                            deposit_enabled=chain.get("isDepositEnabled", False),
                            withdraw_enabled=chain.get("isWithdrawEnabled", False),
                            withdraw_fee=float(chain.get("withdrawalMinFee", 0)),
                            min_withdraw=float(chain.get("withdrawalMinSize", 0)),
                        )

        except Exception as exc:
            logger.error("[kucoin] Wallet check failed: %s", exc)

        return None
