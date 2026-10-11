"""
MigiArbitrage v3.0 — WebSocket Server (aiohttp)
===============================================
Streams spreads, P2P opportunities, and triangular alerts to the frontend.
Handles Azure Health Probes and Webhook API using aiohttp.web.
"""

from __future__ import annotations
import asyncio
import logging
import time
from collections import deque
from typing import Any

import orjson
import aiohttp
from aiohttp import web

from config import WS_SERVER_HOST, WS_SERVER_PORT, ALERT_HISTORY_SIZE, WS_ALLOWED_ORIGINS

logger = logging.getLogger("migi.wsserver")


class DashboardWSServer:
    """
    WebSocket server using aiohttp.web.
    Broadcasts: spread, p2p, triangular, books, status events.
    """

    def __init__(self) -> None:
        self._clients: set = set()
        self._alert_history: deque[dict] = deque(maxlen=ALERT_HISTORY_SIZE)
        self.app = web.Application()
        self.app.router.add_route("GET", "/", self.root_handler)
        self.app.router.add_route("GET", "/health", self.root_handler)
        self.app.router.add_route("POST", "/api/coop-webhook", self.coop_webhook)
        self.app.router.add_route("POST", "/api/paystack-webhook", self.paystack_webhook)
        self.runner = None
        self._last_books = None
        self._last_payment: dict | None = None
        self._last_payment_time: float = 0.0

    async def root_handler(self, request: web.Request) -> web.StreamResponse:
        # Check if it's a WebSocket upgrade request
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._websocket_handler(request)
        else:
            # Azure Load Balancer Health Probe
            return web.Response(text="OK", status=200)

    async def coop_webhook(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            logger.info("Received Safaricom Daraja IPN: %s", data)
            
            self._last_payment = data
            self._last_payment_time = time.time()

            # Broadcast payment to frontend dashboard
            msg = orjson.dumps({
                "type": "payment",
                "data": data,
                "timestamp": time.time(),
            }).decode()
            
            if self._clients:
                asyncio.create_task(asyncio.gather(
                    *[self._safe_send(ws, msg) for ws in self._clients.copy()],
                    return_exceptions=True
                ))

            return web.json_response({
                "ResultCode": 0,
                "ResultDesc": "Accepted"
            }, status=200)
        except Exception as e:
            logger.error("Error processing coop webhook: %s", e)
            return web.json_response({
                "ResultCode": 1,
                "ResultDesc": "Rejected"
            }, status=400)

    async def paystack_webhook(self, request: web.Request) -> web.Response:
        """Handle incoming Paystack webhook notifications (charge.success)."""
        try:
            payload = await request.json()
            event = payload.get("event")
            logger.info("Received Paystack event: %s", event)

            if event == "charge.success":
                data = payload.get("data", {})
                customer = data.get("customer", {})
                # Paystack returns amount in subunits (e.g. 1000 = 10 KES)
                raw_amount = data.get("amount", 0)
                amount = raw_amount / 100 if raw_amount > 0 else 0
                first_name = customer.get("first_name") or customer.get("phone") or "Customer"
                ref = data.get("reference", "")

                payment_data = {
                    "TransAmount": str(amount),
                    "FirstName": first_name,
                    "TransID": ref,
                    "BillRefNumber": data.get("channel", "mobile_money"),
                    "provider": "paystack",
                }

                self._last_payment = payment_data
                self._last_payment_time = time.time()

                msg = orjson.dumps({
                    "type": "payment",
                    "data": payment_data,
                    "timestamp": time.time(),
                }).decode()

                if self._clients:
                    asyncio.create_task(asyncio.gather(
                        *[self._safe_send(ws, msg) for ws in self._clients.copy()],
                        return_exceptions=True
                    ))

            return web.Response(text="OK", status=200)
        except Exception as e:
            logger.error("Error processing paystack webhook: %s", e)
            return web.Response(text="Error", status=400)

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)

        self._clients.add(ws)
        client_id = id(ws)
        logger.info("Client connected [%s] — total: %d", client_id, len(self._clients))

        try:
            # Send history
            if self._alert_history:
                await ws.send_str(orjson.dumps({
                    "type": "history",
                    "data": list(self._alert_history),
                    "timestamp": time.time(),
                }).decode())

            # Send welcome
            await ws.send_str(orjson.dumps({
                "type": "status",
                "data": {
                    "connected": True,
                    "clients": len(self._clients),
                    "history_size": len(self._alert_history),
                },
                "timestamp": time.time(),
            }).decode())

            # Send books
            if getattr(self, "_last_books", None):
                await ws.send_str(orjson.dumps({
                    "type": "books",
                    "data": self._last_books,
                    "timestamp": time.time(),
                }).decode())

            # Send recent payment if received within last 10 minutes
            if self._last_payment and (time.time() - self._last_payment_time < 600):
                await ws.send_str(orjson.dumps({
                    "type": "payment",
                    "data": self._last_payment,
                    "timestamp": self._last_payment_time,
                }).decode())

            # Keep alive
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = orjson.loads(msg.data)
                        if data.get("type") == "ping":
                            await ws.send_str(orjson.dumps({
                                "type": "pong",
                                "timestamp": time.time(),
                            }).decode())
                    except Exception:
                        pass
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.debug("ws connection closed with exception %s", ws.exception())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("ws handler error: %s", e)
        finally:
            self._clients.discard(ws)
            logger.info("Client disconnected [%s] — total: %d", client_id, len(self._clients))

        return ws

    async def start(self) -> None:
        """Start the aiohttp web server."""
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, WS_SERVER_HOST, WS_SERVER_PORT)
        await site.start()
        logger.info("Dashboard WS/HTTP server on http://%s:%d", WS_SERVER_HOST, WS_SERVER_PORT)

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    async def _safe_send(self, ws: web.WebSocketResponse, msg: str) -> None:
        try:
            await ws.send_str(msg)
        except Exception:
            self._clients.discard(ws)

    async def broadcast_spread(self, data: Any) -> None:
        """Broadcast any opportunity (spot, P2P, triangular)."""
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
        self._last_books = books_summary
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

    async def broadcast_executor_stats(self, stats: dict) -> None:
        msg = orjson.dumps({
            "type": "executor_stats",
            "data": stats,
            "timestamp": time.time(),
        }).decode()

        if self._clients:
            await asyncio.gather(
                *[self._safe_send(ws, msg) for ws in self._clients.copy()],
                return_exceptions=True,
            )

    @property
    def client_count(self) -> int:
        return len(self._clients)
