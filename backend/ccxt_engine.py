"""
MigiArbitrage v2.0 — Unified CCXT Exchange Engine
===================================================
Replaces all individual exchange WS clients with a single ccxt.pro
engine. Manages connections, order book ingestion, wallet status
checks, and memory guardrails for all 9+ exchanges.
"""

from __future__ import annotations
import asyncio
import gc
import logging
import os
import time
from typing import Optional

import ccxt.pro as ccxtpro
import ccxt

from backend.config import (
    ENABLED_EXCHANGES,
    EXCHANGE_API_KEYS,
    SPOT_SYMBOLS,
    ORDER_BOOK_DEPTH,
    MEMORY_LIMIT_MB,
    MEMORY_CHECK_INTERVAL,
)
from backend.orderbook import OrderBookManager

logger = logging.getLogger("migi.ccxt")


def _get_memory_mb() -> float:
    """Get current process RSS in MB. Cross-platform."""
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except ImportError:
        # Windows fallback
        try:
            import psutil
            return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
        except ImportError:
            return 0.0


class CCXTEngine:
    """
    Unified exchange engine using ccxt.pro for async WebSocket
    order book streaming across all configured exchanges.

    Features:
    - Single class manages all exchange connections
    - Connection pooling: one ccxt instance per exchange
    - Memory watchdog: auto-restarts WS if RSS > threshold
    - Wallet status via fetchCurrencies()
    """

    def __init__(self, book_manager: OrderBookManager) -> None:
        self.book_manager = book_manager
        self._exchanges: dict[str, ccxtpro.Exchange] = {}
        self._running = True
        self._connected: set[str] = set()
        self._tasks: list[asyncio.Task] = []
        self._restart_count = 0

    async def initialize(self) -> None:
        """Create ccxt.pro exchange instances for all enabled exchanges."""
        for ex_id in ENABLED_EXCHANGES:
            try:
                exchange_class = getattr(ccxtpro, ex_id, None)
                if exchange_class is None:
                    # Fallback to sync ccxt for REST-only exchanges
                    logger.warning("[%s] No ccxt.pro class, skipping WS", ex_id)
                    continue

                keys = EXCHANGE_API_KEYS.get(ex_id, {})
                config = {
                    "enableRateLimit": True,
                    "options": {
                        "defaultType": "spot",
                        "watchOrderBook": {"limit": ORDER_BOOK_DEPTH},
                    },
                }
                # Only add keys if they exist
                if keys.get("apiKey"):
                    config.update(keys)

                exchange = exchange_class(config)
                self._exchanges[ex_id] = exchange
                logger.info("[%s] Initialized", ex_id)

            except Exception as exc:
                logger.error("[%s] Init failed: %s", ex_id, exc)

        logger.info(
            "CCXT engine ready — %d/%d exchanges initialized",
            len(self._exchanges), len(ENABLED_EXCHANGES),
        )

    async def run(self) -> None:
        """Start watching order books on all exchanges concurrently."""
        for ex_id, exchange in self._exchanges.items():
            for symbol in SPOT_SYMBOLS:
                task = asyncio.create_task(
                    self._watch_orderbook(ex_id, exchange, symbol),
                    name=f"ob-{ex_id}-{symbol}",
                )
                self._tasks.append(task)

        logger.info(
            "Started %d order book watchers across %d exchanges",
            len(self._tasks), len(self._exchanges),
        )

        # Memory watchdog
        asyncio.create_task(self._memory_watchdog(), name="mem-watchdog")

        # Wait for all tasks
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _watch_orderbook(
        self, ex_id: str, exchange: ccxtpro.Exchange, symbol: str
    ) -> None:
        """Watch a single order book with auto-reconnect."""
        backoff = 1.0
        max_backoff = 60.0

        while self._running:
            try:
                # Check if exchange supports this symbol
                if not exchange.markets:
                    await exchange.load_markets()

                if symbol not in exchange.markets:
                    logger.debug("[%s] %s not listed, skipping", ex_id, symbol)
                    return

                self._connected.add(ex_id)

                while self._running:
                    ob = await exchange.watch_order_book(symbol, ORDER_BOOK_DEPTH)

                    # Update our internal order book
                    book = self.book_manager.get_or_create(ex_id, symbol)
                    book.update_snapshot(
                        [[float(p), float(q)] for p, q in ob["bids"][:ORDER_BOOK_DEPTH]],
                        [[float(p), float(q)] for p, q in ob["asks"][:ORDER_BOOK_DEPTH]],
                    )

                    backoff = 1.0  # Reset on success

            except asyncio.CancelledError:
                break
            except ccxt.NetworkError as exc:
                logger.warning("[%s] %s network error: %s (retry %.0fs)", ex_id, symbol, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
            except ccxt.ExchangeNotAvailable as exc:
                logger.warning("[%s] %s unavailable: %s (retry %.0fs)", ex_id, symbol, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
            except Exception as exc:
                logger.error("[%s] %s error: %s (retry %.0fs)", ex_id, symbol, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    async def check_wallet_status(
        self, exchange_id: str, asset: str, network: str
    ) -> Optional[dict]:
        """
        Check deposit/withdrawal status for an asset via ccxt fetchCurrencies().
        Returns dict with deposit_enabled, withdraw_enabled, fee, etc.
        """
        exchange = self._exchanges.get(exchange_id)
        if not exchange:
            return None

        try:
            currencies = await exchange.fetch_currencies()
            currency = currencies.get(asset.upper())
            if not currency:
                return None

            # Look through networks for the right one
            networks = currency.get("networks", {})
            for net_id, net_info in networks.items():
                if net_id.upper() == network.upper() or net_info.get("network", "").upper() == network.upper():
                    return {
                        "asset": asset,
                        "network": network,
                        "deposit_enabled": net_info.get("deposit", True),
                        "withdraw_enabled": net_info.get("withdraw", True),
                        "withdraw_fee": float(net_info.get("fee", 0) or 0),
                        "min_withdraw": float(net_info.get("limits", {}).get("withdraw", {}).get("min", 0) or 0),
                    }

            # Fallback: use the currency-level info
            return {
                "asset": asset,
                "network": network,
                "deposit_enabled": currency.get("deposit", True),
                "withdraw_enabled": currency.get("withdraw", True),
                "withdraw_fee": float(currency.get("fee", 0) or 0),
                "min_withdraw": 0.0,
            }

        except Exception as exc:
            logger.error("[%s] Wallet check failed for %s: %s", exchange_id, asset, exc)
            return None

    async def _memory_watchdog(self) -> None:
        """Monitor memory usage and restart connections if threshold exceeded."""
        while self._running:
            await asyncio.sleep(MEMORY_CHECK_INTERVAL)

            mem_mb = _get_memory_mb()
            if mem_mb <= 0:
                continue

            if mem_mb > MEMORY_LIMIT_MB:
                logger.warning(
                    "⚠️ Memory %.0f MB exceeds %d MB threshold — restarting WS connections",
                    mem_mb, MEMORY_LIMIT_MB,
                )
                self._restart_count += 1

                # Close all exchange connections
                for ex_id, exchange in self._exchanges.items():
                    try:
                        await exchange.close()
                        logger.info("[%s] Connection closed for memory recovery", ex_id)
                    except Exception:
                        pass

                # Force GC
                gc.collect()
                await asyncio.sleep(5)

                # Re-initialize connections
                for ex_id in list(self._exchanges.keys()):
                    try:
                        exchange_class = getattr(ccxtpro, ex_id)
                        keys = EXCHANGE_API_KEYS.get(ex_id, {})
                        config = {
                            "enableRateLimit": True,
                            "options": {
                                "defaultType": "spot",
                                "watchOrderBook": {"limit": ORDER_BOOK_DEPTH},
                            },
                        }
                        if keys.get("apiKey"):
                            config.update(keys)
                        self._exchanges[ex_id] = exchange_class(config)
                    except Exception as exc:
                        logger.error("[%s] Re-init failed: %s", ex_id, exc)

                logger.info("Memory recovery complete (restart #%d)", self._restart_count)
            else:
                logger.debug("Memory: %.0f MB (limit: %d MB)", mem_mb, MEMORY_LIMIT_MB)

    @property
    def exchange_ids(self) -> list[str]:
        """Return list of initialized exchange IDs."""
        return list(self._exchanges.keys())

    @property
    def connected_exchanges(self) -> set[str]:
        return self._connected

    def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()

    async def close(self) -> None:
        """Gracefully close all exchange connections."""
        self.stop()
        for ex_id, exchange in self._exchanges.items():
            try:
                await exchange.close()
            except Exception:
                pass
