"""
MigiArbitrage — Configuration Module
=====================================
Central configuration for exchange fees, monitored pairs, thresholds,
and environment variable loading. All fees are expressed as ratios (0.001 = 0.1%).
"""

import os
import gc
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
#  Memory Optimization (2 GB RAM server)
# ──────────────────────────────────────────────
gc.set_threshold(700, 10, 5)  # Aggressive GC thresholds

# ──────────────────────────────────────────────
#  Telegram Configuration
# ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_RATE_LIMIT: int = 20  # Max messages per minute

# ──────────────────────────────────────────────
#  Exchange API Keys (READ-ONLY — no trading)
# ──────────────────────────────────────────────
BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")
KRAKEN_API_KEY: str = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET: str = os.getenv("KRAKEN_API_SECRET", "")
KUCOIN_API_KEY: str = os.getenv("KUCOIN_API_KEY", "")
KUCOIN_API_SECRET: str = os.getenv("KUCOIN_API_SECRET", "")
KUCOIN_API_PASSPHRASE: str = os.getenv("KUCOIN_API_PASSPHRASE", "")

# ──────────────────────────────────────────────
#  WebSocket Server (streams to frontend)
# ──────────────────────────────────────────────
WS_SERVER_HOST: str = os.getenv("WS_SERVER_HOST", "0.0.0.0")
WS_SERVER_PORT: int = int(os.getenv("WS_SERVER_PORT", "8765"))

# ──────────────────────────────────────────────
#  Monitored Trading Pairs
#  Normalized to uppercase base-quote format
# ──────────────────────────────────────────────
MONITORED_PAIRS: list[dict] = [
    {
        "base": "BTC",
        "quote": "USDT",
        "binance": "btcusdt",
        "kraken": "XBT/USDT",
        "kucoin": "BTC-USDT",
    },
    {
        "base": "ETH",
        "quote": "USDT",
        "binance": "ethusdt",
        "kraken": "ETH/USDT",
        "kucoin": "ETH-USDT",
    },
    {
        "base": "SOL",
        "quote": "USDT",
        "binance": "solusdt",
        "kraken": "SOL/USDT",
        "kucoin": "SOL-USDT",
    },
    {
        "base": "XRP",
        "quote": "USDT",
        "binance": "xrpusdt",
        "kraken": "XRP/USDT",
        "kucoin": "XRP-USDT",
    },
    {
        "base": "ADA",
        "quote": "USDT",
        "binance": "adausdt",
        "kraken": "ADA/USDT",
        "kucoin": "ADA-USDT",
    },
    {
        "base": "DOGE",
        "quote": "USDT",
        "binance": "dogeusdt",
        "kraken": "DOGE/USDT",
        "kucoin": "DOGE-USDT",
    },
    {
        "base": "AVAX",
        "quote": "USDT",
        "binance": "avaxusdt",
        "kraken": "AVAX/USDT",
        "kucoin": "AVAX-USDT",
    },
    {
        "base": "LINK",
        "quote": "USDT",
        "binance": "linkusdt",
        "kraken": "LINK/USDT",
        "kucoin": "LINK-USDT",
    },
]

# ──────────────────────────────────────────────
#  Exchange Fee Schedules
#  All values as decimal ratios (0.001 = 0.1%)
# ──────────────────────────────────────────────
EXCHANGE_FEES: dict = {
    "binance": {
        "maker": 0.001,       # 0.10%
        "taker": 0.001,       # 0.10%
    },
    "kraken": {
        "maker": 0.0016,      # 0.16%
        "taker": 0.0026,      # 0.26%
    },
    "kucoin": {
        "maker": 0.001,       # 0.10%
        "taker": 0.001,       # 0.10%
    },
}

# ──────────────────────────────────────────────
#  Withdrawal Fees (per asset, per network)
#  These are approximate defaults; the pre-flight
#  check queries live values from exchange REST APIs.
# ──────────────────────────────────────────────
WITHDRAWAL_FEES: dict = {
    "BTC": {"BTC": 0.0005, "BEP20": 0.0000052},
    "ETH": {"ERC20": 0.005, "ARB": 0.0001, "OP": 0.0001},
    "SOL": {"SOL": 0.01},
    "XRP": {"XRP": 0.25},
    "ADA": {"ADA": 1.0},
    "DOGE": {"DOGE": 5.0},
    "AVAX": {"AVAX": 0.01, "C-Chain": 0.01},
    "LINK": {"ERC20": 0.3, "ARB": 0.01},
}

# Preferred transfer networks per asset (fastest / cheapest)
PREFERRED_NETWORKS: dict = {
    "BTC": "BTC",
    "ETH": "ARB",
    "SOL": "SOL",
    "XRP": "XRP",
    "ADA": "ADA",
    "DOGE": "DOGE",
    "AVAX": "AVAX",
    "LINK": "ARB",
}

# ──────────────────────────────────────────────
#  Scanner Thresholds
# ──────────────────────────────────────────────
MIN_NET_PROFIT_USD: float = float(os.getenv("MIN_NET_PROFIT_USD", "1.00"))
SCAN_INTERVAL_MS: int = int(os.getenv("SCAN_INTERVAL_MS", "500"))
ORDER_BOOK_DEPTH: int = 20  # Levels kept per side

# ──────────────────────────────────────────────
#  Network Congestion Thresholds (seconds)
# ──────────────────────────────────────────────
NETWORK_CONGESTION_WARN_SECS: int = 600   # 10 min avg → warning
NETWORK_CONGESTION_BLOCK_SECS: int = 1800  # 30 min avg → block alert

# ──────────────────────────────────────────────
#  Alert History Buffer
# ──────────────────────────────────────────────
ALERT_HISTORY_SIZE: int = 100  # Ring buffer for WS server
