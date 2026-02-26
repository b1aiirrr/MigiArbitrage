"use client";

import { useEffect, useRef, useState, useCallback } from "react";

export interface SpreadData {
    pair: string;
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
    preflight: {
        passed: boolean;
        network: string;
        withdrawal_fee: number;
        risk_level: string;
        risk_notes: string[];
        buy_wallet: any;
        sell_wallet: any;
    } | null;
    timestamp: number;
}

export interface WSMessage {
    type: "spread" | "history" | "books" | "status" | "pong";
    data: any;
    timestamp: number;
}

interface UseArbitrageStreamReturn {
    connected: boolean;
    spreads: SpreadData[];
    history: SpreadData[];
    books: Record<string, any>;
    clientCount: number;
}

const MAX_LIVE_SPREADS = 50;
const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 30000]; // Exponential backoff

/**
 * Custom hook to connect to the MigiArbitrage backend WebSocket server.
 * Handles auto-reconnection with exponential backoff.
 */
export function useArbitrageStream(): UseArbitrageStreamReturn {
    const [connected, setConnected] = useState(false);
    const [spreads, setSpreads] = useState<SpreadData[]>([]);
    const [history, setHistory] = useState<SpreadData[]>([]);
    const [books, setBooks] = useState<Record<string, any>>({});
    const [clientCount, setClientCount] = useState(0);

    const wsRef = useRef<WebSocket | null>(null);
    const reconnectAttempt = useRef(0);
    const reconnectTimer = useRef<NodeJS.Timeout | null>(null);

    const wsUrl =
        typeof window !== "undefined"
            ? process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8765"
            : "";

    const connect = useCallback(() => {
        if (!wsUrl) return;

        try {
            const ws = new WebSocket(wsUrl);
            wsRef.current = ws;

            ws.onopen = () => {
                setConnected(true);
                reconnectAttempt.current = 0;
                console.log("[MigiArbitrage] WebSocket connected");
            };

            ws.onmessage = (event) => {
                try {
                    const msg: WSMessage = JSON.parse(event.data);

                    switch (msg.type) {
                        case "spread":
                            setSpreads((prev) => {
                                const next = [msg.data as SpreadData, ...prev];
                                return next.slice(0, MAX_LIVE_SPREADS);
                            });
                            break;

                        case "history":
                            setHistory(msg.data as SpreadData[]);
                            break;

                        case "books":
                            setBooks(msg.data as Record<string, any>);
                            break;

                        case "status":
                            setClientCount(msg.data?.clients || 0);
                            break;

                        case "pong":
                            break;
                    }
                } catch (err) {
                    console.warn("[MigiArbitrage] Parse error:", err);
                }
            };

            ws.onclose = () => {
                setConnected(false);
                wsRef.current = null;
                console.log("[MigiArbitrage] WebSocket disconnected — reconnecting...");
                scheduleReconnect();
            };

            ws.onerror = (err) => {
                console.error("[MigiArbitrage] WebSocket error:", err);
                ws.close();
            };
        } catch (err) {
            console.error("[MigiArbitrage] Connection failed:", err);
            scheduleReconnect();
        }
    }, [wsUrl]);

    const scheduleReconnect = useCallback(() => {
        const delay =
            RECONNECT_DELAYS[
            Math.min(reconnectAttempt.current, RECONNECT_DELAYS.length - 1)
            ];
        reconnectAttempt.current++;

        reconnectTimer.current = setTimeout(() => {
            connect();
        }, delay);
    }, [connect]);

    useEffect(() => {
        connect();

        // Ping interval to keep connection alive
        const pingInterval = setInterval(() => {
            if (wsRef.current?.readyState === WebSocket.OPEN) {
                wsRef.current.send(JSON.stringify({ type: "ping" }));
            }
        }, 30000);

        return () => {
            clearInterval(pingInterval);
            if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
            if (wsRef.current) wsRef.current.close();
        };
    }, [connect]);

    return { connected, spreads, history, books, clientCount };
}
