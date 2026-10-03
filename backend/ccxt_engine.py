"""
MigiArbitrage v3.0 — Unified CCXT Exchange Engine
===================================================
Replaces all individual exchange WS clients with a single ccxt.pro
engine. Manages connections, order book ingestion, wallet status
checks, and memory guardrails for all 9+ exchanges.

v3.0 changes:
- TTL-based fee cache with background refresh loop
- Zero-latency cached fee/wallet lookups for scanner hot path
- Decoupled REST API scraping from scanner critical path
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

from config import (
    ENABLED_EXCHANGES,
    EXCHANGE_API_KEYS,
    EXCHANGE_FEES,
    WITHDRAWAL_FEES,
    SPOT_SYMBOLS,
    ORDER_BOOK_DEPTH,
    MEMORY_LIMIT_MB,
    MEMORY_CHECK_INTERVAL,
)
from orderbook import OrderBookManager

logger = logging.getLogger("migi.ccxt")

# Per-exchange order book depth overrides.
# Some exchanges only accept specific limit values.
_DEPTH_OVERRIDES: dict[str, int] = {
    "bybit": 50,       # bybit spot: [1, 50, 200, 1000]
    "coinbase": 50,    # coinbase: limited depth levels
}


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


# ── Fee Cache Data Structures ────────────────

class _CachedFeeEntry:
    """Cached exchange fee and wallet status for a single asset."""
    __slots__ = ("maker", "taker", "withdraw_fees", "wallet_status", "updated_ns")

    def __init__(
        self,
        maker: float = 0.001,
        taker: float = 0.001,
        withdraw_fees: Optional[dict[str, float]] = None,
        wallet_status: Optional[dict[str, dict]] = None,
    ) -> None:
        self.maker: float = maker
        self.taker: float = taker
        self.withdraw_fees: dict[str, float] = withdraw_fees or {}
        self.wallet_status: dict[str, dict] = wallet_status or {}
        self.updated_ns: int = time.time_ns()


class CCXTEngine:
    """
    Unified exchange engine using ccxt.pro for async WebSocket
    order book streaming across all configured exchanges.

    Features:
    - Single class manages all exchange connections
    - Connection pooling: one ccxt instance per exchange
    - Memory watchdog: auto-restarts WS if RSS > threshold
    - TTL-based fee cache with background refresh
    - Zero-latency cached fee/wallet lookups
    """

    # Fee cache TTL: 15 minutes in nanoseconds
    FEE_CACHE_TTL_NS: int = 15 * 60 * 1_000_000_000

    def __init__(self, book_manager: OrderBookManager) -> None:
        self.book_manager = book_manager
        self._exchanges: dict[str, ccxtpro.Exchange] = {}
        self._running = True
        self._connected: set[str] = set()
        self._tasks: list[asyncio.Task] = []
        self._ws_tasks: dict[str, asyncio.Task] = {}
        self.STREAM_TIMEOUT_NS = 10 * 1_000_000_000
        self._restart_count = 0
        # Fee cache: (exchange_id, asset) -> _CachedFeeEntry
        self._fee_cache: dict[tuple[str, str], _CachedFeeEntry] = {}
        self._fee_cache_ready = asyncio.Event()

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

    async def _exchange_websocket_loop(self, ex_id: str) -> None:
        """Wrapper to watch all symbols for a specific exchange."""
        exchange = self._exchanges[ex_id]
        tasks = []
        for symbol in SPOT_SYMBOLS:
            task = asyncio.create_task(
                self._watch_orderbook(ex_id, exchange, symbol),
                name=f"ob-{ex_id}-{symbol}",
            )
            tasks.append(task)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self) -> None:
        """Start watching order books on all exchanges concurrently."""
        for ex_id in self._exchanges:
            task = asyncio.create_task(self._exchange_websocket_loop(ex_id))
            self._ws_tasks[ex_id] = task

        logger.info(
            "Started order book watchers across %d exchanges",
            len(self._exchanges),
        )

        # Memory watchdog and stream health monitor
        asyncio.create_task(self._memory_watchdog(), name="mem-watchdog")
        asyncio.create_task(self._monitor_stream_health(), name="stream-health")

        # Wait for all tasks
        await asyncio.gather(*self._ws_tasks.values(), return_exceptions=True)

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

                # Use per-exchange depth or global default
                depth = _DEPTH_OVERRIDES.get(ex_id, ORDER_BOOK_DEPTH)

                while self._running:
                    ob = await exchange.watch_order_book(symbol, depth)

                    # Update our internal order book (in-place mutation, zero alloc)
                    book = self.book_manager.get_or_create(ex_id, symbol)
                    book.update_snapshot(
                        [[float(level[0]), float(level[1])] for level in ob["bids"][:ORDER_BOOK_DEPTH]],
                        [[float(level[0]), float(level[1])] for level in ob["asks"][:ORDER_BOOK_DEPTH]],
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

    async def _monitor_stream_health(self) -> None:
        """Requirement #2: Background loop that hunts for silent freezes every 5 seconds."""
        await asyncio.sleep(10)  # Grace period during boot startup
        
        while self._running:
            try:
                now_ns = time.time_ns()
                
                for ex_id, exchange in list(self._exchanges.items()):
                    # Get all managed books for this specific exchange
                    ex_books = [
                        b for b in self.book_manager._books.values() 
                        if b.exchange == ex_id and b.last_update_ns > 0
                    ]
                    
                    if not ex_books:
                        continue
                    
                    # Find the freshest update time among all pairs for this exchange
                    latest_ex_update = max(b.last_update_ns for b in ex_books)
                    elapsed_ns = now_ns - latest_ex_update
                    
                    if elapsed_ns > self.STREAM_TIMEOUT_NS:
                        logger.warning(
                            "🚨 [STREAM FREEZE] %s showed zero activity for "
                            "%.2fs. Invalidating books and recycling...",
                            ex_id.upper(), elapsed_ns / 1_000_000_000
                        )
                        
                        # Instantly invalidate books to clean up the dashboard
                        for book in ex_books:
                            book._valid = False
                        
                        # Trigger isolated surgical strike recovery
                        asyncio.create_task(self._recycle_exchange_stream(ex_id))
                        
            except Exception as e:
                logger.error(f"Error in stream health monitor loop: {e}")
                
            await asyncio.sleep(5)  # Check intervals

    async def _recycle_exchange_stream(self, exchange_id: str) -> None:
        """Requirement #3: Reinitializes a dead exchange connection without stopping the engine."""
        # Prevent concurrent overlapping recycles for the same exchange
        if getattr(self._exchanges[exchange_id], "_recycling", False):
            return
        
        self._exchanges[exchange_id]._recycling = True
        logger.info("🔄 Commencing isolated teardown for %s WebSocket stream...", exchange_id)
        
        # 1. Cancel existing listening loop task
        if exchange_id in self._ws_tasks:
            self._ws_tasks[exchange_id].cancel()
            try:
                await self._ws_tasks[exchange_id]
            except asyncio.CancelledError:
                pass
        
        # 2. Close sockets gracefully via CCXT
        try:
            await self._exchanges[exchange_id].close()
        except Exception as e:
            logger.debug("Exception during socket close for %s: %s", exchange_id, e)

        # 3. Re-initialize stream loop with exponential backoff safety
        backoff = 2.0
        max_backoff = 60.0
        
        while self._running:
            try:
                logger.info("🔌 Attempting clean reconnect to %s...", exchange_id)
                
                # Re-instantiate the exchange instance to completely clear internal WS cache
                exchange_class = getattr(ccxtpro, exchange_id)
                keys = EXCHANGE_API_KEYS.get(exchange_id, {})
                config = {
                    "enableRateLimit": True,
                    "options": {
                        "defaultType": "spot",
                        "watchOrderBook": {"limit": ORDER_BOOK_DEPTH},
                    },
                }
                if keys.get("apiKey"):
                    config.update(keys)
                self._exchanges[exchange_id] = exchange_class(config)
                
                # Spawn the new active listening task wrapper
                new_task = asyncio.create_task(self._exchange_websocket_loop(exchange_id))
                self._ws_tasks[exchange_id] = new_task
                
                # Check back briefly to ensure it didn't instantly crash
                await asyncio.sleep(2)
                if not new_task.done():
                    logger.info("✅ %s stream successfully recovered and active.", exchange_id.upper())
                    break
                    
            except Exception as e:
                logger.error("❌ Reconnection failed for %s: %s. Retrying in %ss...", exchange_id, e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                
        self._exchanges[exchange_id]._recycling = False

    # ── Fee Cache Layer ──────────────────────────

    async def _fee_refresh_loop(self) -> None:
        """
        Background task: refresh fee & wallet caches every 15 minutes.

        Completely decoupled from the scanner critical path.
        Staggered 2s sleep between exchanges to avoid rate-limit bursts.
        """
        logger.info("Fee cache refresh loop started (TTL=15min)")

        while self._running:
            refresh_count = 0

            for ex_id, exchange in self._exchanges.items():
                try:
                    # ── Fetch currency info (withdrawal fees + wallet status) ──
                    currencies = {}
                    if exchange.has.get("fetchCurrencies", False):
                        try:
                            currencies = await exchange.fetch_currencies()
                        except Exception as e:
                            logger.debug("[%s] fetch_currencies failed: %s", ex_id, e)
                    
                    if not currencies:
                        try:
                            await exchange.load_markets()
                            currencies = getattr(exchange, "currencies", {})
                        except Exception as e:
                            logger.debug("[%s] load_markets fallback failed: %s", ex_id, e)
                            
                    deposit_withdraw_fees = {}
                    if exchange.has.get("fetchDepositWithdrawFees", False):
                        try:
                            deposit_withdraw_fees = await exchange.fetch_deposit_withdraw_fees()
                        except Exception as e:
                            logger.debug("[%s] fetch_deposit_withdraw_fees failed: %s", ex_id, e)

                    # We iterate over our watched SPOT_SYMBOLS to only cache what we care about
                    assets_to_cache = {s.split("/")[0].upper() for s in SPOT_SYMBOLS} | {s.split("/")[1].upper() for s in SPOT_SYMBOLS}
                    
                    for asset in assets_to_cache:
                        withdraw_fees: dict[str, float] = {}
                        wallet_status: dict[str, dict] = {}
                        
                        info = currencies.get(asset, {})
                        networks = info.get("networks", {})
                        
                        # Fallback to deposit_withdraw_fees if networks not in currencies
                        if not networks and asset in deposit_withdraw_fees:
                            networks = deposit_withdraw_fees[asset].get("networks", {})
                            
                        for net_id, net_info in networks.items():
                            net_key = net_id.upper()
                            fee_raw = net_info.get("fee", 0.0)
                            try:
                                fee = float(fee_raw) if fee_raw is not None else 0.0
                            except (ValueError, TypeError):
                                fee = 0.0
                                
                            withdraw_fees[net_key] = fee
                            wallet_status[net_key] = {
                                "deposit_enabled": bool(net_info.get("deposit", net_info.get("depositEnable", True))),
                                "withdraw_enabled": bool(net_info.get("withdraw", net_info.get("withdrawEnable", True))),
                                "min_withdraw": float(net_info.get("limits", {}).get("withdraw", {}).get("min", 0) or 0),
                            }

                        # Fallback for exchanges that don't expose per-network data
                        if not networks and info:
                            withdraw_fees[asset.upper()] = float(info.get("fee", 0) or 0)
                            wallet_status[asset.upper()] = {
                                "deposit_enabled": bool(info.get("deposit", True)),
                                "withdraw_enabled": bool(info.get("withdraw", True)),
                                "min_withdraw": 0.0,
                            }
                            
                        # If we successfully parsed any info for the asset
                        if withdraw_fees or wallet_status:
                            key = (ex_id, asset.upper())
                            existing = self._fee_cache.get(key)
                            if existing:
                                existing.withdraw_fees = withdraw_fees
                                existing.wallet_status = wallet_status
                                existing.updated_ns = time.time_ns()
                            else:
                                self._fee_cache[key] = _CachedFeeEntry(
                                    withdraw_fees=withdraw_fees,
                                    wallet_status=wallet_status,
                                )
                            refresh_count += 1

                    # ── Fetch trading fees (maker/taker) ──
                    try:
                        if exchange.has.get("fetchTradingFees"):
                            trading_fees = await exchange.fetch_trading_fees()
                            for symbol, fee_info in trading_fees.items():
                                if "/" not in symbol:
                                    continue
                                base = symbol.split("/")[0].upper()
                                key = (ex_id, base)
                                entry = self._fee_cache.get(key)
                                if entry:
                                    entry.maker = float(fee_info.get("maker", 0.001) or 0.001)
                                    entry.taker = float(fee_info.get("taker", 0.001) or 0.001)
                    except Exception as fee_exc:
                        logger.debug("[%s] Trading fee fetch failed: %s", ex_id, fee_exc)

                except Exception as exc:
                    logger.warning("[%s] Fee cache refresh failed: %s", ex_id, exc)

                # Stagger between exchanges to avoid rate-limit bursts
                await asyncio.sleep(2)

            if refresh_count > 0:
                logger.info(
                    "💰 Fee cache refreshed: %d assets across %d exchanges",
                    refresh_count, len(self._exchanges),
                )

            if not self._fee_cache_ready.is_set():
                self._fee_cache_ready.set()
                logger.info("Fee cache initial population complete")

            # Sleep until next refresh cycle
            await asyncio.sleep(900)  # 15 minutes

    def get_cached_withdrawal_fee(
        self, exchange: str, asset: str, network: str
    ) -> float:
        """
        Zero-latency cached withdrawal fee lookup.
        Falls back to hardcoded config if cache miss.
        """
        entry = self._fee_cache.get((exchange, asset.upper()))
        if entry and (time.time_ns() - entry.updated_ns) < self.FEE_CACHE_TTL_NS:
            cached = entry.withdraw_fees.get(network.upper())
            if cached is not None:
                return cached

        # Fallback to hardcoded config
        return WITHDRAWAL_FEES.get(asset, {}).get(network, 0.0)

    def get_cached_wallet_status(
        self, exchange: str, asset: str, network: str
    ) -> Optional[dict]:
        """
        Zero-latency cached wallet status lookup.
        Returns dict with deposit_enabled/withdraw_enabled, or None if cache miss.
        """
        entry = self._fee_cache.get((exchange, asset.upper()))
        if entry and (time.time_ns() - entry.updated_ns) < self.FEE_CACHE_TTL_NS:
            status = entry.wallet_status.get(network.upper())
            if status:
                return status
        return None

    def get_cached_trading_fees(
        self, exchange: str, asset: str
    ) -> tuple[float, float]:
        """
        Zero-latency cached maker/taker fee lookup.
        Returns (maker, taker) rates. Falls back to hardcoded config.
        """
        entry = self._fee_cache.get((exchange, asset.upper()))
        if entry and (time.time_ns() - entry.updated_ns) < self.FEE_CACHE_TTL_NS:
            return (entry.maker, entry.taker)

        # Fallback to hardcoded config
        fees = EXCHANGE_FEES.get(exchange, {"maker": 0.001, "taker": 0.001})
        return (fees["maker"], fees["taker"])

    def get_all_cached_networks(
        self, exchange: str, asset: str
    ) -> dict[str, float]:
        """
        Return all cached networks and their withdrawal fees for an asset.
        Used by gas_router to enumerate available routes.
        """
        entry = self._fee_cache.get((exchange, asset.upper()))
        if entry and (time.time_ns() - entry.updated_ns) < self.FEE_CACHE_TTL_NS:
            return dict(entry.withdraw_fees)
        return WITHDRAWAL_FEES.get(asset, {})

    # ── Legacy wallet check (now cache-backed) ───

    async def check_wallet_status(
        self, exchange_id: str, asset: str, network: str
    ) -> Optional[dict]:
        """
        Check deposit/withdrawal status for an asset.
        Now primarily serves from cache; falls back to REST API.
        """
        # Try cache first (zero-latency)
        cached = self.get_cached_wallet_status(exchange_id, asset, network)
        if cached:
            return {
                "asset": asset,
                "network": network,
                "deposit_enabled": cached.get("deposit_enabled", True),
                "withdraw_enabled": cached.get("withdraw_enabled", True),
                "withdraw_fee": self.get_cached_withdrawal_fee(exchange_id, asset, network),
                "min_withdraw": cached.get("min_withdraw", 0.0),
            }

        # Cache miss — fall back to REST API
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
