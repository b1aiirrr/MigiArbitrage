"""Exchange client modules for MigiArbitrage.

NOTE: The individual legacy client implementations (binance, kraken, kucoin)
have been superseded by the unified ccxt.pro engine (ccxt_engine.py).
They are kept for reference only and are NOT auto-imported to avoid
pulling in stale config dependencies. Use ccxt_engine instead.
"""
from .base import ExchangeClient, WalletStatus

__all__ = ["ExchangeClient", "WalletStatus"]
