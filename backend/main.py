"""
MigiArbitrage v2.0 — Main Orchestrator
========================================
Starts CCXT engine, spot scanner, P2P scanner, triangular scanner,
dashboard WS server, and memory watchdog. Coordinates graceful shutdown.
"""

from __future__ import annotations
import asyncio
import gc
import logging
import signal
import sys
import time

sys.path.insert(0, "/app")

from backend.config import (
    SCAN_INTERVAL_MS, ENABLED_EXCHANGES, P2P_ENABLED, TRIANGULAR_ENABLED,
    SIGNALS_ENABLED
)
from backend.orderbook import OrderBookManager
from backend.ccxt_engine import CCXTEngine
from backend.preflight import PreFlightChecker
from backend.scanner import ArbitrageScanner
from backend.p2p_scanner import P2PScanner
from backend.triangular import TriangularScanner
from backend.signals import SignalScanner
from backend.alerter import TelegramAlerter
from backend.ws_server import DashboardWSServer

# ── Logging ──────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(name)-18s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("migi.main")

logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("ccxt").setLevel(logging.WARNING)


async def periodic_gc(interval: float = 30.0) -> None:
    """Periodically force garbage collection."""
    while True:
        await asyncio.sleep(interval)
        collected = gc.collect()
        if collected > 0:
            logger.debug("GC collected %d objects", collected)


async def periodic_status(
    book_manager: OrderBookManager,
    scanner: ArbitrageScanner,
    p2p_scanner: P2PScanner,
    tri_scanner: TriangularScanner,
    signal_scanner: Optional[SignalScanner],
    ws_server: DashboardWSServer,
    ccxt_engine: CCXTEngine,
    interval: float = 60.0,
) -> None:
    """Log system status periodically."""
    while True:
        await asyncio.sleep(interval)

        spot = scanner.stats
        p2p = p2p_scanner.stats
        tri = tri_scanner.stats
        books = book_manager.stats()
        valid_books = sum(1 for b in books.values() if b["valid"])

        logger.info(
            "📊 Status: spot_scans=%d spot_ops=%d | p2p_scans=%d p2p_ops=%d | "
            "tri_scans=%d tri_ops=%d | signals=%s | books=%d/%d | ws=%d | exchanges=%d",
            spot["scans"], spot["opportunities"],
            p2p["p2p_scans"], p2p["p2p_opportunities"],
            tri["triangular_scans"], tri["triangular_opportunities"],
            "ACTIVE" if SIGNALS_ENABLED else "OFF",
            valid_books, len(books),
            ws_server.client_count,
            len(ccxt_engine.connected_exchanges),
        )

        await ws_server.broadcast_books(books)


async def main() -> None:
    """Main application entry point."""
    logger.info("=" * 60)
    logger.info("  MigiArbitrage v2.0 — Real-Time Arbitrage Scanner")
    logger.info("  ⚠️  ALERT-ONLY + SEMI-AUTO MODE")
    logger.info("=" * 60)
    logger.info("Exchanges: %s", ", ".join(ENABLED_EXCHANGES))

    # ── Initialize components ──
    book_manager = OrderBookManager(max_depth=20)

    # CCXT Engine (replaces individual exchange clients)
    ccxt_engine = CCXTEngine(book_manager)
    await ccxt_engine.initialize()

    # Pre-flight checker (uses CCXT for wallet status)
    preflight = PreFlightChecker(ccxt_engine=ccxt_engine)

    # Telegram alerter
    alerter = TelegramAlerter()

    # Dashboard WebSocket server
    ws_server = DashboardWSServer()

    # Spot scanner
    async def on_spot_spread(opportunity):
        await ws_server.broadcast_spread(opportunity)

    scanner = ArbitrageScanner(
        book_manager=book_manager,
        preflight=preflight,
        alerter=alerter,
        exchange_ids=ccxt_engine.exchange_ids,
        on_spread=on_spot_spread,
    )

    # P2P scanner
    async def on_p2p_spread(opp_dict):
        await ws_server.broadcast_spread(opp_dict)
        await alerter.send_any_opportunity(opp_dict)

    p2p_scanner = P2PScanner(on_spread=on_p2p_spread)

    # Triangular scanner
    async def on_tri_spread(opp_dict):
        await ws_server.broadcast_spread(opp_dict)
        await alerter.send_any_opportunity(opp_dict)

    tri_scanner = TriangularScanner(
        book_manager=book_manager,
        on_spread=on_tri_spread,
    )

    # Signal scanner
    async def on_signal(signal_dict):
        await ws_server.broadcast_spread(signal_dict)
        # We don't send Telegram alerts for signals yet per prompt, 
        # but could add alerter.send_any_opportunity(signal_dict) here

    signal_scanner = SignalScanner(ccxt_engine=ccxt_engine, broadcast_func=on_signal) if SIGNALS_ENABLED else None

    # ── Start all tasks ──
    await ws_server.start()
    await alerter.send_startup_message()

    tasks = [
        asyncio.create_task(ccxt_engine.run(), name="ccxt-engine"),
        asyncio.create_task(scanner.run(), name="spot-scanner"),
        asyncio.create_task(periodic_gc(), name="gc"),
        asyncio.create_task(
            periodic_status(
                book_manager, scanner, p2p_scanner, tri_scanner,
                signal_scanner, ws_server, ccxt_engine,
            ),
            name="status",
        ),
    ]

    if P2P_ENABLED:
        tasks.append(asyncio.create_task(p2p_scanner.run(), name="p2p-scanner"))
        logger.info("P2P scanner enabled — KES/USDT monitoring active")

    if TRIANGULAR_ENABLED:
        tasks.append(asyncio.create_task(tri_scanner.run(), name="tri-scanner"))
        logger.info("Triangular scanner enabled")

    if SIGNALS_ENABLED and signal_scanner:
        tasks.append(asyncio.create_task(signal_scanner.run(), name="signal-scanner"))
        logger.info("Predictive signals enabled")

    logger.info("All %d tasks started — scanner running every %dms", len(tasks), SCAN_INTERVAL_MS)

    # ── Graceful shutdown ──
    shutdown_event = asyncio.Event()

    def signal_handler():
        logger.info("Shutdown signal received...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    try:
        await shutdown_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass

    logger.info("Shutting down...")

    scanner.stop()
    p2p_scanner.stop()
    tri_scanner.stop()
    if signal_scanner:
        signal_scanner.stop()

    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)
    await ccxt_engine.close()
    await ws_server.stop()

    logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
