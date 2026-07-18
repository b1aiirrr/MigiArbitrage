import ccxt.async_support as ccxt
import asyncio
import logging
from typing import Dict, Any, Optional

# Configure robust logging for the execution engine
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ExecutionEngine")

async def get_exchange_instance(exchange_name: str, api_key: str = None, secret: str = None) -> ccxt.Exchange:
    """Initialize an async CCXT exchange instance."""
    # Note: Deriv might not be natively supported for crypto spot trading in standard CCXT in the same way as Binance/Bybit. 
    # If Deriv is custom or requires a specific API, it might need a custom ccxt subclass.
    exchange_class = getattr(ccxt, exchange_name.lower(), None)
    if not exchange_class:
        raise ValueError(f"Exchange '{exchange_name}' is not supported by CCXT")
    
    config = {
        'enableRateLimit': True,
    }
    if api_key and secret:
        config['apiKey'] = api_key
        config['secret'] = secret
        
    return exchange_class(config)

async def fetch_taker_fee(exchange: ccxt.Exchange, symbol: str) -> float:
    """
    Dynamically fetch taker fee for the given symbol.
    Market orders are always charged the Taker fee.
    """
    try:
        await exchange.load_markets()
        market = exchange.market(symbol)
        if 'taker' in market and market['taker'] is not None:
            return market['taker']
        
        # Fallback to fetching trading fees if not explicitly in the market info
        if exchange.has['fetchTradingFee']:
            fee_info = await exchange.fetch_trading_fee(symbol)
            if 'taker' in fee_info and fee_info['taker'] is not None:
                return fee_info['taker']
                
        # Default fallback if API doesn't provide fee endpoint (e.g. 0.1%)
        logger.warning(f"Could not dynamically fetch fee for {symbol} on {exchange.id}. Using default 0.1% (0.001)")
        return 0.001 
    except Exception as e:
        logger.warning(f"Failed to fetch fees for {symbol} on {exchange.id}: {e}. Using default 0.1% (0.001)")
        return 0.001

async def execute_market_order(exchange: ccxt.Exchange, symbol: str, side: str, amount: float) -> Optional[Dict]:
    """Execute a market order on the exchange with strict error handling."""
    try:
        logger.info(f"Executing {side.upper()} order for {amount} {symbol} on {exchange.id}...")
        order = await exchange.create_order(symbol, 'market', side, amount)
        logger.info(f"Order executed successfully on {exchange.id}: ID {order.get('id')}")
        return order
        
    except ccxt.InsufficientFunds as e:
        logger.critical(f"Insufficient funds on {exchange.id} for {side.upper()} {amount} {symbol}: {e}")
        raise
    except ccxt.NetworkError as e:
        logger.critical(f"Network timeout/error on {exchange.id}: {e}")
        raise
    except ccxt.RateLimitExceeded as e:
        logger.critical(f"API Rate limit exceeded on {exchange.id}: {e}")
        raise
    except Exception as e:
        logger.critical(f"Unexpected error executing {side} on {exchange.id}: {e}")
        raise

async def execute_arbitrage_trade(signal_data: Dict[str, Any]) -> bool:
    """
    Executes a dual-balance (inventory) arbitrage trade simultaneously.
    
    Expected `signal_data` format:
    {
        'symbol': 'BTC/USDT',
        'buy_exchange': 'binance',
        'sell_exchange': 'bybit',
        'amount': 0.01,
        'buy_price': 50000.0,
        'sell_price': 50015.0,
        'buy_exchange_api_key': '...',
        'buy_exchange_secret': '...',
        'sell_exchange_api_key': '...',
        'sell_exchange_secret': '...'
    }
    """
    symbol = signal_data['symbol']
    amount = float(signal_data['amount'])
    buy_price = float(signal_data.get('buy_price', 0.0))
    sell_price = float(signal_data.get('sell_price', 0.0))
    
    buy_ex_name = signal_data['buy_exchange']
    sell_ex_name = signal_data['sell_exchange']
    
    # Calculate gross spread
    gross_spread = (sell_price - buy_price) * amount
    
    buy_exchange = None
    sell_exchange = None
    
    try:
        # Initialize Exchanges
        buy_exchange = await get_exchange_instance(
            buy_ex_name, 
            signal_data.get('buy_exchange_api_key'), 
            signal_data.get('buy_exchange_secret')
        )
        sell_exchange = await get_exchange_instance(
            sell_ex_name, 
            signal_data.get('sell_exchange_api_key'), 
            signal_data.get('sell_exchange_secret')
        )
        
        # 1. Dynamically Fetch Fees Asynchronously
        buy_fee_task = fetch_taker_fee(buy_exchange, symbol)
        sell_fee_task = fetch_taker_fee(sell_exchange, symbol)
        buy_fee_rate, sell_fee_rate = await asyncio.gather(buy_fee_task, sell_fee_task)
        
        # Calculate absolute fee costs in quote currency
        buy_fee_cost = buy_price * amount * buy_fee_rate
        sell_fee_cost = sell_price * amount * sell_fee_rate
        total_fees = buy_fee_cost + sell_fee_cost
        
        logger.info(f"Gross Spread: {gross_spread:.4f}")
        logger.info(f"Total Estimated Fees: {total_fees:.4f} (Buy: {buy_fee_cost:.4f}, Sell: {sell_fee_cost:.4f})")
        
        # 2. Check profitability & abort if fees exceed spread
        if total_fees >= gross_spread:
            logger.warning(f"Arbitrage aborted. Total Fees ({total_fees:.4f}) exceed or negate the gross spread ({gross_spread:.4f}).")
            return False
            
        net_profit = gross_spread - total_fees
        logger.info(f"Spread is profitable! Net expected profit: {net_profit:.4f}")
        
        # 3. Check Dual-Balance Inventory (Simultaneously)
        buy_bal_task = buy_exchange.fetch_balance()
        sell_bal_task = sell_exchange.fetch_balance()
        
        try:
            buy_balance, sell_balance = await asyncio.gather(buy_bal_task, sell_bal_task)
        except Exception as e:
            logger.critical(f"Failed to fetch balances. Aborting trade. Error: {e}")
            return False
            
        base_currency, quote_currency = symbol.split('/')
        
        # Verify enough Quote currency to BUY
        if buy_balance.get(quote_currency, {}).get('free', 0) < (buy_price * amount):
            logger.critical(f"Insufficient {quote_currency} on {buy_ex_name} to buy {amount} {symbol}")
            return False
            
        # Verify enough Base currency to SELL
        if sell_balance.get(base_currency, {}).get('free', 0) < amount:
            logger.critical(f"Insufficient {base_currency} on {sell_ex_name} to sell {amount} {symbol}")
            return False
            
        # 4. Simultaneous Market Execution
        logger.info(f"Executing simultaneous BUY on {buy_ex_name} and SELL on {sell_ex_name} for {amount} {symbol}...")
        buy_task = execute_market_order(buy_exchange, symbol, 'buy', amount)
        sell_task = execute_market_order(sell_exchange, symbol, 'sell', amount)
        
        # Return exceptions instead of raising them immediately to know if one leg succeeded while the other failed
        results = await asyncio.gather(buy_task, sell_task, return_exceptions=True)
        
        buy_result, sell_result = results
        success = True
        
        if isinstance(buy_result, Exception):
            logger.critical(f"CRITICAL: BUY order failed on {buy_ex_name}: {buy_result}")
            success = False
            
        if isinstance(sell_result, Exception):
            logger.critical(f"CRITICAL: SELL order failed on {sell_ex_name}: {sell_result}")
            success = False
            
        if success:
            logger.info("Arbitrage trade executed successfully on both exchanges! Spread captured.")
        else:
            logger.critical("ALERT: One or more legs of the arbitrage failed! Your inventory is now UNBALANCED and requires manual intervention or a rebalancing script.")
            
        return success

    except Exception as e:
        logger.error(f"Unexpected error in arbitrage execution engine: {e}", exc_info=True)
        return False
        
    finally:
        # 5. Cleanup resources
        if buy_exchange:
            await buy_exchange.close()
        if sell_exchange:
            await sell_exchange.close()

# -------------------------------------------------------------
# Example Usage for signals.py:
# -------------------------------------------------------------
# from execution import execute_arbitrage_trade
# import asyncio
#
# signal = {
#     'symbol': 'BTC/USDT',
#     'buy_exchange': 'binance',
#     'sell_exchange': 'bybit',
#     'amount': 0.01,
#     'buy_price': 65000.0,
#     'sell_price': 65050.0,
#     'buy_exchange_api_key': '...',
#     'buy_exchange_secret': '...',
#     'sell_exchange_api_key': '...',
#     'sell_exchange_secret': '...'
# }
# success = asyncio.run(execute_arbitrage_trade(signal))
