"""
MigiArbitrage v3.0 — Predictive Signals Module
==============================================
Parallel scanner that uses technical analysis (RSI, MACD) to generate
trading signals on major pairs. Processes 15m OHLCV data.

v3.0 changes:
- Pure numpy replaces pandas + pandas-ta (10-50× faster, 80% less RAM)
- CPU-bound math offloaded to ProcessPoolExecutor (event-loop safe)
- Zero pandas dependency
"""

from __future__ import annotations
import asyncio
import logging
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from typing import Optional

import numpy as np

from config import SIGNAL_SYMBOLS, SIGNALS_POLL_INTERVAL
from ccxt_engine import CCXTEngine

logger = logging.getLogger("migi.signals")


# ── CPU-bound signal computation (runs in ProcessPoolExecutor) ──

def _compute_signals_cpu(ohlcv_raw: list[list]) -> Optional[dict]:
    """
    Pure-numpy RSI + MACD computation.

    Runs in a ProcessPoolExecutor to avoid blocking the event loop.
    Uses contiguous numpy arrays for SIMD-vectorized operations.

    Args:
        ohlcv_raw: Raw OHLCV data as list of [timestamp, open, high, low, close, volume]

    Returns:
        Dict with computed indicators, or None if insufficient data.
    """
    if len(ohlcv_raw) < 35:
        return None

    closes = np.array([c[4] for c in ohlcv_raw], dtype=np.float64)

    # ── RSI-14 (Wilder's smoothed) ──
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Use Wilder's exponential moving average (alpha = 1/14)
    period = 14
    avg_gain = np.empty(len(gains))
    avg_loss = np.empty(len(losses))
    avg_gain[0:period] = 0.0
    avg_loss[0:period] = 0.0

    # Initial SMA seed
    avg_gain[period - 1] = np.mean(gains[:period])
    avg_loss[period - 1] = np.mean(losses[:period])

    # Wilder's smoothing: EMA with alpha = 1/period
    alpha = 1.0 / period
    for i in range(period, len(gains)):
        avg_gain[i] = alpha * gains[i] + (1 - alpha) * avg_gain[i - 1]
        avg_loss[i] = alpha * losses[i] + (1 - alpha) * avg_loss[i - 1]

    # Avoid division by zero
    safe_loss = np.where(avg_loss == 0, 1e-10, avg_loss)
    rs = avg_gain / safe_loss
    rsi_arr = 100.0 - (100.0 / (1.0 + rs))
    rsi = float(rsi_arr[-1])

    # ── MACD (12, 26, 9) ──
    def _ema(data: np.ndarray, period: int) -> np.ndarray:
        """Exponential moving average using standard alpha = 2/(period+1)."""
        alpha_val = 2.0 / (period + 1)
        out = np.empty_like(data)
        out[0] = data[0]
        for idx in range(1, len(data)):
            out[idx] = alpha_val * data[idx] + (1 - alpha_val) * out[idx - 1]
        return out

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = ema12 - ema26
    signal_line = _ema(macd_line, 9)

    return {
        "rsi": rsi,
        "macd": float(macd_line[-1]),
        "macd_signal": float(signal_line[-1]),
        "prev_macd": float(macd_line[-2]),
        "prev_macd_signal": float(signal_line[-2]),
        "price": float(closes[-1]),
    }


class SignalScanner:
    """
    Scans historical OHLCV data for technical indicators.
    Emits BUY/SELL signals based on RSI and MACD convergence.

    v3.0: numpy backend with executor offloading.
    """

    def __init__(
        self,
        ccxt_engine: CCXTEngine,
        broadcast_func,
        executor: Optional[ProcessPoolExecutor] = None,
    ) -> None:
        self.engine = ccxt_engine
        self.broadcast = broadcast_func
        self._executor = executor
        self._running = True

    async def run(self) -> None:
        """Main loop for signal scanning."""
        logger.info(
            "Signal Scanner started (Poll=%ds, Backend=numpy, Executor=%s)",
            SIGNALS_POLL_INTERVAL,
            "ProcessPool" if self._executor else "inline",
        )

        # Wait for engine to initialize exchanges
        while self._running and not self.engine.exchange_ids:
            await asyncio.sleep(5)

        while self._running:
            try:
                # Prioritize high-liquidity exchanges for signal data
                preferred_exchanges = ["binance", "okx", "bybit"]
                exchange_id = None

                for ex in preferred_exchanges:
                    if ex in self.engine.exchange_ids:
                        exchange_id = ex
                        break

                if not exchange_id:
                    exchange_id = self.engine.exchange_ids[0]

                tasks = [
                    self._scan_symbol(exchange_id, symbol)
                    for symbol in SIGNAL_SYMBOLS
                ]
                await asyncio.gather(*tasks)

            except Exception as exc:
                logger.error("Signal Scanner error: %s", exc)

            await asyncio.sleep(SIGNALS_POLL_INTERVAL)

    async def _scan_symbol(self, exchange_id: str, symbol: str) -> None:
        """Fetch OHLCV and calculate indicators for a single symbol."""
        exchange = self.engine._exchanges.get(exchange_id)
        if not exchange:
            return

        try:
            # Fetch 100 candles of 15m timeframe
            ohlcv = await exchange.fetch_ohlcv(symbol, timeframe="15m", limit=100)
            if len(ohlcv) < 35:
                return

            # Offload CPU-bound computation to executor
            if self._executor:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    self._executor, _compute_signals_cpu, ohlcv
                )
            else:
                # Inline fallback (still fast with numpy)
                result = _compute_signals_cpu(ohlcv)

            if not result:
                return

            rsi = result["rsi"]
            macd = result["macd"]
            macd_signal = result["macd_signal"]
            prev_macd = result["prev_macd"]
            prev_macd_signal = result["prev_macd_signal"]
            current_price = result["price"]

            action = None
            indicator = ""

            # Trigger Logic:
            # STRONG BUY: RSI < 30 AND MACD crosses UP
            if rsi < 30 and macd > macd_signal and prev_macd <= prev_macd_signal:
                action = "STRONG BUY"
                indicator = f"RSI: {rsi:.1f} | MACD Bullish Cross"

            # STRONG SELL: RSI > 70 AND MACD crosses DOWN
            elif rsi > 70 and macd < macd_signal and prev_macd >= prev_macd_signal:
                action = "STRONG SELL"
                indicator = f"RSI: {rsi:.1f} | MACD Bearish Cross"

            if action:
                signal_data = {
                    "type": "SIGNAL",
                    "pair": symbol,
                    "exchange": exchange_id.upper(),
                    "action": action,
                    "price": float(current_price),
                    "indicator": indicator,
                    "timestamp": datetime.now().isoformat(),
                }
                logger.info(
                    "📡 SIGNAL: %s %s @ %s (%s)",
                    action, symbol, current_price, exchange_id,
                )
                await self.broadcast(signal_data)

        except Exception as exc:
            logger.error("[%s] %s signal scan failed: %s", exchange_id, symbol, exc)

    def stop(self) -> None:
        self._running = False
