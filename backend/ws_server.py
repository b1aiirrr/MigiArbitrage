"""
MigiArbitrage v2.0 — WebSocket Server
=======================================
Streams spreads, P2P opportunities, and triangular alerts to the
frontend. Includes CORS origin checking for migi-arbitrage.vercel.app.
"""

from __future__ import annotations
import asyncio
import logging
import time
from collections import deque
from typing import Any

import orjson
import websockets.asyncio.server as ws_server_mod
from websockets.http11 import Response

from config import WS_SERVER_HOST, WS_SERVER_PORT, ALERT_HISTORY_SIZE, WS_ALLOWED_ORIGINS

logger = logging.getLogger("migi.wsserver")


class DashboardWSServer:
    """
    WebSocket server with CORS origin validation.

    Broadcasts: spread, p2p, triangular, books, status events.
    New clients receive the last N alerts on connect.
    """

    def __init__(self) -> None:
        self._clients: set = set()
        self._alert_history: deque[dict] = deque(maxlen=ALERT_HISTORY_SIZE)
        self._server = None
        self._running = True

    async def start(self) -> None:
        """Start the WebSocket server with CORS origin checking."""
        self._server = await ws_server_mod.serve(
            self._handle_client,
            WS_SERVER_HOST,
            WS_SERVER_PORT,
            origins=WS_ALLOWED_ORIGINS,
            ping_interval=30,
            ping_timeout=10,
            max_size=2**18,
            close_timeout=5,
        )
        logger.info(
            "Dashboard WS server on ws://%s:%d (CORS: %s)",
            WS_SERVER_HOST, WS_SERVER_PORT,
            ", ".join(WS_ALLOWED_ORIGINS),
        )

    async def _handle_client(self, websocket) -> None:
        self._clients.add(websocket)
        client_id = id(websocket)
        logger.info("Client connected [%s] — total: %d", client_id, len(self._clients))

        try:
            # Send history to new client
            if self._alert_history:
                history_msg = orjson.dumps({
                    "type": "history",
                    "data": list(self._alert_history),
                    "timestamp": time.time(),
                }).decode()
                await websocket.send(history_msg)

            # Send welcome/status
            status_msg = orjson.dumps({
                "type": "status",
                "data": {
                    "connected": True,
                    "clients": len(self._clients),
                    "history_size": len(self._alert_history),
                },
                "timestamp": time.time(),
            }).decode()
            await websocket.send(status_msg)

            # Keep alive
            async for message in websocket:
                try:
                    data = orjson.loads(message)
                    if data.get("type") == "ping":
                        pong = orjson.dumps({
                            "type": "pong",
                            "timestamp": time.time(),
                        }).decode()
                        await websocket.send(pong)
                except Exception:
                    pass

        except Exception:
            pass
        finally:
            self._clients.discard(websocket)
            logger.info("Client disconnected [%s] — total: %d", client_id, len(self._clients))

    async def broadcast_spread(self, data: Any) -> None:
        """Broadcast any opportunity (spot, P2P, triangular) to all clients."""
        # If data is an object with to_dict, convert it
        if hasattr(data, "to_dict"):
            data = data.to_dict()

        self._alert_history.append(data)

        msg = orjson.dumps({
            "type": "spread",
            "data": data,
            "timestamp": time.time(),
        }).decode()

        if self._clients:
            await asyncio.gather(
                *[self._safe_send(ws, msg) for ws in self._clients.copy()],
                return_exceptions=True,
            )

    async def broadcast_books(self, books_summary: dict) -> None:
        msg = orjson.dumps({
            "type": "books",
            "data": books_summary,
            "timestamp": time.time(),
        }).decode()

        if self._clients:
            await asyncio.gather(
                *[self._safe_send(ws, msg) for ws in self._clients.copy()],
                return_exceptions=True,
            )

    async def _safe_send(self, ws, msg: str) -> None:
        try:
            await ws.send(msg)
        except Exception:
            self._clients.discard(ws)

    async def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    @property
    def client_count(self) -> int:
        return len(self._clients)
