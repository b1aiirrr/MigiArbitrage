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

from config import (
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
        """Fetch P2P ads from all exchanges and evaluate real bid/ask spreads."""
        tasks = [self._fetch_ads(ex) for ex in P2P_EXCHANGES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_ads: list[P2PAdvert] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning("P2P fetch failed for %s: %s", P2P_EXCHANGES[i], result)
                continue
            all_ads.extend(result)

        if not all_ads:
            return

        # side == "sell" → advertiser sells USDT → we BUY USDT (ask)
        # side == "buy"  → advertiser buys USDT  → we SELL USDT (bid)
        ask_ads = [a for a in all_ads if a.side == "sell"]
        bid_ads = [a for a in all_ads if a.side == "buy"]
        if not ask_ads or not bid_ads:
            return

        # Spatial arbitrage: buy USDT at the lowest asks, sell at the highest bids.
        # We evaluate per payment method so the two legs are settleable.
        for pm_key, pm_cfg in PAYMENT_METHODS.items():
            pm_asks = sorted([a for a in ask_ads if pm_key in a.payment_methods], key=lambda a: a.price)[:15]
            pm_bids = sorted([a for a in bid_ads if pm_key in a.payment_methods], key=lambda a: a.price, reverse=True)[:15]
            if not pm_asks or not pm_bids:
                continue

            found_for_pm = 0
            for ask_ad in pm_asks:
                for bid_ad in pm_bids:
                    # No edge if the best place to sell isn't above the cheapest place to buy.
                    if bid_ad.price <= ask_ad.price:
                        continue
                    prev_opp_count = self._opportunity_count
                    await self._evaluate_p2p_spread(ask_ad, bid_ad, pm_key, pm_cfg)
                    if self._opportunity_count > prev_opp_count:
                        found_for_pm += 1
                        if found_for_pm >= 3:
                            break
                if found_for_pm >= 3:
                    break

    async def _evaluate_p2p_spread(
        self, ask_ad: P2PAdvert, bid_ad: P2PAdvert, pm_key: str, pm_cfg: dict
    ) -> None:
        """
        Evaluate a P2P spatial spread.

        Strategy: buy USDT on P2P at the lowest ask (KES/USDT), then sell it
        on P2P at the highest bid (KES/USDT). Profit is the rate differential
        net of fees/risk, not a fabricated $1.00 cost basis.
        """
        buy_rate = ask_ad.price    # KES per USDT we pay
        sell_rate = bid_ad.price   # KES per USDT we receive

        # Convert fiat min/max transaction limits (in KES) to crypto units (USDT)
        ask_min_crypto = (ask_ad.min_amount / buy_rate) if buy_rate > 0 else 0.0
        ask_max_crypto = min(
            ask_ad.available,
            (ask_ad.max_amount / buy_rate) if (ask_ad.max_amount > 0 and buy_rate > 0) else ask_ad.available,
        )
        bid_min_crypto = (bid_ad.min_amount / sell_rate) if sell_rate > 0 else 0.0
        bid_max_crypto = min(
            bid_ad.available,
            (bid_ad.max_amount / sell_rate) if (bid_ad.max_amount > 0 and sell_rate > 0) else bid_ad.available,
        )

        volume = min(ask_max_crypto, bid_max_crypto, MAX_CAPITAL_USD)
        if volume < max(ask_min_crypto, bid_min_crypto, 5.0):
            return

        # ── Gross spread in KES ──
        gross_kes = volume * (sell_rate - buy_rate)

        # Payment-method risk buffer (chargebacks etc.) expressed in KES
        fee_risk = volume * (pm_cfg["margin_pct"] / 100.0) * buy_rate

        # Cross-exchange transfer cost: only if the two legs are on different
        # exchanges. Same-exchange P2P needs no on-chain movement.
        same_exchange = ask_ad.exchange == bid_ad.exchange
        network = PREFERRED_NETWORKS.get("USDT", "TRC20")
        fee_withdrawal_units = 0.0 if same_exchange else WITHDRAWAL_FEES.get("USDT", {}).get(network, 1.0)
        fee_withdrawal_kes = fee_withdrawal_units * buy_rate

        net_profit_kes = gross_kes - fee_risk - fee_withdrawal_kes
        if net_profit_kes < MIN_P2P_PROFIT_KES:
            return

        mid_rate = (buy_rate + sell_rate) / 2.0
        net_profit_usd = net_profit_kes / mid_rate if mid_rate > 0 else 0.0
        margin_pct = (net_profit_kes / (volume * buy_rate)) * 100 if buy_rate > 0 else 0.0

        if same_exchange:
            transfer_time = "instant (same exchange)"
        else:
            transfer_times = {"TRC20": "~3 min", "ARB": "~1 min", "SOL": "~30 sec", "OP": "~2 min"}
            transfer_time = f"{transfer_times.get(network, '~5 min')} ({network})"

        opportunity = P2PSpreadOpportunity(
            pair=f"{P2P_CRYPTO}/{P2P_FIAT}",
            buy_exchange=ask_ad.exchange,
            sell_exchange=bid_ad.exchange,
            buy_price_usd=buy_rate,
            sell_price_kes=sell_rate,
            volume=volume,
            payment_method=pm_key,
            payment_label=pm_cfg["label"],
            risk_level=pm_cfg["risk_level"],
            margin_pct=margin_pct,
            fee_spot=0.0,
            fee_withdrawal=fee_withdrawal_units,
            fee_network=fee_withdrawal_kes,
            net_profit_kes=net_profit_kes,
            net_profit_usd=net_profit_usd,
            advertiser=bid_ad.advertiser,
            advertiser_rate=bid_ad.completion_rate,
            advertiser_trades=bid_ad.trade_count,
            transfer_time_est=transfer_time,
            timestamp=time.time(),
        )

        logger.info(
            "💱 P2P spread [%s]: buy @%.2f (%s) → sell @%.2f (%s) | %s | "
            "Vol=%.0f | Risk=%s | Net=KES%.0f ($%.2f)",
            pm_cfg["label"],
            buy_rate, ask_ad.exchange, sell_rate, bid_ad.exchange,
            f"{'same-ex' if same_exchange else 'cross-ex'}",
            volume, pm_cfg["risk_level"], net_profit_kes, net_profit_usd,
        )

        if self.on_spread:
            res = self.on_spread(opportunity.to_dict())
            if asyncio.iscoroutine(res):
                await res

        self._opportunity_count += 1

    async def _fetch_ads(self, exchange: str) -> list[P2PAdvert]:
        """Fetch BOTH buy and sell P2P ads from an exchange REST API."""
        ads: list[P2PAdvert] = []
        if exchange == "binance":
            ads += await self._fetch_binance_p2p("SELL")  # we buy USDT
            ads += await self._fetch_binance_p2p("BUY")   # we sell USDT
        elif exchange == "okx":
            ads += await self._fetch_okx_p2p("sell")      # we buy USDT
            ads += await self._fetch_okx_p2p("buy")       # we sell USDT
        elif exchange == "bybit":
            ads += await self._fetch_bybit_p2p("1")       # side 1 = advertiser sells → we buy
            ads += await self._fetch_bybit_p2p("0")       # side 0 = advertiser buys  → we sell
        return ads

    async def _fetch_binance_p2p(self, trade_type: str) -> list[P2PAdvert]:
        """Fetch Binance P2P ads for KES/USDT.

        trade_type "SELL" → advertiser sells USDT (we buy) → side="sell".
        trade_type "BUY"  → advertiser buys USDT  (we sell) → side="buy".
        """
        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        payload = {
            "fiat": P2P_FIAT,
            "page": 1,
            "rows": 20,
            "tradeType": trade_type,
            "asset": P2P_CRYPTO,
            "publisherType": None,
        }
        side = "sell" if trade_type == "SELL" else "buy"

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
                    return self._parse_binance_ads(ads, side)
        except Exception as exc:
            logger.error("[binance-p2p] Fetch failed: %s", exc)
            return []

    def _parse_binance_ads(self, ads: list, side: str) -> list[P2PAdvert]:
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
                side=side,
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

    async def _fetch_okx_p2p(self, side: str) -> list[P2PAdvert]:
        """Fetch OKX P2P ads. Uses OKX C2C API. side in {"sell","buy"}."""
        url = "https://www.okx.com/v3/c2c/tradingOrders/books"
        params = {
            "quoteCurrency": P2P_FIAT.lower(),
            "baseCurrency": P2P_CRYPTO.lower(),
            "side": side,
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
                    ads = data.get("data", {}).get(side, [])
                    return self._parse_okx_ads(ads, side)
        except Exception as exc:
            logger.error("[okx-p2p] Fetch failed: %s", exc)
            return []

    def _parse_okx_ads(self, ads: list, side: str) -> list[P2PAdvert]:
        """Parse OKX P2P response."""
        result = []
        for item in ads:
            payment_keys = []
            for pm in item.get("paymentMethods", []):
                mapped = P2P_PAYMENT_MAP.get("okx", {}).get(pm)
                if not mapped:
                    pm_str = str(pm).lower()
                    if "pesa" in pm_str or "airtel" in pm_str:
                        mapped = "mpesa"
                    elif "bank" in pm_str:
                        mapped = "bank_transfer"
                    elif "paypal" in pm_str:
                        mapped = "paypal"
                    elif "skrill" in pm_str:
                        mapped = "skrill"
                if mapped and mapped not in payment_keys:
                    payment_keys.append(mapped)

            if not payment_keys:
                continue

            result.append(P2PAdvert(
                exchange="okx",
                side=side,
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

    async def _fetch_bybit_p2p(self, side: str) -> list[P2PAdvert]:
        """Fetch Bybit P2P ads. side "1" = advertiser sells (we buy), "0" = advertiser buys (we sell)."""
        url = "https://api2.bybit.com/fiat/otc/item/online"
        payload = {
            "tokenId": P2P_CRYPTO,
            "currencyId": P2P_FIAT,
            "side": side,
            "size": "20",
            "page": "1",
        }
        internal_side = "sell" if side == "1" else "buy"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    items = data.get("result", {}).get("items", [])
                    return self._parse_bybit_ads(items, internal_side)
        except Exception as exc:
            logger.error("[bybit-p2p] Fetch failed: %s", exc)
            return []

    def _parse_bybit_ads(self, ads: list, side: str) -> list[P2PAdvert]:
        """Parse Bybit P2P response."""
        result = []
        for item in ads:
            payment_keys = []
            for pm in item.get("payments", []):
                pm_name = pm if isinstance(pm, str) else pm.get("paymentName", "")
                mapped = P2P_PAYMENT_MAP.get("bybit", {}).get(str(pm_name))
                if not mapped:
                    pm_str = str(pm_name).lower()
                    if "pesa" in pm_str or "176" in pm_str or "664" in pm_str:
                        mapped = "mpesa"
                    elif "bank" in pm_str or pm_str in ("14", "65", "170", "502", "109", "6"):
                        mapped = "bank_transfer"
                if mapped and mapped not in payment_keys:
                    payment_keys.append(mapped)

            if not payment_keys:
                continue

            result.append(P2PAdvert(
                exchange="bybit",
                side=side,
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
