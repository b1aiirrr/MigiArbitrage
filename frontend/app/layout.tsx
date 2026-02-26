import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
    title: "MigiArbitrage — Real-Time Crypto Arbitrage Scanner",
    description:
        "Live arbitrage spread detection across Binance, OKX, KuCoin, MEXC, Bybit, Gate.io, Bitget & Coinbase with P2P KES/USDT, triangular scanning, and instant Telegram alerts. Built in Nairobi, Kenya 🇰🇪",
    keywords: [
        "crypto arbitrage",
        "bitcoin arbitrage",
        "cryptocurrency scanner",
        "real-time trading",
        "P2P trading Kenya",
        "KES USDT",
    ],
    manifest: "/manifest.json",
    icons: {
        icon: "/favicon.svg",
        apple: "/icon-192.png",
    },
    appleWebApp: {
        capable: true,
        statusBarStyle: "black-translucent",
        title: "MigiArb",
    },
    applicationName: "MigiArbitrage",
    other: {
        "mobile-web-app-capable": "yes",
    },
};

export const viewport: Viewport = {
    themeColor: "#00f0ff",
    width: "device-width",
    initialScale: 1,
    maximumScale: 1,
    viewportFit: "cover",
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
            <body>
                {children}
                {/* Service Worker Registration */}
                <script
                    dangerouslySetInnerHTML={{
                        __html: `
                            if ('serviceWorker' in navigator) {
                                window.addEventListener('load', function() {
                                    navigator.serviceWorker.register('/sw.js')
                                        .then(function(reg) {
                                            console.log('[PWA] Service worker registered');
                                        })
                                        .catch(function(err) {
                                            console.log('[PWA] Service worker registration failed:', err);
                                        });
                                });
                            }
                        `,
                    }}
                />
            </body>
        </html>
    );
}
