"use client";

import React, { useState } from "react";

interface PaystackModalProps {
    isOpen: boolean;
    onClose: () => void;
}

declare global {
    interface Window {
        PaystackPop?: any;
    }
}

export default function PaystackModal({ isOpen, onClose }: PaystackModalProps) {
    const [amount, setAmount] = useState("10");
    const [phone, setPhone] = useState("");
    const [email, setEmail] = useState("");
    const [loading, setLoading] = useState(false);

    if (!isOpen) return null;

    const handlePay = () => {
        setLoading(true);

        const loadScript = () => {
            return new Promise((resolve) => {
                if (window.PaystackPop) {
                    resolve(true);
                    return;
                }
                const script = document.createElement("script");
                script.src = "https://js.paystack.co/v1/inline.js";
                script.onload = () => resolve(true);
                script.onerror = () => resolve(false);
                document.body.appendChild(script);
            });
        };

        loadScript().then((loaded) => {
            if (!loaded) {
                alert("Failed to load Paystack SDK. Please check your internet connection.");
                setLoading(false);
                return;
            }

            const numAmount = Math.max(1, parseFloat(amount) || 10);
            const userEmail = email.trim() || "migiarbitrage@gmail.com";
            const ref = `MIGI_${Date.now()}_${Math.floor(Math.random() * 1000)}`;

            const paystack = new window.PaystackPop();
            paystack.newTransaction({
                key: process.env.NEXT_PUBLIC_PAYSTACK_PUBLIC_KEY || "pk_live_f27b56cb82aab29a7d80331f66393306a10bfa82",
                email: userEmail,
                amount: Math.round(numAmount * 100), // subunits
                currency: "KES",
                channels: ["mobile_money", "card"],
                reference: ref,
                metadata: {
                    custom_fields: [
                        { display_name: "Phone", variable_name: "phone", value: phone },
                        { display_name: "Project", variable_name: "project", value: "MigiArbitrage" }
                    ]
                },
                onSuccess: (tx: any) => {
                    setLoading(false);
                    onClose();
                },
                onCancel: () => {
                    setLoading(false);
                }
            });
        });
    };

    return (
        <div style={{
            position: "fixed",
            inset: 0,
            backgroundColor: "rgba(0, 0, 0, 0.75)",
            backdropFilter: "blur(6px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 10000,
            padding: "16px"
        }} onClick={onClose}>
            <div style={{
                backgroundColor: "var(--color-bg-panel, #0f172a)",
                border: "1px solid var(--color-border, #1e293b)",
                borderRadius: "16px",
                width: "100%",
                maxWidth: "380px",
                padding: "24px",
                color: "#fff",
                boxShadow: "0 20px 40px rgba(0,0,0,0.6)",
                position: "relative"
            }} onClick={(e) => e.stopPropagation()}>
                {/* Close Button */}
                <button onClick={onClose} style={{
                    position: "absolute",
                    top: "16px",
                    right: "16px",
                    background: "none",
                    border: "none",
                    color: "var(--color-text-muted, #94a3b8)",
                    fontSize: "20px",
                    cursor: "pointer"
                }}>✕</button>

                <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "16px" }}>
                    <div style={{
                        width: "40px", height: "40px",
                        borderRadius: "10px",
                        background: "linear-gradient(135deg, rgba(0, 195, 247, 0.2), rgba(16, 185, 129, 0.2))",
                        border: "1px solid rgba(0, 195, 247, 0.4)",
                        display: "flex", alignItems: "center", justifyContent: "center",
                        color: "#00C3F7",
                        boxShadow: "0 0 16px rgba(0, 195, 247, 0.25)"
                    }}>
                        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <rect x="3" y="4" width="18" height="16" rx="3.5" stroke="#00C3F7" strokeWidth="2" fill="rgba(0, 195, 247, 0.1)"/>
                            <path d="M3 9.5H21" stroke="#00C3F7" strokeWidth="1.5" opacity="0.6"/>
                            <circle cx="16.5" cy="14.5" r="2" fill="#10B981" />
                            <path d="M7 14.5H11" stroke="#00C3F7" strokeWidth="1.6" strokeLinecap="round"/>
                        </svg>
                    </div>
                    <div>
                        <h3 style={{ margin: 0, fontSize: "1.1rem", fontWeight: 700 }}>M-PESA / Card Deposit</h3>
                        <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #94a3b8)" }}>Powered by Paystack Live</span>
                    </div>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                    <div>
                        <label style={{ display: "block", fontSize: "0.8rem", color: "var(--color-text-muted, #94a3b8)", marginBottom: "6px" }}>
                            Amount (KES)
                        </label>
                        <div style={{ display: "flex", gap: "8px" }}>
                            {["10", "50", "100"].map((preset) => (
                                <button
                                    key={preset}
                                    type="button"
                                    onClick={() => setAmount(preset)}
                                    style={{
                                        flex: 1,
                                        padding: "6px 0",
                                        borderRadius: "6px",
                                        border: amount === preset ? "1px solid #00f0ff" : "1px solid var(--color-border, #334155)",
                                        backgroundColor: amount === preset ? "rgba(0, 240, 255, 0.15)" : "transparent",
                                        color: amount === preset ? "#00f0ff" : "#fff",
                                        fontSize: "0.85rem",
                                        cursor: "pointer",
                                        fontWeight: 600
                                    }}
                                >
                                    KES {preset}
                                </button>
                            ))}
                        </div>
                        <input
                            type="number"
                            value={amount}
                            onChange={(e) => setAmount(e.target.value)}
                            placeholder="Custom amount"
                            style={{
                                width: "100%",
                                marginTop: "8px",
                                padding: "10px 12px",
                                borderRadius: "8px",
                                border: "1px solid var(--color-border, #334155)",
                                backgroundColor: "rgba(0, 0, 0, 0.3)",
                                color: "#fff",
                                fontSize: "0.9rem",
                                outline: "none",
                                boxSizing: "border-box"
                            }}
                        />
                    </div>

                    <div>
                        <label style={{ display: "block", fontSize: "0.8rem", color: "var(--color-text-muted, #94a3b8)", marginBottom: "6px" }}>
                            M-PESA Phone Number (Optional)
                        </label>
                        <input
                            type="tel"
                            value={phone}
                            onChange={(e) => setPhone(e.target.value)}
                            placeholder="0704893919"
                            style={{
                                width: "100%",
                                padding: "10px 12px",
                                borderRadius: "8px",
                                border: "1px solid var(--color-border, #334155)",
                                backgroundColor: "rgba(0, 0, 0, 0.3)",
                                color: "#fff",
                                fontSize: "0.9rem",
                                outline: "none",
                                boxSizing: "border-box"
                            }}
                        />
                    </div>

                    <button
                        type="button"
                        onClick={handlePay}
                        disabled={loading}
                        style={{
                            marginTop: "8px",
                            padding: "12px",
                            borderRadius: "8px",
                            border: "none",
                            backgroundColor: "#00C3F7",
                            color: "#fff",
                            fontSize: "0.95rem",
                            fontWeight: 700,
                            cursor: loading ? "wait" : "pointer",
                            boxShadow: "0 4px 14px rgba(0, 195, 247, 0.4)",
                            transition: "all 0.2s ease"
                        }}
                    >
                        {loading ? "Launching Checkout..." : `Pay KES ${amount || "0"} via M-PESA`}
                    </button>
                </div>
            </div>
        </div>
    );
}
