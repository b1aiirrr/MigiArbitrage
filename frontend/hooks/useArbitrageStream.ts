"use client";

import { useState, useEffect, useRef, useCallback } from "react";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8765";

export interface SpreadData {
    pair: string;
    arb_type?: string;
    base: string;
    quote: string;
    buy_exchange: string;
    sell_exchange: string;
    ask_price: number;
    bid_price: number;
    raw_spread_pct: number;
    volume: number;
    fee_maker: number;
    fee_taker: number;
    fee_withdrawal: number;
    fee_network: number;
    net_profit: number;
    net_profit_kes?: number;
    payment_method?: string;
    payment_label?: string;
    risk_level?: string;
    margin_pct?: number;
    transfer_time_est?: string;
    advertiser?: string;
    advertiser_rate?: number;
    advertiser_trades?: number;
    preflight?: {
        passed: boolean;
        risk_level: string;
        network: string;
        risk_notes: string[];
    };
    triangular_steps?: { pair: string; side: string; price: number }[];
    action?: string;
    indicator?: string;
    type?: string;
    timestamp: number;
}

interface StreamState {
    connected: boolean;
    spreads: SpreadData[];
    history: SpreadData[];
    books: Record<string, any>;
    clientCount: number;
}

export function useArbitrageStream(): StreamState {
    const [connected, setConnected] = useState(false);
    const [spreads, setSpreads] = useState<SpreadData[]>([]);
    const [history, setHistory] = useState<SpreadData[]>([]);
    const [books, setBooks] = useState<Record<string, any>>({});
    const [clientCount, setClientCount] = useState(0);

    const wsRef = useRef<WebSocket | null>(null);
    const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const reconnectAttempts = useRef(0);
    const maxReconnectDelay = 30000;

    const connect = useCallback(() => {
        if (wsRef.current?.readyState === WebSocket.OPEN) return;

        try {
            const ws = new WebSocket(WS_URL);
            wsRef.current = ws;

            ws.onopen = () => {
                setConnected(true);
                reconnectAttempts.current = 0;
                console.log("[WS] Connected to", WS_URL);
            };

            ws.onmessage = (event) => {
                try {
                    const msg = JSON.parse(event.data);

                    switch (msg.type) {
                        case "spread":
                            setSpreads((prev) => {
                                const next = [msg.data, ...prev];
                                return next.slice(0, 200);
                            });
                            break;

                        case "history":
                            setHistory(Array.isArray(msg.data) ? msg.data : []);
                            break;

                        case "books":
                            setBooks(msg.data || {});
                            break;

                        case "status":
                            setClientCount(msg.data?.clients || 0);
                            break;

                        case "pong":
                            break;
                    }
                } catch (e) {
                    console.error("[WS] Parse error:", e);
                }
            };

            ws.onclose = () => {
                setConnected(false);
                scheduleReconnect();
            };

            ws.onerror = (err) => {
                console.error("[WS] Error:", err);
                ws.close();
            };
        } catch (err) {
            console.error("[WS] Connection failed:", err);
            scheduleReconnect();
        }
    }, []);

    const scheduleReconnect = useCallback(() => {
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts.current), maxReconnectDelay);
        reconnectAttempts.current += 1;
        console.log(`[WS] Reconnecting in ${delay}ms (attempt ${reconnectAttempts.current})`);

        reconnectTimer.current = setTimeout(() => {
            connect();
        }, delay);
    }, [connect]);

    useEffect(() => {
        connect();

        // Heartbeat
        const heartbeat = setInterval(() => {
            if (wsRef.current?.readyState === WebSocket.OPEN) {
                wsRef.current.send(JSON.stringify({ type: "ping" }));
            }
        }, 25000);

        return () => {
            clearInterval(heartbeat);
            if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
            wsRef.current?.close();
        };
    }, [connect]);

    return { connected, spreads, history, books, clientCount };
}
