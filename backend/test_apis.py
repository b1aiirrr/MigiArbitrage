"""
MigiArbitrage — API Connectivity & Balance Diagnostic Tool
Tests configured exchange API keys (Binance, MEXC, OKX) and reports balances.
"""

import os
import sys
from dotenv import load_dotenv

# Ensure backend directory is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

import ccxt

def test_binance():
    print("\n" + "="*50)
    print("Testing Binance API...")
    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    secret = os.getenv("BINANCE_API_SECRET", "").strip()
    
    if not api_key or not secret:
        print("❌ Binance: Missing API Key or Secret in .env")
        return False

    try:
        exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
                "adjustForTimeDifference": True,
                "recvWindow": 60000,
            },
        })
        balance = exchange.fetch_balance()
        non_zero = {k: v for k, v in balance.get("total", {}).items() if v and v > 0}
        print("✅ Binance Connection SUCCESSFUL!")
        print(f"   Non-zero balances: {non_zero if non_zero else '0 (No funds in spot wallet)'}")
        return True
    except Exception as e:
        print(f"❌ Binance Connection FAILED: {e}")
        return False

def test_mexc():
    print("\n" + "="*50)
    print("Testing MEXC API...")
    api_key = os.getenv("MEXC_API_KEY", "").strip()
    secret = os.getenv("MEXC_API_SECRET", "").strip()
    
    if not api_key or not secret:
        print("❌ MEXC: Missing API Key or Secret in .env")
        return False

    try:
        exchange = ccxt.mexc({
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        balance = exchange.fetch_balance()
        non_zero = {k: v for k, v in balance.get("total", {}).items() if v and v > 0}
        print("✅ MEXC Connection SUCCESSFUL!")
        print(f"   Non-zero balances: {non_zero if non_zero else '0 (No funds in spot wallet)'}")
        return True
    except Exception as e:
        print(f"❌ MEXC Connection FAILED: {e}")
        return False

def test_okx():
    print("\n" + "="*50)
    print("Testing OKX API...")
    api_key = os.getenv("OKX_API_KEY", "").strip()
    secret = os.getenv("OKX_API_SECRET", "").strip()
    passphrase = os.getenv("OKX_API_PASSPHRASE", "").strip()
    
    if not api_key or not secret:
        print("❌ OKX: Missing API Key or Secret in .env")
        return False

    if not passphrase:
        print("⚠️ OKX: Passphrase (password) is NOT set. OKX requires apiKey + secret + passphrase.")
        print("   Please provide the passphrase you entered when creating the OKX API key.")
        return False

    try:
        exchange = ccxt.okx({
            "apiKey": api_key,
            "secret": secret,
            "password": passphrase,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        balance = exchange.fetch_balance()
        non_zero = {k: v for k, v in balance.get("total", {}).items() if v and v > 0}
        print("✅ OKX Connection SUCCESSFUL!")
        print(f"   Non-zero balances: {non_zero if non_zero else '0 (No funds in spot wallet)'}")
        return True
    except Exception as e:
        print(f"❌ OKX Connection FAILED: {e}")
        return False

if __name__ == "__main__":
    print("MigiArbitrage API Pre-Flight Connection Check")
    binance_ok = test_binance()
    mexc_ok = test_mexc()
    okx_ok = test_okx()
    print("\n" + "="*50)
    print("SUMMARY:")
    print(f"Binance: {'✅ READY' if binance_ok else '❌ NOT READY'}")
    print(f"MEXC:    {'✅ READY' if mexc_ok else '❌ NOT READY'}")
    print(f"OKX:     {'✅ READY' if okx_ok else '❌ NEEDS PASSPHRASE / FAILED'}")
    print("="*50)
