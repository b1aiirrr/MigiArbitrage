import React from "react";

/**
 * MigiArbitrage SVG Logo
 * A sleek geometric logo featuring neon-cyan financial graph elements
 * with interconnected nodes representing multi-exchange connectivity.
 */
export default function Logo({
    width = 180,
    height = 40,
}: {
    width?: number;
    height?: number;
}) {
    return (
        <svg
            width={width}
            height={height}
            viewBox="0 0 220 44"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            aria-label="MigiArbitrage Logo"
        >
            <defs>
                {/* Neon cyan gradient */}
                <linearGradient id="cyan-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#00f0ff" />
                    <stop offset="100%" stopColor="#007cf0" />
                </linearGradient>
                {/* Glow filter */}
                <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
                    <feGaussianBlur stdDeviation="1.5" result="blur" />
                    <feMerge>
                        <feMergeNode in="blur" />
                        <feMergeNode in="SourceGraphic" />
                    </feMerge>
                </filter>
                {/* Subtle glow for graph */}
                <filter id="graphGlow" x="-30%" y="-30%" width="160%" height="160%">
                    <feGaussianBlur stdDeviation="2" result="blur" />
                    <feMerge>
                        <feMergeNode in="blur" />
                        <feMergeNode in="SourceGraphic" />
                    </feMerge>
                </filter>
            </defs>

            {/* ── Icon: Geometric Graph ── */}
            <g filter="url(#graphGlow)">
                {/* Upward trend line */}
                <polyline
                    points="6,32 13,24 20,28 27,14 34,18 40,8"
                    stroke="url(#cyan-grad)"
                    strokeWidth="2.2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    fill="none"
                />
                {/* Graph nodes */}
                <circle cx="6" cy="32" r="2.5" fill="#00f0ff" opacity="0.8" />
                <circle cx="13" cy="24" r="2" fill="#00f0ff" opacity="0.6" />
                <circle cx="20" cy="28" r="2" fill="#00f0ff" opacity="0.7" />
                <circle cx="27" cy="14" r="2.5" fill="#00f0ff" opacity="0.8" />
                <circle cx="34" cy="18" r="2" fill="#00f0ff" opacity="0.6" />
                <circle cx="40" cy="8" r="3" fill="#00f0ff" />

                {/* Connecting vertical bars (like candlesticks) */}
                <line x1="13" y1="28" x2="13" y2="22" stroke="#00f0ff" strokeWidth="1" opacity="0.3" />
                <line x1="27" y1="18" x2="27" y2="12" stroke="#00f0ff" strokeWidth="1" opacity="0.3" />
                <line x1="34" y1="22" x2="34" y2="16" stroke="#00f0ff" strokeWidth="1" opacity="0.3" />

                {/* Diamond accent at peak */}
                <polygon
                    points="40,4 43,8 40,12 37,8"
                    fill="none"
                    stroke="#00f0ff"
                    strokeWidth="1"
                    opacity="0.5"
                />
            </g>

            {/* ── Text: MigiArbitrage ── */}
            <g filter="url(#glow)">
                {/* "Migi" in bold */}
                <text
                    x="52"
                    y="30"
                    fontFamily="Inter, sans-serif"
                    fontWeight="800"
                    fontSize="22"
                    fill="#f1f5f9"
                    letterSpacing="-0.5"
                >
                    Migi
                </text>
                {/* "Arbitrage" in accent gradient */}
                <text
                    x="98"
                    y="30"
                    fontFamily="Inter, sans-serif"
                    fontWeight="600"
                    fontSize="22"
                    fill="url(#cyan-grad)"
                    letterSpacing="-0.3"
                >
                    Arbitrage
                </text>
            </g>

            {/* Subtle underline accent */}
            <rect
                x="52"
                y="35"
                width="160"
                height="1.5"
                rx="1"
                fill="url(#cyan-grad)"
                opacity="0.2"
            />
        </svg>
    );
}
