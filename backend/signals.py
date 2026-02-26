"""
MigiArbitrage v2.0 — Predictive Signals Module
==============================================
Parallel scanner that uses technical analysis (RSI, MACD) to generate
trading signals on major pairs. Processes 15m OHLCV data.
"""

import asyncio
import logging
import pandas as pd
import pandas_ta as ta
from datetime import datetime
from typing import Optional

from backend.config import SIGNAL_SYMBOLS, SIGNALS_POLL_INTERVAL
from backend.ccxt_engine import CCXTEngine

logger = logging.getLogger("migi.signals")

class SignalScanner:
    """
    Scans historical OHLCV data for technical indicators.
    Emits BUY/SELL signals based on RSI and MACD convergence.
    """

    def __init__(self, ccxt_engine: CCXTEngine, broadcast_func) -> None:
        self.engine = ccxt_engine
        self.broadcast = broadcast_func
        self._running = True

    async def run(self) -> None:
        """Main loop for signal scanning."""
        logger.info("Signal Scanner started (Poll Interval: %ds)", SIGNALS_POLL_INTERVAL)
        
        # Wait for engine to initialize exchanges
        while self._running and not self.engine.exchange_ids:
            await asyncio.sleep(5)

        while self._running:
            try:
                # We prioritize Binance or OKX for signal data (highest liquidity)
                preferred_exchanges = ["binance", "okx", "bybit"]
                exchange_id = None
                
                for ex in preferred_exchanges:
                    if ex in self.engine.exchange_ids:
                        exchange_id = ex
                        break
                
                if not exchange_id:
                    # Fallback to first available exchange
                    exchange_id = self.engine.exchange_ids[0]

                tasks = [self._scan_symbol(exchange_id, symbol) for symbol in SIGNAL_SYMBOLS]
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
            if len(ohlcv) < 35:  # Need enough data for MACD/RSI
                return

            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            
            # Calculate RSI (14)
            df.ta.rsi(length=14, append=True)
            
            # Calculate MACD (12, 26, 9)
            df.ta.macd(fast=12, slow=26, signal=9, append=True)
            
            # Latest values
            last_row = df.iloc[-1]
            prev_row = df.iloc[-2]
            
            rsi = last_row["RSI_14"]
            macd = last_row["MACD_12_26_9"]
            macd_signal = last_row["MACDs_12_26_9"]
            
            prev_macd = prev_row["MACD_12_26_9"]
            prev_macd_signal = prev_row["MACDs_12_26_9"]
            
            action = None
            indicator = ""
            current_price = last_row["close"]

            # Trigger Logic:
            # STRONG BUY: RSI < 30 AND MACD Crosses UP (MACD > Signal and prev MACD <= prev Signal)
            if rsi < 30 and macd > macd_signal and prev_macd <= prev_macd_signal:
                action = "STRONG BUY"
                indicator = f"RSI: {rsi:.1f} | MACD Bullish Cross"
            
            # STRONG SELL: RSI > 70 AND MACD Crosses DOWN (MACD < Signal and prev MACD >= prev Signal)
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
                    "timestamp": datetime.now().isoformat()
                }
                logger.info("📡 SIGNAL: %s %s @ %s (%s)", action, symbol, current_price, exchange_id)
                await self.broadcast(signal_data)

        except Exception as exc:
            logger.error("[%s] %s signal scan failed: %s", exchange_id, symbol, exc)

    def stop(self) -> None:
        self._running = False
