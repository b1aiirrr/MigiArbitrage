import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
    title: "MigiArbitrage — Real-Time Crypto Arbitrage Scanner",
    description:
        "Live arbitrage spread detection across Binance, Kraken, and KuCoin with ghost-spread prevention and instant Telegram alerts.",
    keywords: [
        "crypto arbitrage",
        "bitcoin arbitrage",
        "cryptocurrency scanner",
        "real-time trading",
    ],
    icons: {
        icon: "/favicon.svg",
    },
};

export default function RootLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    return (
        <html lang="en">
            <head>
                <link rel="preconnect" href="https://fonts.googleapis.com" />
                <link
                    rel="preconnect"
                    href="https://fonts.gstatic.com"
                    crossOrigin="anonymous"
                />
                <link
                    href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap"
                    rel="stylesheet"
                />
            </head>
            <body>{children}</body>
        </html>
    );
}
