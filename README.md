# MigiArbitrage

**Real-Time Cryptocurrency Arbitrage Scanner & Alert System**

A production-ready system that detects price discrepancies across Binance, Kraken, and KuCoin in real-time, calculates true net profit after all fees, validates wallet/network viability (ghost spread prevention), and sends instant Telegram alerts. Includes a premium Next.js dashboard for live monitoring.

> ⚠️ **ALERT-ONLY MODE** — This system does NOT execute any trades. All API keys are strictly read-only.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Ubuntu VPS (1vCPU / 2GB)                 │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐               │
│  │  Binance   │  │  Kraken   │  │  KuCoin   │  ← WebSocket │
│  │  WS Client │  │  WS Client│  │  WS Client│    Streams   │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘               │
│        └───────────────┼───────────────┘                    │
│                  ┌─────▼─────┐                              │
│                  │ OrderBook │  (Memory-efficient,           │
│                  │  Manager  │   __slots__, top 20 levels)  │
│                  └─────┬─────┘                              │
│                  ┌─────▼─────┐                              │
│                  │ Arbitrage │  Net Profit =                │
│                  │  Scanner  │  (Pbid×V)-(Pask×V)-Fees      │
│                  └──┬────┬───┘                              │
│             ┌───────▼┐  ┌▼────────┐                         │
│             │PreFlight│  │Telegram │  → Instant Alerts       │
│             │ Check   │  │ Alerter │                        │
│             └─────────┘  └─────────┘                        │
│                  ┌─────────────┐                            │
│                  │  WS Server  │  → Port 8765               │
│                  │ (Dashboard) │                            │
│                  └──────┬──────┘                            │
└─────────────────────────┼───────────────────────────────────┘
                          │
                    ┌─────▼─────┐
                    │  Vercel   │
                    │ Next.js   │  ← Frontend Dashboard
                    │ Dashboard │
                    └───────────┘
```

---

## Features

- **3 Exchange WebSocket Streams** — Binance, Kraken, KuCoin L2 order book data
- **Net Profit Calculator** — Full fee deduction (maker, taker, withdrawal, network)
- **Ghost Spread Prevention** — Pre-flight wallet status & network congestion checks
- **Telegram Alerts** — Instant, formatted notifications with risk assessment
- **Live Dashboard** — Next.js dark-mode UI with glassmorphism design
- **Memory Optimized** — Runs on 2GB RAM with aggressive GC and `__slots__`
- **Docker Deployed** — Single command deployment with memory limits

---

## Project Structure

```
MigiArbitrage/
├── backend/
│   ├── exchanges/
│   │   ├── __init__.py
│   │   ├── base.py          # Abstract exchange client
│   │   ├── binance.py       # Binance WS + REST
│   │   ├── kraken.py        # Kraken WS v2 + REST
│   │   └── kucoin.py        # KuCoin WS + REST
│   ├── __init__.py
│   ├── config.py            # Configuration & env vars
│   ├── orderbook.py         # Memory-efficient order book
│   ├── scanner.py           # Arbitrage detection engine
│   ├── preflight.py         # Ghost spread prevention
│   ├── alerter.py           # Telegram notifications
│   ├── ws_server.py         # Dashboard WebSocket server
│   ├── main.py              # Orchestrator entry point
│   ├── requirements.txt
│   ├── Dockerfile
│   └── docker-compose.yml
├── frontend/
│   ├── app/
│   │   ├── globals.css      # Design system
│   │   ├── layout.tsx       # Root layout + SEO
│   │   └── page.tsx         # Dashboard page
│   ├── components/
│   │   ├── Logo.tsx         # SVG logo component
│   │   ├── SpreadGrid.tsx   # Live spread data grid
│   │   └── AlertLog.tsx     # Historical alerts table
│   ├── hooks/
│   │   └── useArbitrageStream.ts  # WebSocket hook
│   ├── public/
│   │   └── favicon.svg
│   ├── package.json
│   ├── tsconfig.json
│   ├── next.config.js
│   └── next-env.d.ts
├── .env.example
├── .gitignore
└── README.md
```

---

## Deployment Guide

### Prerequisites

- An Ubuntu VPS with Docker installed (1 vCPU, 2 GB RAM minimum)
- A Telegram Bot (create via [@BotFather](https://t.me/BotFather))
- Read-only API keys from Binance, Kraken, and KuCoin
- A [Vercel](https://vercel.com) account for frontend hosting
- Git and a GitHub account

---

### Step 1: Server Setup (Ubuntu VPS)

SSH into your server:

```bash
ssh root@your_server_ip
```

Install Docker (if not already installed):

```bash
# Update packages
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sh

# Install Docker Compose
sudo apt install docker-compose-plugin -y

# Verify installation
docker --version
docker compose version
```

---

### Step 2: Clone the Repository

```bash
# Clone the repo
cd /opt
git clone https://github.com/your-username/MigiArbitrage.git
cd MigiArbitrage
```

---

### Step 3: Configure Environment Variables

```bash
# Copy the example env file
cp .env.example .env

# Edit with your credentials
nano .env
```

Fill in all required values:
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `TELEGRAM_CHAT_ID` — your chat/group ID (use @userinfobot)
- Exchange API keys (READ-ONLY permissions only!)

---

### Step 4: Build & Start the Backend

```bash
cd backend

# Build the Docker image
docker compose build

# Start in detached mode
docker compose up -d

# Verify it's running
docker compose ps
docker compose logs -f --tail=50
```

The scanner will:
1. Connect to all 3 exchange WebSocket streams
2. Start scanning for arbitrage opportunities every 500ms
3. Send a startup message to your Telegram
4. Start the dashboard WS server on port `8765`

---

### Step 5: Open Firewall Port

```bash
# Allow WebSocket traffic from Vercel to your server
sudo ufw allow 8765/tcp
sudo ufw enable
```

---

### Step 6: Deploy Frontend to Vercel

On your **local machine**:

```bash
# Install Vercel CLI
npm install -g vercel

# Navigate to the frontend
cd frontend

# Install dependencies
npm install

# Test locally (optional)
npm run dev

# Deploy to Vercel
vercel

# Follow the prompts:
# - Set up and deploy? Yes
# - Which scope? (select your account)
# - Link to existing project? No
# - Project name: migi-arbitrage
# - Directory: ./
# - Override settings? No
```

After deployment, set the environment variable in Vercel:

```bash
# Set your backend WS URL
vercel env add NEXT_PUBLIC_WS_URL
# Enter: ws://your_server_ip:8765
# Select: Production, Preview, Development
```

Then redeploy:

```bash
vercel --prod
```

---

### Step 7: Push to GitHub

```bash
# Initialize git (from the project root)
cd /path/to/MigiArbitrage
git init
git add .
git commit -m "feat: initial MigiArbitrage release"

# Create repo on GitHub, then:
git remote add origin https://github.com/your-username/MigiArbitrage.git
git branch -M main
git push -u origin main
```

---

## Managing the Backend

```bash
# View live logs
docker compose logs -f

# Restart the scanner
docker compose restart

# Stop the scanner
docker compose down

# Update after code changes
git pull
docker compose build && docker compose up -d
```

---

## Configuration Reference

| Variable | Description | Default |
|----------|------------|---------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token | — |
| `TELEGRAM_CHAT_ID` | Target chat/group ID | — |
| `BINANCE_API_KEY` | Binance read-only API key | — |
| `KRAKEN_API_KEY` | Kraken read-only API key | — |
| `KUCOIN_API_KEY` | KuCoin read-only API key | — |
| `MIN_NET_PROFIT_USD` | Minimum profit to alert (USD) | `1.00` |
| `SCAN_INTERVAL_MS` | Scanner loop interval (ms) | `500` |
| `WS_SERVER_PORT` | Dashboard WS server port | `8765` |
| `NEXT_PUBLIC_WS_URL` | Backend WS URL for frontend | `ws://localhost:8765` |

---

## Security

- **No trade execution code exists** — the system is structurally alert-only
- All API keys must be **read-only** with zero trading permissions
- The Docker container runs as a **non-root user**
- WebSocket server is read-only (no client commands accepted)
- Environment variables are never committed (`.env` in `.gitignore`)

---

## License

Private — All rights reserved.
