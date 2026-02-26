"use client";

import React, { useState, useMemo } from "react";

interface SpreadData {
    pair: string;
    arb_type?: string;
    buy_exchange: string;
    sell_exchange: string;
    ask_price: number;
    bid_price: number;
    raw_spread_pct: number;
    volume: number;
    net_profit: number;
    net_profit_kes?: number;
    payment_method?: string;
    payment_label?: string;
    risk_level?: string;
    base: string;
    quote: string;
    timestamp: number;
    preflight?: {
        passed: boolean;
        risk_level: string;
        network: string;
    };
}

interface SpreadGridProps {
    spreads: SpreadData[];
    title?: string;
}

type SortField = "net_profit" | "raw_spread_pct" | "timestamp";
type FilterType = "all" | "spot" | "p2p" | "triangular";

export default function SpreadGrid({ spreads, title = "Live Arbitrage Spreads" }: SpreadGridProps) {
    const [sortBy, setSortBy] = useState<SortField>("net_profit");
    const [sortDesc, setSortDesc] = useState(true);
    const [filterType, setFilterType] = useState<FilterType>("all");

    const filtered = useMemo(() => {
        let items = [...spreads];
        if (filterType !== "all") {
            items = items.filter((s) => (s.arb_type || "spot") === filterType);
        }
        items.sort((a, b) => {
            const aVal = a[sortBy] ?? 0;
            const bVal = b[sortBy] ?? 0;
            return sortDesc ? bVal - aVal : aVal - bVal;
        });
        return items;
    }, [spreads, sortBy, sortDesc, filterType]);

    const handleSort = (field: SortField) => {
        if (sortBy === field) setSortDesc(!sortDesc);
        else { setSortBy(field); setSortDesc(true); }
    };

    const arbTypeBadge = (type: string) => {
        switch (type) {
            case "p2p": return <span className="badge badge-p2p">P2P</span>;
            case "triangular": return <span className="badge badge-triangular">TRI</span>;
            default: return <span className="badge badge-spot">SPOT</span>;
        }
    };

    const riskBadge = (level?: string) => {
        if (!level || level === "low") return <span className="badge badge-low-risk">LOW</span>;
        if (level === "high") return <span className="badge badge-high-risk">HIGH</span>;
        return <span className="badge badge-med-risk">MED</span>;
    };

    const formatTime = (ts: number) => {
        const d = new Date(ts * 1000);
        return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    };

    const formatPrice = (p: number) => {
        if (p >= 100) return p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        if (p >= 1) return p.toFixed(4);
        return p.toFixed(6);
    };

    return (
        <div className="glass-card" id="spread-grid">
            <div className="spread-grid-header">
                <h2 className="card-title">{title}</h2>

                {/* ── Filter Bar ── */}
                <div className="filter-bar">
                    <div className="filter-group">
                        {(["all", "spot", "p2p", "triangular"] as FilterType[]).map((ft) => (
                            <button
                                key={ft}
                                className={`filter-btn ${filterType === ft ? "filter-btn-active" : ""}`}
                                onClick={() => setFilterType(ft)}
                                id={`filter-${ft}`}
                            >
                                {ft === "all" ? "All" : ft === "p2p" ? "P2P" : ft === "triangular" ? "Tri" : "Spot"}
                            </button>
                        ))}
                    </div>
                    <div className="filter-group">
                        <select
                            className="sort-select"
                            value={sortBy}
                            onChange={(e) => { setSortBy(e.target.value as SortField); setSortDesc(true); }}
                            id="sort-select"
                        >
                            <option value="net_profit">Highest Profit</option>
                            <option value="raw_spread_pct">Highest Spread</option>
                            <option value="timestamp">Most Recent</option>
                        </select>
                    </div>
                </div>
            </div>

            {filtered.length === 0 ? (
                <div className="empty-state">
                    <span className="empty-icon">📡</span>
                    <p>No {filterType === "all" ? "" : filterType + " "}spreads detected yet</p>
                    <p className="empty-sub">Scanning across all exchanges...</p>
                </div>
            ) : (
                <div className="table-wrapper">
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Type</th>
                                <th>Pair</th>
                                <th>Buy</th>
                                <th>Sell</th>
                                <th className="sortable" onClick={() => handleSort("raw_spread_pct")}>
                                    Spread {sortBy === "raw_spread_pct" ? (sortDesc ? "▼" : "▲") : ""}
                                </th>
                                <th className="sortable" onClick={() => handleSort("net_profit")}>
                                    Net Profit {sortBy === "net_profit" ? (sortDesc ? "▼" : "▲") : ""}
                                </th>
                                <th>Payment</th>
                                <th>Risk</th>
                                <th className="sortable" onClick={() => handleSort("timestamp")}>
                                    Time {sortBy === "timestamp" ? (sortDesc ? "▼" : "▲") : ""}
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            {filtered.slice(0, 50).map((s, i) => (
                                <tr key={`${s.pair}-${s.buy_exchange}-${s.timestamp}-${i}`} className="data-row fade-in-row">
                                    <td>{arbTypeBadge(s.arb_type || "spot")}</td>
                                    <td className="cell-mono">{s.pair}</td>
                                    <td>
                                        <span className="exchange-tag">{s.buy_exchange.toUpperCase()}</span>
                                        <br />
                                        <span className="cell-price">{formatPrice(s.ask_price)}</span>
                                    </td>
                                    <td>
                                        <span className="exchange-tag">{s.sell_exchange.toUpperCase()}</span>
                                        <br />
                                        <span className="cell-price">{formatPrice(s.bid_price)}</span>
                                    </td>
                                    <td className="cell-mono cell-spread">{s.raw_spread_pct.toFixed(3)}%</td>
                                    <td className="cell-mono cell-profit">
                                        ${s.net_profit.toFixed(2)}
                                        {s.net_profit_kes ? (
                                            <span className="profit-kes"> / KES {s.net_profit_kes.toLocaleString()}</span>
                                        ) : null}
                                    </td>
                                    <td>{s.payment_label ? <span className="payment-badge">{s.payment_label}</span> : "—"}</td>
                                    <td>{riskBadge(s.risk_level)}</td>
                                    <td className="cell-time">{formatTime(s.timestamp)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
