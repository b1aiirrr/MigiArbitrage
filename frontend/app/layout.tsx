import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
    metadataBase: new URL("https://migiarbitrage.vercel.app"),
    title: {
        default: "MigiArbitrage — Spot The Gap. Seize The Spread.",
        template: "%s | MigiArbitrage"
    },
    description:
        "MigiArbitrage: The #1 Real-Time Crypto Arbitrage Scanner in Kenya. Spot the gap and seize the spread across Binance, OKX, Bybit, and more. Featuring M-Pesa P2P KES/USDT detection and triangular scanning.",
    keywords: [
        "crypto arbitrage Kenya",
        "M-Pesa crypto arbitrage",
        "bitcoin arbitrage Nairobi",
        "KES USDT arbitrage",
        "real-time crypto scanner",
        "triangular arbitrage bot",
        "Binance P2P Kenya",
        "crypto trading signals Kenya",
        "automated arbitrage scanner",
        "MigiArbitrage"
    ],
    alternates: {
        canonical: "https://migiarbitrage.vercel.app",
    },
    openGraph: {
        type: "website",
        locale: "en_KE",
        url: "https://migiarbitrage.vercel.app",
        siteName: "MigiArbitrage",
        title: "MigiArbitrage — Real-Time Crypto Arbitrage Scanner",
        description: "Live cross-exchange arbitrage spreads across Binance, OKX, Bybit, KuCoin, MEXC, Gate.io & Bitget. Sub-second WebSocket streaming and M-PESA KES/USDT P2P spatial detection.",
        images: [
            {
                url: "/icon-512.png",
                width: 512,
                height: 512,
                alt: "MigiArbitrage Crypto Scanner",
            },
        ],
    },
    twitter: {
        card: "summary_large_image",
        title: "MigiArbitrage — Real-Time Crypto Arbitrage Scanner",
        description: "Sub-second crypto arbitrage scanner across 8+ major exchanges with M-PESA P2P spread detection.",
        images: ["/icon-512.png"],
    },
    robots: {
        index: true,
        follow: true,
        googleBot: {
            index: true,
            follow: true,
            "max-video-preview": -1,
            "max-image-preview": "large",
            "max-snippet": -1,
        },
    },
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
    themeColor: "#0a0e17",
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
    const jsonLd = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "MigiArbitrage",
        "operatingSystem": "All (Web, Android, iOS)",
        "applicationCategory": "FinanceApplication",
        "description": "High-frequency cryptocurrency arbitrage scanner monitoring live spot and P2P spreads across Binance, Bybit, OKX, KuCoin, Gate.io, and Bitget.",
        "url": "https://migiarbitrage.vercel.app",
        "offers": {
            "@type": "Offer",
            "price": "0.00",
            "priceCurrency": "USD"
        },
        "creator": {
            "@type": "Organization",
            "name": "MigiArbitrage",
            "email": "migiarbitrage@gmail.com",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": "Nairobi",
                "addressCountry": "KE"
            }
        }
    };

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
                <script
                    type="application/ld+json"
                    dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
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
