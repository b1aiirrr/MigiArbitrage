"use client";

import React from "react";
import Logo from "@/components/Logo";
import SpreadGrid from "@/components/SpreadGrid";
import AlertLog from "@/components/AlertLog";
import { useArbitrageStream } from "@/hooks/useArbitrageStream";

const EXCHANGES = [
    "Binance", "OKX", "KuCoin", "MEXC", "Bybit",
    "Gate.io", "Bitget", "Coinbase",
];

export default function DashboardPage() {
    const { connected, spreads, history, books, clientCount } =
        useArbitrageStream();

    const allAlerts = [...spreads, ...history].sort(
        (a, b) => b.timestamp - a.timestamp
    );

    // Stats
    const totalSpreads = allAlerts.length;
    const totalProfit = allAlerts.reduce((sum, s) => sum + s.net_profit, 0);
    const avgSpread =
        totalSpreads > 0
            ? allAlerts.reduce((sum, s) => sum + s.raw_spread_pct, 0) / totalSpreads
            : 0;
    const validBooks = Object.values(books).filter((b: any) => b?.valid).length;
    const totalBooks = Object.keys(books).length;

    // Arb type counts
    const spotCount = allAlerts.filter((a) => (a.arb_type || "spot") === "spot").length;
    const p2pCount = allAlerts.filter((a) => a.arb_type === "p2p").length;
    const triCount = allAlerts.filter((a) => a.arb_type === "triangular").length;

    return (
        <div className="app-container">
            {/* ── Header ── */}
            <header className="app-header">
                <div className="app-header-left">
                    <Logo width={200} height={44} />
                    <span className="version-badge">v2.0</span>
                </div>
                <div className="app-header-right">
                    <div className="connection-status" id="connection-status">
                        <span
                            className={`status-dot ${connected ? "status-dot-connected" : "status-dot-disconnected"
                                }`}
                        />
                        <span>{connected ? "Live" : "Disconnected"}</span>
                    </div>
                    <span className="badge badge-info" style={{ fontSize: "0.7rem" }}>
                        🔒 Semi-Auto Mode
                    </span>
                </div>
            </header>

            {/* ── Stats Grid ── */}
            <div className="stats-grid">
                <div className="glass-card stat-card" id="stat-active-books">
                    <span className="stat-label">Active Order Books</span>
                    <span className="stat-value stat-value-accent">
                        {validBooks}
                        <span style={{ fontSize: "0.8rem", color: "var(--color-text-muted)", fontWeight: 400 }}>
                            {" "} / {totalBooks || "—"}
                        </span>
                    </span>
                </div>
                <div className="glass-card stat-card" id="stat-spreads-found">
                    <span className="stat-label">Spreads Detected</span>
                    <span className="stat-value stat-value-accent">
                        {totalSpreads}
                        <span style={{ fontSize: "0.7rem", color: "var(--color-text-muted)", fontWeight: 400, marginLeft: 6 }}>
                            S:{spotCount} P:{p2pCount} T:{triCount}
                        </span>
                    </span>
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
            <footer className="app-footer">
                <div className="footer-exchanges">
                    <span className="footer-exchanges-label">Exchanges Covered</span>
                    <div className="footer-exchange-grid">
                        {EXCHANGES.map((ex) => (
                            <span key={ex} className="footer-exchange-chip">{ex}</span>
                        ))}
                    </div>
                </div>

                <div className="footer-divider" />

                <div className="footer-legal">
                    <p className="footer-brand">
                        <span className="footer-logo-text">MigiArbitrage</span>
                        <span className="footer-flag">🇰🇪</span>
                        <span className="footer-tagline">Real-Time Crypto Arbitrage Intelligence · Nairobi, Kenya</span>
                    </p>
                    <p className="footer-disclaimer">
                        ⚠ Semi-automated mode — manual payment confirmation required for P2P trades. No funds are moved without user action.
                    </p>
                    <p className="footer-copyright">
                        © {new Date().getFullYear()} MigiArbitrage. All rights reserved.
                        {clientCount > 0 && (
                            <span className="footer-clients"> · {clientCount} active client{clientCount > 1 ? "s" : ""}</span>
                        )}
                    </p>
                </div>
            </footer>
        </div>
    );
}
