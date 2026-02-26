"""
MigiArbitrage v2.0 — P2P & Spatial Arbitrage Scanner
======================================================
Monitors P2P order books for KES/USDT on Binance, OKX, and Bybit.
Calculates spatial spreads: buy USDT on spot exchange, transfer to
P2P exchange, sell for KES. Includes payment method risk tiering
and dynamic margin calculation.
"""

from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from backend.config import (
    P2P_ENABLED,
    P2P_FIAT,
    P2P_CRYPTO,
    P2P_EXCHANGES,
    P2P_POLL_INTERVAL,
    PAYMENT_METHODS,
    P2P_PAYMENT_MAP,
    EXCHANGE_FEES,
    WITHDRAWAL_FEES,
    PREFERRED_NETWORKS,
    MIN_P2P_PROFIT_KES,
    MAX_CAPITAL_USD,
)

logger = logging.getLogger("migi.p2p")


@dataclass
class P2PAdvert:
    """Single P2P advertisement from an exchange."""
    __slots__ = [
        "exchange", "side", "price", "min_amount", "max_amount",
        "available", "payment_methods", "advertiser", "completion_rate",
        "trade_count", "timestamp",
    ]
    exchange: str
    side: str           # "buy" or "sell"
    price: float        # KES per USDT
    min_amount: float   # Minimum trade amount (USDT)
    max_amount: float   # Maximum trade amount (USDT)
    available: float    # Available quantity (USDT)
    payment_methods: list[str]  # Internal payment method keys
    advertiser: str     # Advertiser nickname
    completion_rate: float  # 0.0 - 1.0
    trade_count: int
    timestamp: float


@dataclass
class P2PSpreadOpportunity:
    """Detected P2P arbitrage opportunity with risk tiering."""
    pair: str                    # e.g., "USDT/KES"
    arb_type: str = "p2p"       # Always "p2p"
    buy_exchange: str = ""      # Where to buy USDT (spot)
    sell_exchange: str = ""     # Where to sell on P2P
    buy_price_usd: float = 0.0
    sell_price_kes: float = 0.0
    volume: float = 0.0
    payment_method: str = ""    # Internal key (e.g., "mpesa")
    payment_label: str = ""     # Display label (e.g., "M-Pesa")
    risk_level: str = "low"     # "low" or "high"
    margin_pct: float = 0.0
    fee_spot: float = 0.0
    fee_withdrawal: float = 0.0
    fee_network: float = 0.0
    net_profit_kes: float = 0.0
    net_profit_usd: float = 0.0
    advertiser: str = ""
    advertiser_rate: float = 0.0
    advertiser_trades: int = 0
    transfer_time_est: str = ""  # e.g., "~5 min (TRC20)"
    preflight: Optional[dict] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "pair": self.pair,
            "arb_type": self.arb_type,
            "buy_exchange": self.buy_exchange,
            "sell_exchange": self.sell_exchange,
            "buy_price_usd": round(self.buy_price_usd, 4),
            "sell_price_kes": round(self.sell_price_kes, 2),
            "ask_price": round(self.buy_price_usd, 4),   # Alias for grid compat
            "bid_price": round(self.sell_price_kes, 2),
            "volume": round(self.volume, 4),
            "base": P2P_CRYPTO,
            "quote": P2P_FIAT,
            "payment_method": self.payment_method,
            "payment_label": self.payment_label,
            "risk_level": self.risk_level,
            "margin_pct": round(self.margin_pct, 2),
            "raw_spread_pct": round(self.margin_pct, 4),  # Alias
            "fee_spot": round(self.fee_spot, 4),
            "fee_maker": round(self.fee_spot, 4),          # Alias
            "fee_taker": 0.0,
            "fee_withdrawal": round(self.fee_withdrawal, 4),
            "fee_network": round(self.fee_network, 4),
            "net_profit": round(self.net_profit_usd, 4),
            "net_profit_kes": round(self.net_profit_kes, 2),
            "advertiser": self.advertiser,
            "advertiser_rate": self.advertiser_rate,
            "advertiser_trades": self.advertiser_trades,
            "transfer_time_est": self.transfer_time_est,
            "preflight": self.preflight,
            "timestamp": self.timestamp,
        }


class P2PScanner:
    """
    Scans P2P markets for KES/USDT arbitrage opportunities.

    Strategy: Buy USDT cheaply on a spot exchange (via card/bank),
    transfer to a P2P exchange, sell for KES at a premium.

    Per-payment-method dynamic margins:
    - M-Pesa / Bank Transfer: Low risk, ~0.3-0.5% margin
    - PayPal / Skrill / Google Pay: High risk (chargebacks), 2-3% margin
    """

    def __init__(self, on_spread=None) -> None:
        self._running = True
        self.on_spread = on_spread
        self._scan_count = 0
        self._opportunity_count = 0

    async def run(self) -> None:
        """Main P2P scan loop."""
        if not P2P_ENABLED:
            logger.info("P2P scanner disabled")
            return

        logger.info(
            "P2P scanner started — monitoring %s/%s on %s (interval=%.0fs)",
            P2P_CRYPTO, P2P_FIAT,
            ", ".join(P2P_EXCHANGES),
            P2P_POLL_INTERVAL,
        )

        while self._running:
            try:
                await self._scan_p2p()
                self._scan_count += 1
            except Exception as exc:
                logger.error("P2P scan error: %s", exc, exc_info=True)

            await asyncio.sleep(P2P_POLL_INTERVAL)

    async def _scan_p2p(self) -> None:
        """Fetch P2P ads from all exchanges and evaluate spreads."""
        tasks = [self._fetch_ads(ex) for ex in P2P_EXCHANGES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_sell_ads: list[P2PAdvert] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning("P2P fetch failed for %s: %s", P2P_EXCHANGES[i], result)
                continue
            all_sell_ads.extend(result)

        if not all_sell_ads:
            return

        # For each sell ad, calculate if buying USDT on spot + selling P2P is profitable
        for ad in all_sell_ads:
            if ad.side != "sell":
                continue
            for pm_key in ad.payment_methods:
                pm_cfg = PAYMENT_METHODS.get(pm_key)
                if not pm_cfg:
                    continue
                await self._evaluate_p2p_spread(ad, pm_key, pm_cfg)

    async def _evaluate_p2p_spread(
        self, ad: P2PAdvert, pm_key: str, pm_cfg: dict
    ) -> None:
        """
        Evaluate a single P2P spread opportunity.
        We buy USDT at ~$1 on spot → transfer → sell on P2P for KES.
        """
        usdt_buy_price = 1.0  # USDT is roughly $1 on spot
        sell_price_kes = ad.price  # KES per USDT on P2P

        volume = min(ad.available, ad.max_amount, MAX_CAPITAL_USD)  # Cap by MAX_CAPITAL_USD
        if volume < max(ad.min_amount, 10):
            return

        # ── Fees ──
        fee_spot = volume * 0.001  # Taker fee buying USDT on spot

        network = PREFERRED_NETWORKS.get("USDT", "TRC20")
        fee_withdrawal_units = WITHDRAWAL_FEES.get("USDT", {}).get(network, 1.0)
        fee_withdrawal = fee_withdrawal_units  # In USDT

        # Margin/risk fee for this payment method
        risk_margin = pm_cfg["margin_pct"] / 100.0
        fee_risk = volume * risk_margin

        # ── Net profit in KES ──
        net_usdt = volume - fee_spot - fee_withdrawal - fee_risk
        revenue_kes = net_usdt * sell_price_kes
        cost_kes = volume * sell_price_kes  # What we "would" get at face value
        # True cost: USDT bought at $1 * KES_rate - fees
        cost_usd = volume * usdt_buy_price + fee_spot + fee_withdrawal
        net_profit_kes = revenue_kes - (cost_usd * sell_price_kes)
        net_profit_usd = net_profit_kes / sell_price_kes if sell_price_kes > 0 else 0

        if net_profit_kes < MIN_P2P_PROFIT_KES:
            return

        margin_pct = (net_profit_kes / (cost_usd * sell_price_kes)) * 100 if cost_usd > 0 else 0

        # Transfer time estimate
        transfer_times = {"TRC20": "~3 min", "ARB": "~1 min", "SOL": "~30 sec", "OP": "~2 min"}
        transfer_time = transfer_times.get(network, "~5 min")

        opportunity = P2PSpreadOpportunity(
            pair=f"{P2P_CRYPTO}/{P2P_FIAT}",
            buy_exchange="spot",
            sell_exchange=ad.exchange,
            buy_price_usd=usdt_buy_price,
            sell_price_kes=sell_price_kes,
            volume=volume,
            payment_method=pm_key,
            payment_label=pm_cfg["label"],
            risk_level=pm_cfg["risk_level"],
            margin_pct=margin_pct,
            fee_spot=fee_spot,
            fee_withdrawal=fee_withdrawal,
            fee_network=0.0,
            net_profit_kes=net_profit_kes,
            net_profit_usd=net_profit_usd,
            advertiser=ad.advertiser,
            advertiser_rate=ad.completion_rate,
            advertiser_trades=ad.trade_count,
            transfer_time_est=f"{transfer_time} ({network})",
            timestamp=time.time(),
        )

        logger.info(
            "💱 P2P spread: %s %s | Sell %s@KES%.2f | %s | "
            "Risk=%s | Net=KES%.0f ($%.2f)",
            P2P_CRYPTO, pm_cfg["label"], ad.exchange, ad.price,
            f"Vol={volume:.0f}", pm_cfg["risk_level"],
            net_profit_kes, net_profit_usd,
        )

        if self.on_spread:
            await self.on_spread(opportunity.to_dict())

        self._opportunity_count += 1

    async def _fetch_ads(self, exchange: str) -> list[P2PAdvert]:
        """Fetch P2P sell ads from exchange REST API."""
        if exchange == "binance":
            return await self._fetch_binance_p2p()
        elif exchange == "okx":
            return await self._fetch_okx_p2p()
        elif exchange == "bybit":
            return await self._fetch_bybit_p2p()
        return []

    async def _fetch_binance_p2p(self) -> list[P2PAdvert]:
        """Fetch Binance P2P ads for KES/USDT."""
        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        payload = {
            "fiat": P2P_FIAT,
            "page": 1,
            "rows": 20,
            "tradeType": "SELL",  # We want to find people selling USDT for KES
            "asset": P2P_CRYPTO,
            "publisherType": None,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    ads = data.get("data", [])
                    return self._parse_binance_ads(ads)
        except Exception as exc:
            logger.error("[binance-p2p] Fetch failed: %s", exc)
            return []

    def _parse_binance_ads(self, ads: list) -> list[P2PAdvert]:
        """Parse Binance P2P response into P2PAdvert list."""
        result = []
        for item in ads:
            adv = item.get("adv", {})
            advertiser = item.get("advertiser", {})

            # Map payment methods
            trade_methods = adv.get("tradeMethods", [])
            payment_keys = []
            for tm in trade_methods:
                pm_id = tm.get("identifier", "") or tm.get("tradeMethodName", "")
                mapped = P2P_PAYMENT_MAP.get("binance", {}).get(pm_id)
                if mapped:
                    payment_keys.append(mapped)

            if not payment_keys:
                continue

            result.append(P2PAdvert(
                exchange="binance",
                side="sell",
                price=float(adv.get("price", 0)),
                min_amount=float(adv.get("minSingleTransAmount", 0)),
                max_amount=float(adv.get("maxSingleTransAmount", 0)),
                available=float(adv.get("surplusAmount", 0)),
                payment_methods=payment_keys,
                advertiser=advertiser.get("nickName", "Unknown"),
                completion_rate=float(advertiser.get("monthFinishRate", 0)),
                trade_count=int(advertiser.get("monthOrderCount", 0)),
                timestamp=time.time(),
            ))
        return result

    async def _fetch_okx_p2p(self) -> list[P2PAdvert]:
        """Fetch OKX P2P ads. Uses OKX C2C API."""
        url = "https://www.okx.com/v3/c2c/tradingOrders/books"
        params = {
            "quoteCurrency": P2P_FIAT.lower(),
            "baseCurrency": P2P_CRYPTO.lower(),
            "side": "sell",
            "paymentMethod": "all",
            "userType": "all",
            "showTrade": "false",
            "showFollow": "false",
            "showAlreadyTraded": "false",
            "isAbleFilter": "false",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    return self._parse_okx_ads(data.get("data", {}).get("sell", []))
        except Exception as exc:
            logger.error("[okx-p2p] Fetch failed: %s", exc)
            return []

    def _parse_okx_ads(self, ads: list) -> list[P2PAdvert]:
        """Parse OKX P2P response."""
        result = []
        for item in ads:
            payment_keys = []
            for pm in item.get("paymentMethods", []):
                mapped = P2P_PAYMENT_MAP.get("okx", {}).get(pm)
                if mapped:
                    payment_keys.append(mapped)

            if not payment_keys:
                continue

            result.append(P2PAdvert(
                exchange="okx",
                side="sell",
                price=float(item.get("price", 0)),
                min_amount=float(item.get("quoteMinAmountPerOrder", 0)),
                max_amount=float(item.get("quoteMaxAmountPerOrder", 0)),
                available=float(item.get("availableAmount", 0)),
                payment_methods=payment_keys,
                advertiser=item.get("nickName", "Unknown"),
                completion_rate=float(item.get("completedRate", 0)),
                trade_count=int(item.get("completedOrderQuantity", 0)),
                timestamp=time.time(),
            ))
        return result

    async def _fetch_bybit_p2p(self) -> list[P2PAdvert]:
        """Fetch Bybit P2P ads."""
        url = "https://api2.bybit.com/fiat/otc/item/online"
        payload = {
            "tokenId": P2P_CRYPTO,
            "currencyId": P2P_FIAT,
            "side": "1",  # 1 = sell
            "size": "20",
            "page": "1",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    return self._parse_bybit_ads(data.get("result", {}).get("items", []))
        except Exception as exc:
            logger.error("[bybit-p2p] Fetch failed: %s", exc)
            return []

    def _parse_bybit_ads(self, ads: list) -> list[P2PAdvert]:
        """Parse Bybit P2P response."""
        result = []
        for item in ads:
            payment_keys = []
            for pm in item.get("payments", []):
                pm_name = pm if isinstance(pm, str) else pm.get("paymentName", "")
                mapped = P2P_PAYMENT_MAP.get("bybit", {}).get(pm_name)
                if mapped:
                    payment_keys.append(mapped)

            if not payment_keys:
                continue

            result.append(P2PAdvert(
                exchange="bybit",
                side="sell",
                price=float(item.get("price", 0)),
                min_amount=float(item.get("minAmount", 0)),
                max_amount=float(item.get("maxAmount", 0)),
                available=float(item.get("quantity", 0)),
                payment_methods=payment_keys,
                advertiser=item.get("nickName", "Unknown"),
                completion_rate=float(item.get("recentExecuteRate", 0)),
                trade_count=int(item.get("recentOrderNum", 0)),
                timestamp=time.time(),
            ))
        return result

    def stop(self) -> None:
        self._running = False

    @property
    def stats(self) -> dict:
        return {
            "p2p_scans": self._scan_count,
            "p2p_opportunities": self._opportunity_count,
        }
