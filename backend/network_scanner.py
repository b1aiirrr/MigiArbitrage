"""
MigiArbitrage v3.0 — Network Viability Scanner
==============================================
Production-ready async utility to query CCXT exchanges for all
underlying blockchain networks for a specific base asset.

Extracts:
- Network ID
- Deposit viability (deposit_enabled)
- Withdrawal viability (withdraw_enabled)
- Fixed network cost (withdraw_fee)

Used to detect "Ghost Spreads" (where one exchange has paused deposits
or withdrawals) and to find optimal Layer-2 routing paths.
"""

import asyncio
import logging
import sys
from typing import Any, Dict

import ccxt.async_support as ccxt
import orjson

# Configure robust logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("NetworkScanner")


def _parse_network_info(net_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Safely parse the CCXT network dictionary into a structured format.
    Handles exchange-specific naming inconsistencies.
    """
    # Extract booleans, defaulting to True if missing but the network object exists
    deposit = net_info.get("deposit", net_info.get("depositEnable", True))
    withdraw = net_info.get("withdraw", net_info.get("withdrawEnable", True))
    
    # Extract fee, handling None or missing keys gracefully
    fee_raw = net_info.get("fee", 0.0)
    try:
        fee = float(fee_raw) if fee_raw is not None else 0.0
    except (ValueError, TypeError):
        fee = 0.0

    return {
        "deposit_enabled": bool(deposit),
        "withdraw_enabled": bool(withdraw),
        "withdraw_fee": fee
    }


async def fetch_pair_network_status(exchange_id: str, symbol: str) -> Dict[str, Any]:
    """
    Query an exchange for all available blockchain networks for the base asset of a trading pair.
    
    Implements a unified fallback extraction architecture:
    1. Primary: exchange.fetch_currencies()
    2. Secondary: exchange.load_markets() -> exchange.currencies property cache
    3. Tertiary: exchange.fetch_deposit_withdraw_fees()
    
    Args:
        exchange_id: CCXT exchange ID (e.g., 'binance', 'mexc', 'bybit')
        symbol: Trading pair (e.g., 'ETH/USDT')
        
    Returns:
        Structured JSON-like dictionary with network configurations.
    """
    if "/" not in symbol:
        return {"error": f"Invalid symbol format '{symbol}'. Expected 'BASE/QUOTE'."}
    
    base_asset = symbol.split("/")[0].upper()
    exchange_id = exchange_id.lower()
    
    exchange_class = getattr(ccxt, exchange_id, None)
    if not exchange_class:
        return {"error": f"Exchange '{exchange_id}' is not supported by CCXT."}
        
    # Instantiate with rate limits enabled
    exchange = exchange_class({"enableRateLimit": True})
    
    result = {
        "exchange": exchange_id,
        "symbol": symbol,
        "base_asset": base_asset,
        "networks": {}
    }
    
    try:
        # ── Primary: Query fetch_currencies() ──
        if exchange.has.get("fetchCurrencies", False):
            try:
                currencies = await exchange.fetch_currencies()
                currency_info = currencies.get(base_asset, {})
                
                if "networks" in currency_info and currency_info["networks"]:
                    for net_id, net_info in currency_info["networks"].items():
                        result["networks"][net_id.upper()] = _parse_network_info(net_info)
                    return result
            except ccxt.RateLimitExceeded:
                logger.warning(f"[{exchange_id}] Rate limit exceeded on fetchCurrencies")
            except (ccxt.NetworkError, ccxt.ExchangeError) as e:
                logger.warning(f"[{exchange_id}] Primary fetchCurrencies failed: {e}")

        # ── Secondary Fallback: load_markets() and currencies cache ──
        try:
            await exchange.load_markets()
            currencies_cache = getattr(exchange, "currencies", {})
            currency_info = currencies_cache.get(base_asset, {})
            
            if "networks" in currency_info and currency_info["networks"]:
                for net_id, net_info in currency_info["networks"].items():
                    result["networks"][net_id.upper()] = _parse_network_info(net_info)
                return result
        except Exception as e:
            logger.warning(f"[{exchange_id}] Secondary load_markets fallback failed: {e}")

        # ── Tertiary Fallback: fetch_deposit_withdraw_fees() ──
        if exchange.has.get("fetchDepositWithdrawFees", False):
            try:
                fees = await exchange.fetch_deposit_withdraw_fees([base_asset])
                asset_fees = fees.get(base_asset, {})
                if "networks" in asset_fees and asset_fees["networks"]:
                    for net_id, net_info in asset_fees["networks"].items():
                        result["networks"][net_id.upper()] = _parse_network_info(net_info)
                    return result
            except Exception as e:
                logger.warning(f"[{exchange_id}] Tertiary fetchDepositWithdrawFees failed: {e}")

        # ── Ultimate Fallback: Currency-level info (no specific network map) ──
        if currency_info:
            logger.info(f"[{exchange_id}] No specific network dict found, using currency-level fallback.")
            result["networks"]["DEFAULT"] = {
                "deposit_enabled": bool(currency_info.get("deposit", True)),
                "withdraw_enabled": bool(currency_info.get("withdraw", True)),
                "withdraw_fee": float(currency_info.get("fee", 0.0) or 0.0)
            }
            return result
            
        result["error"] = f"No network data discovered for {base_asset} on {exchange_id}."
        return result

    except Exception as e:
        logger.error(f"[{exchange_id}] Critical error querying network status: {e}")
        result["error"] = str(e)
        return result
    finally:
        await exchange.close()


# ── Standalone Execution Example ──
async def main():
    if len(sys.argv) == 3:
        target_exchange = sys.argv[1]
        target_symbol = sys.argv[2]
    else:
        target_exchange = "mexc"
        target_symbol = "ETH/USDT"
        
    logger.info(f"Querying network parameters for {target_symbol} on {target_exchange}...")
    
    status = await fetch_pair_network_status(target_exchange, target_symbol)
    
    # Output cleanly formatted JSON
    print("\n" + orjson.dumps(status, option=orjson.OPT_INDENT_2).decode("utf-8"))


if __name__ == "__main__":
    # Graceful exit handling for Windows/Linux
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
