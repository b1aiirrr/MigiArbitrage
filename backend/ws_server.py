"""
MigiArbitrage v3.0 — WebSocket Server (aiohttp)
===============================================
Streams spreads, P2P opportunities, and triangular alerts to the frontend.
Handles Azure Health Probes and Webhook API using aiohttp.web.
"""

from __future__ import annotations
import asyncio
import hashlib
import hmac
import logging
import time
from collections import deque
from typing import Any

import orjson
import aiohttp
from aiohttp import web

from config import (
    WS_SERVER_HOST, WS_SERVER_PORT, ALERT_HISTORY_SIZE, WS_ALLOWED_ORIGINS,
    PAYSTACK_SECRET_KEY, SAFARICOM_WEBHOOK_SECRET, SAFARICOM_ALLOWED_IPS,
)

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
            # ── IP allowlist (if configured) ──
            if SAFARICOM_ALLOWED_IPS:
                client_ip = request.remote or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                if client_ip not in SAFARICOM_ALLOWED_IPS:
                    logger.warning("Rejected Safaricom webhook from unauthorised IP: %s", client_ip)
                    return web.json_response({"ResultCode": 1, "ResultDesc": "Forbidden"}, status=403)

            # ── Shared-secret validation ──
            if SAFARICOM_WEBHOOK_SECRET:
                provided_secret = (
                    request.query.get("secret")
                    or request.headers.get("X-Webhook-Secret", "")
                )
                if not hmac.compare_digest(provided_secret, SAFARICOM_WEBHOOK_SECRET):
                    logger.warning("Rejected Safaricom webhook: invalid shared secret")
                    return web.json_response({"ResultCode": 1, "ResultDesc": "Unauthorized"}, status=401)

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
            raw_body = await request.read()

            # ── HMAC-SHA512 signature verification ──
            if PAYSTACK_SECRET_KEY:
                sig_header = request.headers.get("x-paystack-signature", "")
                expected_sig = hmac.new(
                    PAYSTACK_SECRET_KEY.encode("utf-8"),
                    raw_body,
                    hashlib.sha512,
                ).hexdigest()
                if not sig_header or not hmac.compare_digest(sig_header, expected_sig):
                    logger.warning("Rejected Paystack webhook: invalid signature")
                    return web.Response(text="Invalid signature", status=401)

            payload = orjson.loads(raw_body)
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
        # ── Origin allowlist enforcement ──
        origin = request.headers.get("Origin", "")
        if WS_ALLOWED_ORIGINS and origin and origin not in WS_ALLOWED_ORIGINS:
            logger.warning("Rejected WS connection from unauthorised origin: %s", origin)
            return web.Response(text="Forbidden origin", status=403)

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

        # Auto-seed initial opportunities from live order books so new clients see spreads instantly
        if len(self._alert_history) < 8 and books_summary:
            try:
                by_sym: dict[str, list[tuple[str, float, float]]] = {}
                for k, v in books_summary.items():
                    if not v.get("valid") or not v.get("bid") or not v.get("ask"):
                        continue
                    if ":" not in k:
                        continue
                    ex, sym = k.split(":", 1)
                    by_sym.setdefault(sym, []).append((ex, float(v["bid"]), float(v["ask"])))

                for sym, ex_list in by_sym.items():
                    if len(ex_list) < 2:
                        continue
                    best_buy = min(ex_list, key=lambda x: x[2])
                    best_sell = max(ex_list, key=lambda x: x[1])
                    if best_buy[0] == best_sell[0]:
                        continue
                    ask_price = best_buy[2]
                    bid_price = best_sell[1]
                    raw_spread = ((bid_price - ask_price) / ask_price) * 100

                    # Only seed when a real positive spread exists; never fabricate profit.
                    if raw_spread > 0.0:
                        est_notional = 100.0
                        est_fees = 0.20  # rough taker+withdrawal estimate
                        net_profit_est = max(0.0, est_notional * (raw_spread / 100.0) - est_fees)

                        cand = {
                            "pair": sym,
                            "base": sym.split("/")[0],
                            "quote": sym.split("/")[1] if "/" in sym else "USDT",
                            "arb_type": "spot",
                            "buy_exchange": best_buy[0],
                            "sell_exchange": best_sell[0],
                            "ask_price": round(ask_price, 8),
                            "bid_price": round(bid_price, 8),
                            "raw_spread_pct": round(raw_spread, 4),
                            "volume": round(100.0 / ask_price, 6),
                            "fee_maker": 0.10,
                            "fee_taker": 0.10,
                            "fee_withdrawal": 0.0,
                            "fee_network": 0.0,
                            "net_profit": round(net_profit_est, 2),
                            "preflight": {"passed": False, "network": None, "risk_level": "unknown", "risk_notes": ["Seed estimate — not preflight-checked"]},
                            "payment_method": "",
                            "payment_label": "",
                            "risk_level": "low",
                            "seeded": True,
                            "timestamp": time.time(),
                        }
                        self._alert_history.append(cand)
            except Exception as e:
                logger.debug("Seed spread generation: %s", e)

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
