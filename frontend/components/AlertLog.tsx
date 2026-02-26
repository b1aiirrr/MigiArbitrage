"use client";

import React from "react";
import type { SpreadData } from "@/hooks/useArbitrageStream";

interface AlertLogProps {
    alerts: SpreadData[];
}

function formatDateTime(ts: number): string {
    const d = new Date(ts * 1000);
    return d.toLocaleString("en-GB", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    });
}

function getRiskIcon(preflight: SpreadData["preflight"]): string {
    if (!preflight) return "❓";
    switch (preflight.risk_level) {
        case "high":
            return "🔴";
        case "medium":
            return "🟡";
        default:
            return "🟢";
    }
}

export default function AlertLog({ alerts }: AlertLogProps) {
    if (alerts.length === 0) {
        return (
            <div className="data-section">
                <div className="section-header">
                    <h2 className="section-title">
                        <span>📋</span> Alert History
                    </h2>
                </div>
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-icon">📋</div>
                        <div className="empty-state-title">No alerts yet</div>
                        <div className="empty-state-desc">
                            Historical alerts will be archived here as they are triggered.
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
                    <span>📋</span> Alert History
                </h2>
                <span className="badge badge-info">{alerts.length} alerts</span>
            </div>
            <div className="glass-card" style={{ overflow: "hidden" }}>
                <div className="data-table-wrapper">
                    <table className="data-table" id="alert-log">
                        <thead>
                            <tr>
                                <th>Time</th>
                                <th>Pair</th>
                                <th>Direction</th>
                                <th>Spread</th>
                                <th>Net Profit</th>
                                <th>Network</th>
                                <th>Risk</th>
                            </tr>
                        </thead>
                        <tbody>
                            {alerts.map((a, i) => (
                                <tr key={`hist-${a.timestamp}-${i}`}>
                                    <td className="cell-mono" style={{ color: "var(--color-text-muted)" }}>
                                        {formatDateTime(a.timestamp)}
                                    </td>
                                    <td>
                                        <strong style={{ color: "var(--color-accent)" }}>{a.pair}</strong>
                                    </td>
                                    <td style={{ fontSize: "0.8rem" }}>
                                        <span style={{ color: "var(--color-profit)" }}>
                                            BUY {a.buy_exchange.toUpperCase()}
                                        </span>
                                        {" → "}
                                        <span style={{ color: "var(--color-loss)" }}>
                                            SELL {a.sell_exchange.toUpperCase()}
                                        </span>
                                    </td>
                                    <td className="cell-mono" style={{ color: "var(--color-accent)" }}>
                                        {a.raw_spread_pct.toFixed(3)}%
                                    </td>
                                    <td className="cell-mono cell-profit">${a.net_profit.toFixed(2)}</td>
                                    <td className="cell-mono" style={{ color: "var(--color-text-secondary)" }}>
                                        {a.preflight?.network || "N/A"}
                                    </td>
                                    <td style={{ textAlign: "center" }}>
                                        {getRiskIcon(a.preflight)}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}
