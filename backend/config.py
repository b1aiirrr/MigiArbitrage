"""
MigiArbitrage v2.0 — Configuration Module
===========================================
Central configuration for 9+ exchanges, P2P payment methods,
triangular arbitrage, memory guardrails, and env var loading.
"""

import os
import gc
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
#  Memory Optimization (2 GB RAM server)
# ──────────────────────────────────────────────
gc.set_threshold(700, 10, 5)
MEMORY_LIMIT_MB: int = int(os.getenv("MEMORY_LIMIT_MB", "1500"))
MEMORY_CHECK_INTERVAL: float = 30.0  # seconds

# ──────────────────────────────────────────────
#  Telegram Configuration
# ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_RATE_LIMIT: int = 20

# ──────────────────────────────────────────────
#  Exchange API Keys (READ-ONLY)
# ──────────────────────────────────────────────
EXCHANGE_API_KEYS: dict = {
    "binance": {
        "apiKey": os.getenv("BINANCE_API_KEY", ""),
        "secret": os.getenv("BINANCE_API_SECRET", ""),
    },
    "okx": {
        "apiKey": os.getenv("OKX_API_KEY", ""),
        "secret": os.getenv("OKX_API_SECRET", ""),
        "password": os.getenv("OKX_API_PASSPHRASE", ""),
    },
    "kucoin": {
        "apiKey": os.getenv("KUCOIN_API_KEY", ""),
        "secret": os.getenv("KUCOIN_API_SECRET", ""),
        "password": os.getenv("KUCOIN_API_PASSPHRASE", ""),
    },
    "mexc": {
        "apiKey": os.getenv("MEXC_API_KEY", ""),
        "secret": os.getenv("MEXC_API_SECRET", ""),
    },
    "bybit": {
        "apiKey": os.getenv("BYBIT_API_KEY", ""),
        "secret": os.getenv("BYBIT_API_SECRET", ""),
    },
    "gateio": {
        "apiKey": os.getenv("GATEIO_API_KEY", ""),
        "secret": os.getenv("GATEIO_API_SECRET", ""),
    },
    "bitget": {
        "apiKey": os.getenv("BITGET_API_KEY", ""),
        "secret": os.getenv("BITGET_API_SECRET", ""),
        "password": os.getenv("BITGET_API_PASSPHRASE", ""),
    },
    "coinbase": {
        "apiKey": os.getenv("COINBASE_API_KEY", ""),
        "secret": os.getenv("COINBASE_API_SECRET", ""),
    },
    "kcex": {
        "apiKey": os.getenv("KCEX_API_KEY", ""),
        "secret": os.getenv("KCEX_API_SECRET", ""),
    },
}

# ──────────────────────────────────────────────
#  Enabled Exchanges (ccxt IDs)
# ──────────────────────────────────────────────
ENABLED_EXCHANGES: list[str] = [
    "binance", "okx", "kucoin", "mexc", "bybit",
    "gateio", "bitget", "coinbase",
    # "kcex",  # Enable when ccxt adds support or use REST fallback
]

# ──────────────────────────────────────────────
#  WebSocket Server
# ──────────────────────────────────────────────
WS_SERVER_HOST: str = os.getenv("WS_SERVER_HOST", "0.0.0.0")
WS_SERVER_PORT: int = int(os.getenv("WS_SERVER_PORT", "8765"))
WS_ALLOWED_ORIGINS: list[str] = [
    "https://migi-arbitrage.vercel.app",
    "http://localhost:3000",
    "http://localhost:3001",
]

# ──────────────────────────────────────────────
#  Monitored Spot Trading Pairs
# ──────────────────────────────────────────────
SPOT_SYMBOLS: list[str] = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT",
    "BNB/USDT", "MATIC/USDT", "DOT/USDT", "NEAR/USDT",
]

# ──────────────────────────────────────────────
#  Exchange Fee Schedules (decimal ratios)
# ──────────────────────────────────────────────
EXCHANGE_FEES: dict = {
    "binance":  {"maker": 0.0010, "taker": 0.0010},
    "okx":      {"maker": 0.0008, "taker": 0.0010},
    "kucoin":   {"maker": 0.0010, "taker": 0.0010},
    "mexc":     {"maker": 0.0000, "taker": 0.0010},
    "bybit":    {"maker": 0.0010, "taker": 0.0010},
    "gateio":   {"maker": 0.0015, "taker": 0.0015},
    "bitget":   {"maker": 0.0010, "taker": 0.0010},
    "coinbase": {"maker": 0.0040, "taker": 0.0060},
    "kcex":     {"maker": 0.0010, "taker": 0.0010},
}

# ──────────────────────────────────────────────
#  Withdrawal Fees (per asset, preferred network)
# ──────────────────────────────────────────────
WITHDRAWAL_FEES: dict = {
    "BTC":   {"BTC": 0.0005,  "BEP20": 0.0000052},
    "ETH":   {"ERC20": 0.005, "ARB": 0.0001, "OP": 0.0001},
    "SOL":   {"SOL": 0.01},
    "XRP":   {"XRP": 0.25},
    "ADA":   {"ADA": 1.0},
    "DOGE":  {"DOGE": 5.0},
    "AVAX":  {"AVAX": 0.01},
    "LINK":  {"ERC20": 0.3, "ARB": 0.01},
    "USDT":  {"TRC20": 1.0, "ARB": 0.1, "OP": 0.1, "SOL": 1.0},
    "BNB":   {"BEP20": 0.001},
    "MATIC": {"POLYGON": 0.1},
    "DOT":   {"DOT": 0.1},
    "NEAR":  {"NEAR": 0.01},
}

PREFERRED_NETWORKS: dict = {
    "BTC": "BTC", "ETH": "ARB", "SOL": "SOL", "XRP": "XRP",
    "ADA": "ADA", "DOGE": "DOGE", "AVAX": "AVAX", "LINK": "ARB",
    "USDT": "TRC20", "BNB": "BEP20", "MATIC": "POLYGON",
    "DOT": "DOT", "NEAR": "NEAR",
}

# ──────────────────────────────────────────────
#  P2P Configuration (KES/USDT)
# ──────────────────────────────────────────────
P2P_ENABLED: bool = os.getenv("P2P_ENABLED", "true").lower() == "true"
P2P_FIAT: str = "KES"
P2P_CRYPTO: str = "USDT"
P2P_EXCHANGES: list[str] = ["binance", "okx", "bybit"]
P2P_POLL_INTERVAL: float = float(os.getenv("P2P_POLL_INTERVAL", "15"))  # seconds

# ── Payment Method Configuration ─────────────
PAYMENT_METHODS: dict = {
    "mpesa": {
        "label": "M-Pesa",
        "risk_level": "low",
        "margin_pct": 0.5,           # 0.5% margin
        "details": os.getenv("MPESA_DETAILS", "0704893919"),
        "instructions": os.getenv(
            "MPESA_INSTRUCTIONS",
            "Please send payment via M-Pesa to 0704893919. Name will appear as Migi. Reply 'PAID' when done."
        ),
    },
    "bank_transfer": {
        "label": "Bank Transfer (I&M)",
        "risk_level": "low",
        "margin_pct": 0.3,           # 0.3% margin
        "details": os.getenv("BANK_DETAILS", "I&M Bank - 0704893919"),
        "instructions": os.getenv(
            "BANK_INSTRUCTIONS",
            "Please transfer to I&M Bank, Account: 0704893919. Reply 'PAID' when done."
        ),
    },
    "paypal": {
        "label": "PayPal",
        "risk_level": "high",
        "margin_pct": 3.0,           # 3% margin (chargeback risk)
        "details": os.getenv("PAYPAL_DETAILS", ""),
        "instructions": os.getenv(
            "PAYPAL_INSTRUCTIONS",
            "Please send payment via PayPal to {details}. Use 'Friends & Family'. Reply 'PAID' when done."
        ),
    },
    "skrill": {
        "label": "Skrill",
        "risk_level": "high",
        "margin_pct": 2.5,           # 2.5% margin
        "details": os.getenv("SKRILL_DETAILS", ""),
        "instructions": os.getenv(
            "SKRILL_INSTRUCTIONS",
            "Please send payment via Skrill to {details}. Reply 'PAID' when done."
        ),
    },
    "google_pay": {
        "label": "Google Pay",
        "risk_level": "high",
        "margin_pct": 2.0,           # 2% margin
        "details": os.getenv("GPAY_DETAILS", ""),
        "instructions": os.getenv(
            "GPAY_INSTRUCTIONS",
            "Please send payment via Google Pay to {details}. Reply 'PAID' when done."
        ),
    },
}

# ── P2P Payment Method Mapping per Exchange ──
# Maps exchange P2P payment method identifiers to our internal keys
P2P_PAYMENT_MAP: dict = {
    "binance": {
        "MPESA": "mpesa", "MPesa": "mpesa", "M-Pesa": "mpesa",
        "BANK": "bank_transfer", "BankTransfer": "bank_transfer",
        "PayPal": "paypal", "Paypal": "paypal",
        "Skrill": "skrill",
        "GooglePay": "google_pay",
    },
    "okx": {
        "M-Pesa": "mpesa", "Mpesa": "mpesa",
        "Bank Transfer": "bank_transfer", "Bank transfer": "bank_transfer",
        "PayPal": "paypal",
        "Skrill": "skrill",
        "Google Pay": "google_pay",
    },
    "bybit": {
        "M-PESA": "mpesa", "M-Pesa": "mpesa",
        "Bank Transfer": "bank_transfer",
        "PayPal": "paypal",
        "Skrill": "skrill",
        "Google Pay": "google_pay",
    },
}

# ──────────────────────────────────────────────
#  Triangular Arbitrage Configuration
# ──────────────────────────────────────────────
TRIANGULAR_ENABLED: bool = os.getenv("TRIANGULAR_ENABLED", "true").lower() == "true"
TRIANGULAR_EXCHANGES: list[str] = ["binance", "okx", "kucoin"]
TRIANGULAR_PATHS: list[list[str]] = [
    # Each path: [start_sym, mid_sym, end_sym]
    # KES → USDT → BTC → KES
    ["KES/USDT", "BTC/USDT", "BTC/KES"],
    # USDT → BTC → ETH → USDT
    ["BTC/USDT", "ETH/BTC", "ETH/USDT"],
    # USDT → ETH → SOL → USDT (via intermediate)
    ["ETH/USDT", "SOL/ETH", "SOL/USDT"],
]
TRIANGULAR_POLL_INTERVAL: float = float(os.getenv("TRIANGULAR_POLL_INTERVAL", "2"))

# ──────────────────────────────────────────────
#  Scanner Thresholds
# ──────────────────────────────────────────────
MIN_NET_PROFIT_USD: float = float(os.getenv("MIN_NET_PROFIT_USD", "1.00"))
MIN_P2P_PROFIT_KES: float = float(os.getenv("MIN_P2P_PROFIT_KES", "100"))
MAX_CAPITAL_USD: float = float(os.getenv("MAX_CAPITAL_USD", "100"))
SCAN_INTERVAL_MS: int = int(os.getenv("SCAN_INTERVAL_MS", "500"))
ORDER_BOOK_DEPTH: int = 20

# ──────────────────────────────────────────────
# Signals Configuration
# ──────────────────────────────────────────────
SIGNALS_ENABLED: bool = os.getenv("SIGNALS_ENABLED", "True").lower() == "true"
SIGNALS_POLL_INTERVAL: int = int(os.getenv("SIGNALS_POLL_INTERVAL", "60"))
SIGNAL_SYMBOLS: list[str] = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]

# ──────────────────────────────────────────────
#  Network Congestion Thresholds (seconds)
# ──────────────────────────────────────────────
NETWORK_CONGESTION_WARN_SECS: int = 600
NETWORK_CONGESTION_BLOCK_SECS: int = 1800

# ──────────────────────────────────────────────
#  Alert History Buffer
# ──────────────────────────────────────────────
ALERT_HISTORY_SIZE: int = 200
