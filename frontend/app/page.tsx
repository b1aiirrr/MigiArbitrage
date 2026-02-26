"use client";

import React from "react";
import Logo from "@/components/Logo";
import SpreadGrid from "@/components/SpreadGrid";
import AlertLog from "@/components/AlertLog";
import { useArbitrageStream } from "@/hooks/useArbitrageStream";

export default function DashboardPage() {
    const { connected, spreads, history, books, clientCount } =
        useArbitrageStream();

    // Combine live spreads and history for alert log
    const allAlerts = [...spreads, ...history].sort(
        (a, b) => b.timestamp - a.timestamp
    );

    // Compute stats
    const totalSpreads = allAlerts.length;
    const totalProfit = allAlerts.reduce((sum, s) => sum + s.net_profit, 0);
    const avgSpread =
        totalSpreads > 0
            ? allAlerts.reduce((sum, s) => sum + s.raw_spread_pct, 0) / totalSpreads
            : 0;
    const validBooks = Object.values(books).filter((b: any) => b?.valid).length;
    const totalBooks = Object.keys(books).length;

    return (
        <div className="app-container">
            {/* ── Header ── */}
            <header className="app-header">
                <div className="app-header-left">
                    <Logo width={200} height={44} />
                </div>
                <div className="app-header-right">
                    <div className="connection-status" id="connection-status">
                        <span
                            className={`status-dot ${connected ? "status-dot-connected" : "status-dot-disconnected"
                                }`}
                        />
                        <span>{connected ? "Live" : "Disconnected"}</span>
                    </div>
                    <span
                        className="badge badge-info"
                        style={{ fontSize: "0.7rem" }}
                    >
                        🔒 Alert-Only Mode
                    </span>
                </div>
            </header>

            {/* ── Stats Grid ── */}
            <div className="stats-grid">
                <div className="glass-card stat-card" id="stat-active-books">
                    <span className="stat-label">Active Order Books</span>
                    <span className="stat-value stat-value-accent">
                        {validBooks}
                        <span
                            style={{
                                fontSize: "0.8rem",
                                color: "var(--color-text-muted)",
                                fontWeight: 400,
                            }}
                        >
                            {" "}
                            / {totalBooks || "—"}
                        </span>
                    </span>
                </div>
                <div className="glass-card stat-card" id="stat-spreads-found">
                    <span className="stat-label">Spreads Detected</span>
                    <span className="stat-value stat-value-accent">{totalSpreads}</span>
                </div>
                <div className="glass-card stat-card" id="stat-total-profit">
                    <span className="stat-label">Total Est. Profit</span>
                    <span className="stat-value stat-value-profit">
                        ${totalProfit.toFixed(2)}
                    </span>
                </div>
                <div className="glass-card stat-card" id="stat-avg-spread">
                    <span className="stat-label">Avg. Spread</span>
                    <span className="stat-value stat-value-accent">
                        {avgSpread.toFixed(3)}%
                    </span>
                </div>
            </div>

            {/* ── Live Spread Grid ── */}
            <SpreadGrid spreads={spreads} title="Live Arbitrage Spreads" />

            {/* ── Alert History ── */}
            <AlertLog alerts={allAlerts.slice(0, 100)} />

            {/* ── Footer ── */}
            <footer
                style={{
                    textAlign: "center",
                    padding: "var(--space-2xl) 0 var(--space-lg)",
                    color: "var(--color-text-muted)",
                    fontSize: "0.75rem",
                    borderTop: "1px solid var(--color-border)",
                    marginTop: "var(--space-2xl)",
                }}
            >
                <p>
                    MigiArbitrage — Real-Time Crypto Arbitrage Scanner •{" "}
                    <span style={{ color: "var(--color-warning)" }}>⚠ Alert-only mode</span> — No
                    trades are executed
                </p>
                <p style={{ marginTop: "var(--space-xs)", opacity: 0.6 }}>
                    Monitoring Binance · Kraken · KuCoin •{" "}
                    {clientCount > 0 && `${clientCount} client${clientCount > 1 ? "s" : ""} connected`}
                </p>
            </footer>
        </div>
    );
}
