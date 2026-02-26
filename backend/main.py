"""
MigiArbitrage — Main Orchestrator
===================================
Entry point that starts all exchange WebSocket connections,
the arbitrage scanner, dashboard WS server, and coordinates
graceful shutdown. Includes aggressive GC for 2GB RAM servers.

⚠️ ALERT-ONLY MODE — No trade execution code exists in this system.
"""

from __future__ import annotations
import asyncio
import gc
import logging
import signal
import sys
import time

# Ensure the parent directory is in the path for imports
sys.path.insert(0, "/app")

from backend.config import SCAN_INTERVAL_MS, MONITORED_PAIRS
from backend.orderbook import OrderBookManager
from backend.exchanges.binance import BinanceClient
from backend.exchanges.kraken import KrakenClient
from backend.exchanges.kucoin import KuCoinClient
from backend.preflight import PreFlightChecker
from backend.scanner import ArbitrageScanner
from backend.alerter import TelegramAlerter
from backend.ws_server import DashboardWSServer

# ── Logging ──────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(name)-18s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("migi.main")

# Silence noisy library logs
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)


async def periodic_gc(interval: float = 30.0) -> None:
    """Periodically force garbage collection to keep memory low."""
    while True:
        await asyncio.sleep(interval)
        collected = gc.collect()
        if collected > 0:
            logger.debug("GC collected %d objects", collected)


async def periodic_status(
    book_manager: OrderBookManager,
    scanner: ArbitrageScanner,
    ws_server: DashboardWSServer,
    interval: float = 60.0,
) -> None:
    """Log system status periodically and broadcast book summaries."""
    while True:
        await asyncio.sleep(interval)
        stats = scanner.stats
        books = book_manager.stats()
        valid_books = sum(1 for b in books.values() if b["valid"])

        logger.info(
            "📊 Status: scans=%d | opportunities=%d | books=%d/%d valid | ws_clients=%d",
            stats["scans"],
            stats["opportunities"],
            valid_books,
            len(books),
            ws_server.client_count,
        )

        # Broadcast book summary to frontend
        await ws_server.broadcast_books(books)


async def main() -> None:
    """Main application entry point."""
    logger.info("=" * 60)
    logger.info("  MigiArbitrage — Real-Time Arbitrage Scanner")
    logger.info("  ⚠️  ALERT-ONLY MODE — No trades will be executed")
    logger.info("=" * 60)
    logger.info(
        "Monitoring %d pairs across 3 exchanges (Binance, Kraken, KuCoin)",
        len(MONITORED_PAIRS),
    )

    # ── Initialize components ──
    book_manager = OrderBookManager(max_depth=20)

    # Exchange clients
    binance = BinanceClient(book_manager)
    kraken = KrakenClient(book_manager)
    kucoin = KuCoinClient(book_manager)

    exchange_clients = {
        "binance": binance,
        "kraken": kraken,
        "kucoin": kucoin,
    }

    # Pre-flight checker
    preflight = PreFlightChecker(exchange_clients)

    # Telegram alerter
    alerter = TelegramAlerter()

    # Dashboard WebSocket server
    ws_server = DashboardWSServer()

    # Scanner with broadcast callback
    async def on_spread(opportunity):
        await ws_server.broadcast_spread(opportunity.to_dict())

    scanner = ArbitrageScanner(
        book_manager=book_manager,
        preflight=preflight,
        alerter=alerter,
        on_spread=on_spread,
    )

    # ── Start all tasks ──
    await ws_server.start()
    await alerter.send_startup_message()

    tasks = [
        asyncio.create_task(binance.run_forever(), name="binance-ws"),
        asyncio.create_task(kraken.run_forever(), name="kraken-ws"),
        asyncio.create_task(kucoin.run_forever(), name="kucoin-ws"),
        asyncio.create_task(scanner.run(), name="scanner"),
        asyncio.create_task(periodic_gc(), name="gc"),
        asyncio.create_task(
            periodic_status(book_manager, scanner, ws_server),
            name="status",
        ),
    ]

    logger.info("All tasks started — scanner running every %dms", SCAN_INTERVAL_MS)

    # ── Graceful shutdown ──
    shutdown_event = asyncio.Event()

    def signal_handler() -> None:
        logger.info("Shutdown signal received...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await shutdown_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass

    logger.info("Shutting down...")

    # Stop components
    scanner.stop()
    binance.stop()
    kraken.stop()
    kucoin.stop()

    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)
    await ws_server.stop()

    logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
