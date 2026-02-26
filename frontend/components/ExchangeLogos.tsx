"use client";

import React from "react";

/**
 * Exchange logos using official favicons via Google's Favicon API.
 * Serves the real exchange logo at 128px, auto-updates if branding changes.
 */

interface ExchangeBrand {
    id: string;
    name: string;
    domain: string;
    color: string;
}

const EXCHANGE_BRANDS: ExchangeBrand[] = [
    { id: "binance", name: "Binance", domain: "binance.com", color: "#F0B90B" },
    { id: "okx", name: "OKX", domain: "okx.com", color: "#FFFFFF" },
    { id: "kucoin", name: "KuCoin", domain: "kucoin.com", color: "#23AF91" },
    { id: "mexc", name: "MEXC", domain: "mexc.com", color: "#00B897" },
    { id: "bybit", name: "Bybit", domain: "bybit.com", color: "#F7A600" },
    { id: "gateio", name: "Gate.io", domain: "gate.io", color: "#2354E6" },
    { id: "bitget", name: "Bitget", domain: "bitget.com", color: "#00F0FF" },
    { id: "coinbase", name: "Coinbase", domain: "coinbase.com", color: "#0052FF" },
];

function getLogoUrl(domain: string): string {
    return `https://www.google.com/s2/favicons?domain=${domain}&sz=128`;
}

export default function ExchangeLogos() {
    return (
        <div className="exchange-logos-grid">
            {EXCHANGE_BRANDS.map((brand) => (
                <div key={brand.id} className="exchange-logo-card" id={`exchange-${brand.id}`}>
                    <div className="exchange-logo-img-wrap">
                        <img
                            src={getLogoUrl(brand.domain)}
                            alt={`${brand.name} logo`}
                            width={40}
                            height={40}
                            loading="lazy"
                            className="exchange-logo-img"
                        />
                    </div>
                    <span className="exchange-logo-name" style={{ color: brand.color }}>
                        {brand.name}
                    </span>
                </div>
            ))}
        </div>
    );
}
