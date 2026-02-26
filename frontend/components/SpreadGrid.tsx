"use client";

import React from "react";
import type { SpreadData } from "@/hooks/useArbitrageStream";

interface SpreadGridProps {
    spreads: SpreadData[];
    title?: string;
}

function formatTime(ts: number): string {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    });
}

function formatPrice(price: number): string {
    if (price >= 1000) return price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (price >= 1) return price.toFixed(4);
    return price.toFixed(6);
}

function getRiskBadge(preflight: SpreadData["preflight"]): React.ReactNode {
    if (!preflight) {
        return <span className="badge badge-info">Unchecked</span>;
    }
    if (preflight.risk_level === "high") {
        return <span className="badge badge-danger">High Risk</span>;
    }
    if (preflight.risk_level === "medium") {
        return <span className="badge badge-warning">Caution</span>;
    }
    return <span className="badge badge-profit">Verified ✓</span>;
}

export default function SpreadGrid({ spreads, title = "Live Spreads" }: SpreadGridProps) {
    if (spreads.length === 0) {
        return (
            <div className="data-section">
                <div className="section-header">
                    <h2 className="section-title">
                        <span>📡</span> {title}
                    </h2>
                </div>
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-icon">🔍</div>
                        <div className="empty-state-title">Scanning for arbitrage opportunities...</div>
                        <div className="empty-state-desc">
                            Live spreads will appear here when profitable opportunities are detected
                            across Binance, Kraken, and KuCoin.
                        </div>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="data-section">
            <div className="section-header">
                <h2 className="section-title">
                    <span>📡</span> {title}
                </h2>
                <span className="badge badge-info">{spreads.length} active</span>
            </div>
            <div className="glass-card" style={{ overflow: "hidden" }}>
                <div className="data-table-wrapper">
                    <table className="data-table" id="spread-grid">
                        <thead>
                            <tr>
                                <th>Time</th>
                                <th>Pair</th>
                                <th>Buy Exchange</th>
                                <th>Ask Price</th>
                                <th>Sell Exchange</th>
                                <th>Bid Price</th>
                                <th>Spread</th>
                                <th>Volume</th>
                                <th>Net Profit</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            {spreads.map((s, i) => (
                                <tr
                                    key={`${s.timestamp}-${s.pair}-${i}`}
                                    className={i === 0 ? "row-new" : ""}
                                >
                                    <td className="cell-mono" style={{ color: "var(--color-text-muted)" }}>
                                        {formatTime(s.timestamp)}
                                    </td>
                                    <td>
                                        <strong style={{ color: "var(--color-accent)" }}>{s.pair}</strong>
                                    </td>
                                    <td>
                                        <span style={{ textTransform: "uppercase", fontWeight: 500 }}>
                                            {s.buy_exchange}
                                        </span>
                                    </td>
                                    <td className="cell-mono">${formatPrice(s.ask_price)}</td>
                                    <td>
                                        <span style={{ textTransform: "uppercase", fontWeight: 500 }}>
                                            {s.sell_exchange}
                                        </span>
                                    </td>
                                    <td className="cell-mono">${formatPrice(s.bid_price)}</td>
                                    <td className="cell-mono" style={{ color: "var(--color-accent)" }}>
                                        {s.raw_spread_pct.toFixed(3)}%
                                    </td>
                                    <td className="cell-mono">
                                        {s.volume.toFixed(6)} {s.base}
                                    </td>
                                    <td className="cell-mono cell-profit">${s.net_profit.toFixed(2)}</td>
                                    <td>{getRiskBadge(s.preflight)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}
