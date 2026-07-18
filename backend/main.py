"""
MigiArbitrage v3.0 — Main Orchestrator
========================================
Starts CCXT engine, spot scanner, P2P scanner, triangular scanner,
dashboard WS server, and memory watchdog. Coordinates graceful shutdown.

v3.0 changes:
- uvloop for Linux (conditional, graceful fallback)
- ProcessPoolExecutor for CPU-bound offloading
- Activity-aware smart GC (no stop-the-world during active scanning)
- Fee cache refresh background task
"""

from __future__ import annotations
import asyncio
import gc
import logging
import signal
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Optional

from config import (
    SCAN_INTERVAL_MS, ENABLED_EXCHANGES, P2P_ENABLED, TRIANGULAR_ENABLED,
    SIGNALS_ENABLED,
)
from orderbook import OrderBookManager
from ccxt_engine import CCXTEngine
from preflight import PreFlightChecker
from scanner import ArbitrageScanner
from p2p_scanner import P2PScanner
from triangular import TriangularScanner
from signals import SignalScanner
from alerter import TelegramAlerter
from ws_server import DashboardWSServer

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


# ── uvloop (Linux-only, graceful fallback) ───
def _install_uvloop() -> None:
    """Install uvloop as the default event loop policy on Linux."""
    if sys.platform == "linux":
        try:
            import uvloop
            uvloop.install()
            logger.info("✅ uvloop installed as event loop policy")
        except ImportError:
            logger.info("uvloop not available, using default asyncio loop")
    else:
        logger.debug("Non-Linux platform (%s), skipping uvloop", sys.platform)

_install_uvloop()


# ── Shared Executor ──────────────────────────
# Single-worker executor matching the 1vCPU deployment target.
# Used by signal scanner to offload numpy RSI/MACD calculations.
_CPU_EXECUTOR = ProcessPoolExecutor(max_workers=1)


async def smart_gc(
    book_manager: OrderBookManager,
    interval: float = 30.0,
) -> None:
    """
    Activity-aware garbage collection.

    Instead of blindly calling gc.collect() every 30s (which causes
    stop-the-world pauses during active scanning), we only collect
    during detected idle windows when no order book has been updated
    in the last 2 seconds.
    """
    while True:
        await asyncio.sleep(interval)

        # Check if any book was updated in the last 2 seconds
        now_ns = time.time_ns()
        idle_threshold_ns = 2_000_000_000  # 2 seconds

        recent_activity = any(
            (now_ns - book.last_update_ns) < idle_threshold_ns
            for book in book_manager._books.values()
            if book.last_update_ns > 0
        )

        if not recent_activity:
            # Idle window — safe to do a fast gen-0 collection
            collected = gc.collect(generation=0)
            if collected > 0:
                logger.debug("Smart GC (idle): collected %d objects", collected)
        # Active period — skip, let the threshold-based collector handle it


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
        fresh_books = sum(1 for b in books.values() if b.get("fresh", False))

        logger.info(
            "📊 Status: spot_scans=%d spot_ops=%d | p2p_scans=%d p2p_ops=%d | "
            "tri_scans=%d tri_ops=%d | signals=%s | books=%d/%d (fresh=%d) | ws=%d | exchanges=%d",
            spot["scans"], spot["opportunities"],
            p2p["p2p_scans"], p2p["p2p_opportunities"],
            tri["triangular_scans"], tri["triangular_opportunities"],
            "ACTIVE" if SIGNALS_ENABLED else "OFF",
            valid_books, len(books), fresh_books,
            ws_server.client_count,
            len(ccxt_engine.connected_exchanges),
        )

        await ws_server.broadcast_books(books)


async def main() -> None:
    """Main application entry point."""
    logger.info("=" * 60)
    logger.info("  MigiArbitrage v3.0 — Real-Time Arbitrage Scanner")
    logger.info("  ⚠️  ALERT-ONLY + SEMI-AUTO MODE")
    logger.info("  🚀 uvloop=%s | executor=1 worker | smart-GC=ON",
                "ON" if sys.platform == "linux" else "OFF (Windows)")
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
        ccxt_engine=ccxt_engine,
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

    # Signal scanner (with CPU executor for numpy offloading)
    async def on_signal(signal_dict):
        await ws_server.broadcast_spread(signal_dict)
        # We don't send Telegram alerts for signals yet per prompt,
        # but could add alerter.send_any_opportunity(signal_dict) here

    signal_scanner = SignalScanner(
        ccxt_engine=ccxt_engine,
        broadcast_func=on_signal,
        executor=_CPU_EXECUTOR,
    ) if SIGNALS_ENABLED else None

    # ── Start all tasks ──
    await ws_server.start()
    await alerter.send_startup_message()

    tasks = [
        asyncio.create_task(ccxt_engine.run(), name="ccxt-engine"),
        asyncio.create_task(scanner.run(), name="spot-scanner"),
        asyncio.create_task(smart_gc(book_manager), name="smart-gc"),
        asyncio.create_task(
            periodic_status(
                book_manager, scanner, p2p_scanner, tri_scanner,
                signal_scanner, ws_server, ccxt_engine,
            ),
            name="status",
        ),
        asyncio.create_task(ccxt_engine._fee_refresh_loop(), name="fee-cache"),
    ]

    if P2P_ENABLED:
        tasks.append(asyncio.create_task(p2p_scanner.run(), name="p2p-scanner"))
        logger.info("P2P scanner enabled — KES/USDT monitoring active")

    if TRIANGULAR_ENABLED:
        tasks.append(asyncio.create_task(tri_scanner.run(), name="tri-scanner"))
        logger.info("Triangular scanner enabled")

    if SIGNALS_ENABLED and signal_scanner:
        tasks.append(asyncio.create_task(signal_scanner.run(), name="signal-scanner"))
        logger.info("Predictive signals enabled (numpy backend, executor offload)")

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

    # Enable slow callback detection in debug mode
    loop.slow_callback_duration = 0.05  # Warn on callbacks > 50ms

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
    _CPU_EXECUTOR.shutdown(wait=False)

    logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
