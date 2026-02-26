"use client";

import React, { useState, useMemo } from "react";

interface SpreadData {
    pair: string;
    arb_type?: string;
    type?: string; // For "SIGNAL"
    buy_exchange?: string;
    sell_exchange?: string;
    exchange?: string; // For Signal
    ask_price?: number;
    bid_price?: number;
    price?: number; // For Signal
    action?: string; // For Signal (Strong Buy/Sell)
    indicator?: string; // For Signal
    raw_spread_pct?: number;
    volume?: number;
    net_profit?: number;
    net_profit_kes?: number;
    payment_method?: string;
    payment_label?: string;
    risk_level?: string;
    base?: string;
    quote?: string;
    timestamp: number | string;
}

interface SpreadGridProps {
    spreads: SpreadData[];
    title?: string;
}

type SortField = "net_profit" | "raw_spread_pct" | "timestamp";
type FilterType = "all" | "spot" | "p2p" | "triangular" | "signals";

export default function SpreadGrid({ spreads, title = "Live Arbitrage Spreads" }: SpreadGridProps) {
    const [sortBy, setSortBy] = useState<SortField>("net_profit");
    const [sortDesc, setSortDesc] = useState(true);
    const [filterType, setFilterType] = useState<FilterType>("all");

    const filtered = useMemo(() => {
        let items = [...spreads];

        if (filterType === "signals") {
            items = items.filter((s) => s.type === "SIGNAL");
        } else {
            // Exclude signals from normal arbitrage views
            items = items.filter((s) => s.type !== "SIGNAL");

            if (filterType !== "all") {
                items = items.filter((s) => (s.arb_type || "spot") === filterType);
            }
        }

        items.sort((a, b) => {
            if (filterType === "signals") {
                // Signals always sorted by most recent
                const aTs = typeof a.timestamp === 'string' ? new Date(a.timestamp).getTime() : (a.timestamp as number) * 1000;
                const bTs = typeof b.timestamp === 'string' ? new Date(b.timestamp).getTime() : (b.timestamp as number) * 1000;
                return bTs - aTs;
            }

            const aVal = (a[sortBy as keyof SpreadData] as number) ?? 0;
            const bVal = (b[sortBy as keyof SpreadData] as number) ?? 0;
            return sortDesc ? bVal - aVal : aVal - bVal;
        });

        return items;
    }, [spreads, sortBy, sortDesc, filterType]);

    const handleSort = (field: SortField) => {
        if (sortBy === field) setSortDesc(!sortDesc);
        else { setSortBy(field); setSortDesc(true); }
    };

    const arbTypeBadge = (s: SpreadData) => {
        if (s.type === "SIGNAL") return <span className="badge badge-signal">SIGNAL</span>;
        switch (s.arb_type) {
            case "p2p": return <span className="badge badge-p2p">P2P</span>;
            case "triangular": return <span className="badge badge-triangular">TRI</span>;
            default: return <span className="badge badge-spot">SPOT</span>;
        }
    };

    const actionBadge = (action?: string) => {
        if (!action) return null;
        const isBuy = action.includes("BUY");
        return <span className={`badge ${isBuy ? "badge-low-risk" : "badge-high-risk"}`}>{action}</span>;
    };

    const riskBadge = (level?: string) => {
        if (!level || level === "low") return <span className="badge badge-low-risk">LOW</span>;
        if (level === "high") return <span className="badge badge-high-risk">HIGH</span>;
        return <span className="badge badge-med-risk">MED</span>;
    };

    const formatTime = (ts: number | string) => {
        const d = typeof ts === 'string' ? new Date(ts) : new Date(ts * 1000);
        return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    };

    const formatPrice = (p?: number) => {
        if (p === undefined) return "—";
        if (p >= 100) return p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        if (p >= 1) return p.toFixed(4);
        return p.toFixed(6);
    };

    const isSignals = filterType === "signals";

    return (
        <div className="glass-card" id="spread-grid">
            <div className="spread-grid-header">
                <h2 className="card-title">{isSignals ? "Predictive Trading Signals" : title}</h2>

                {/* ── Filter Bar ── */}
                <div className="filter-bar">
                    <div className="filter-group">
                        {(["all", "spot", "p2p", "triangular", "signals"] as FilterType[]).map((ft) => (
                            <button
                                key={ft}
                                className={`filter-btn ${filterType === ft ? "filter-btn-active" : ""}`}
                                onClick={() => setFilterType(ft)}
                                id={`filter-${ft}`}
                            >
                                {ft === "all" ? "All" : ft === "p2p" ? "P2P" : ft === "triangular" ? "Tri" : ft === "signals" ? "Signals" : "Spot"}
                            </button>
                        ))}
                    </div>
                    {!isSignals && (
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
                    )}
                </div>
            </div>

            {filtered.length === 0 ? (
                <div className="empty-state">
                    <span className="empty-icon">{isSignals ? "📈" : "📡"}</span>
                    <p>No {filterType === "all" ? "" : (filterType === "p2p" ? "P2P" : filterType === "triangular" ? "Triangular" : filterType) + " "} {isSignals ? "signals" : "spreads"} detected yet</p>
                    <p className="empty-sub">Scanning across all exchanges...</p>
                </div>
            ) : (
                <div className="table-wrapper">
                    <table className="data-table">
                        <thead>
                            {isSignals ? (
                                <tr>
                                    <th>Type</th>
                                    <th>Pair</th>
                                    <th>Exchange</th>
                                    <th>Action</th>
                                    <th>Price</th>
                                    <th>Indicator / Reason</th>
                                    <th>Time</th>
                                </tr>
                            ) : (
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
                            )}
                        </thead>
                        <tbody>
                            {filtered.slice(0, 50).map((s, i) => (
                                <tr key={`${s.pair}-${s.timestamp}-${i}`} className="data-row fade-in-row">
                                    {isSignals ? (
                                        <>
                                            <td>{arbTypeBadge(s)}</td>
                                            <td className="cell-mono" style={{ color: "var(--color-text-accent)" }}>{s.pair}</td>
                                            <td><span className="exchange-tag">{s.exchange}</span></td>
                                            <td>{actionBadge(s.action)}</td>
                                            <td className="cell-mono cell-price">${formatPrice(s.price)}</td>
                                            <td style={{ fontSize: "0.75rem", opacity: 0.8 }}>{s.indicator}</td>
                                            <td className="cell-time">{formatTime(s.timestamp)}</td>
                                        </>
                                    ) : (
                                        <>
                                            <td>{arbTypeBadge(s)}</td>
                                            <td className="cell-mono">{s.pair}</td>
                                            <td>
                                                <span className="exchange-tag">{s.buy_exchange?.toUpperCase()}</span>
                                                <br />
                                                <span className="cell-price">{formatPrice(s.ask_price)}</span>
                                            </td>
                                            <td>
                                                <span className="exchange-tag">{s.sell_exchange?.toUpperCase()}</span>
                                                <br />
                                                <span className="cell-price">{formatPrice(s.bid_price)}</span>
                                            </td>
                                            <td className="cell-mono cell-spread">{s.raw_spread_pct?.toFixed(3)}%</td>
                                            <td className="cell-mono cell-profit">
                                                ${s.net_profit?.toFixed(2)}
                                                {s.net_profit_kes ? (
                                                    <span className="profit-kes"> / KES {s.net_profit_kes.toLocaleString()}</span>
                                                ) : null}
                                            </td>
                                            <td>{s.payment_label ? <span className="payment-badge">{s.payment_label}</span> : "—"}</td>
                                            <td>{riskBadge(s.risk_level)}</td>
                                            <td className="cell-time">{formatTime(s.timestamp as number)}</td>
                                        </>
                                    )}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
