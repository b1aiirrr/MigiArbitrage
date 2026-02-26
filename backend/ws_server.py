"""
MigiArbitrage — WebSocket Server
=================================
Lightweight WS server that streams detected spreads and order book
snapshots to the frontend dashboard. Maintains a ring buffer of
recent alerts for new client bootstrapping.
"""

from __future__ import annotations
import asyncio
import logging
import time
from collections import deque
from typing import Any

import orjson
import websockets.asyncio.server as ws_server

from backend.config import WS_SERVER_HOST, WS_SERVER_PORT, ALERT_HISTORY_SIZE

logger = logging.getLogger("migi.wsserver")


class DashboardWSServer:
    """
    WebSocket server for the MigiArbitrage frontend dashboard.

    Broadcasts:
    - "spread" events: real-time arbitrage opportunities
    - "books" events: periodic order book summary snapshots
    - "status" events: system health info

    New clients receive the last N alerts on connect.
    """

    def __init__(self) -> None:
        self._clients: set = set()
        self._alert_history: deque[dict] = deque(maxlen=ALERT_HISTORY_SIZE)
        self._server = None
        self._running = True

    async def start(self) -> None:
        """Start the WebSocket server."""
        self._server = await ws_server.serve(
            self._handle_client,
            WS_SERVER_HOST,
            WS_SERVER_PORT,
            ping_interval=30,
            ping_timeout=10,
            max_size=2**18,  # 256 KB — we only send, clients don't upload
            close_timeout=5,
        )
        logger.info(
            "Dashboard WS server listening on ws://%s:%d",
            WS_SERVER_HOST, WS_SERVER_PORT,
        )

    async def _handle_client(self, websocket) -> None:
        """Handle a new client connection."""
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

            # Keep alive — listen for pings/close
            async for message in websocket:
                # We don't expect client messages, but handle gracefully
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

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)
            logger.info("Client disconnected [%s] — total: %d", client_id, len(self._clients))

    async def broadcast_spread(self, opportunity_dict: dict) -> None:
        """Broadcast a spread opportunity to all connected clients."""
        self._alert_history.append(opportunity_dict)

        msg = orjson.dumps({
            "type": "spread",
            "data": opportunity_dict,
            "timestamp": time.time(),
        }).decode()

        if self._clients:
            # Use gather for concurrent sends; ignore failures
            await asyncio.gather(
                *[self._safe_send(ws, msg) for ws in self._clients.copy()],
                return_exceptions=True,
            )

    async def broadcast_books(self, books_summary: dict) -> None:
        """Broadcast order book summary to all connected clients."""
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
        """Send to a websocket, removing it on failure."""
        try:
            await ws.send(msg)
        except Exception:
            self._clients.discard(ws)

    async def stop(self) -> None:
        """Shutdown the server gracefully."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    @property
    def client_count(self) -> int:
        return len(self._clients)
