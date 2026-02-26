"use client";

import React from "react";

/**
 * Exchange brand logos as inline SVGs with official brand colors.
 * Self-contained — no external CDN dependencies that could break.
 */

interface ExchangeInfo {
    id: string;
    name: string;
    color: string;
    bgColor: string;
    letter: string;
}

const EXCHANGE_BRANDS: ExchangeInfo[] = [
    { id: "binance", name: "Binance", color: "#F0B90B", bgColor: "rgba(240,185,11,0.12)", letter: "B" },
    { id: "okx", name: "OKX", color: "#FFFFFF", bgColor: "rgba(255,255,255,0.08)", letter: "O" },
    { id: "kucoin", name: "KuCoin", color: "#23AF91", bgColor: "rgba(35,175,145,0.12)", letter: "K" },
    { id: "mexc", name: "MEXC", color: "#00B897", bgColor: "rgba(0,184,151,0.12)", letter: "M" },
    { id: "bybit", name: "Bybit", color: "#F7A600", bgColor: "rgba(247,166,0,0.12)", letter: "B" },
    { id: "gateio", name: "Gate.io", color: "#2354E6", bgColor: "rgba(35,84,230,0.12)", letter: "G" },
    { id: "bitget", name: "Bitget", color: "#00F0FF", bgColor: "rgba(0,240,255,0.10)", letter: "B" },
    { id: "coinbase", name: "Coinbase", color: "#0052FF", bgColor: "rgba(0,82,255,0.12)", letter: "C" },
];

function ExchangeLogo({ brand }: { brand: ExchangeInfo }) {
    return (
        <div className="exchange-logo-card" id={`exchange-${brand.id}`}>
            <svg
                width="36"
                height="36"
                viewBox="0 0 36 36"
                fill="none"
                xmlns="http://www.w3.org/2000/svg"
                className="exchange-logo-svg"
            >
                {/* Outer ring */}
                <circle cx="18" cy="18" r="17" stroke={brand.color} strokeWidth="1" strokeOpacity="0.3" fill="none" />
                {/* Inner fill */}
                <circle cx="18" cy="18" r="14" fill={brand.bgColor} />
                {/* Letter */}
                <text
                    x="18"
                    y="18"
                    textAnchor="middle"
                    dominantBaseline="central"
                    fill={brand.color}
                    fontSize="13"
                    fontWeight="700"
                    fontFamily="Inter, sans-serif"
                >
                    {brand.letter}
                </text>
            </svg>
            <span className="exchange-logo-name" style={{ color: brand.color }}>
                {brand.name}
            </span>
        </div>
    );
}

export default function ExchangeLogos() {
    return (
        <div className="exchange-logos-grid">
            {EXCHANGE_BRANDS.map((brand) => (
                <ExchangeLogo key={brand.id} brand={brand} />
            ))}
        </div>
    );
}
