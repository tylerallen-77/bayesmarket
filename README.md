<div align="center">

<br>

```
██████╗  █████╗ ██╗   ██╗███████╗███████╗
██╔══██╗██╔══██╗╚██╗ ██╔╝██╔════╝██╔════╝
██████╔╝███████║ ╚████╔╝ █████╗  ███████╗
██╔══██╗██╔══██║  ╚██╔╝  ██╔══╝  ╚════██║
██████╔╝██║  ██║   ██║   ███████╗███████║
╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚══════╝╚══════╝
              M A R K E T
```

### Automated BTC-PERP Trading Engine for Hyperliquid

<br>

<img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
<img src="https://img.shields.io/badge/Hyperliquid-Mainnet%20%7C%20Testnet-6C5CE7?style=for-the-badge" />
<img src="https://img.shields.io/badge/Mode-Shadow%20%7C%20Live-FFA502?style=for-the-badge" />
<img src="https://img.shields.io/badge/Deploy-Local%20%7C%20Railway%20%7C%20VPS-2ED573?style=for-the-badge" />

<br>

**Accuracy over speed.** Rejects ~85% of signals through multi-layer conviction filters.<br>
When it acts, conviction is high.

<br>

[Quick Start](#-quick-start) &nbsp;&bull;&nbsp; [Telegram Bot](#-telegram-bot) &nbsp;&bull;&nbsp; [How It Works](#-how-it-works) &nbsp;&bull;&nbsp; [Deployment](#-deployment) &nbsp;&bull;&nbsp; [Configuration](#-configuration)

<br>

</div>

---

<br>

## Overview

BayesMarket is an automated perpetual futures trading engine designed for **Hyperliquid BTC-PERP**. It runs in **shadow mode** by default — computing real-time signals from live mainnet data, simulating trades, and logging everything to SQLite — without placing actual orders or requiring any credentials.

<br>

<div align="center">

```
                       ┌─────────────────────────────┐
                       │      LIVE MARKET DATA        │
                       └──────────────┬──────────────-┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              │                       │                       │
    Hyperliquid WS            Binance Futures        Synthetic Klines
    ├─ l2Book (50lvl)         (primary klines)       (internal tracking)
    └─ trades (BTC/CVD)              │
              │                       │
              └───────────┬───────────┘
                          ▼
    ┌─────────────────────────────────────────────────────────┐
    │               CASCADE MTF ENGINE                        │
    │                                                         │
    │   ┌──────┐   ┌──────┐   ┌──────┐   ┌──────┐          │
    │   │  4h  │──▶│  1h  │──▶│ 15m  │──▶│  5m  │          │
    │   │ BIAS │   │  CTX │   │ ZONE │   │ TRIG │          │
    │   └──────┘   └──────┘   └──────┘   └──┬───┘          │
    │                                        │               │
    └────────────────────────────────────────┼───────────────┘
                                             ▼
    ┌─────────────────────────────────────────────────────────┐
    │  RISK ENGINE   2% risk │ 5x lev │ 7% DD │ Funding     │
    └────────────────────────────┬────────────────────────────┘
                                 ▼
    ┌─────────────────────────────────────────────────────────┐
    │  EXECUTION     Shadow: simulate @ mid                   │
    │                Live: Limit ALO entry │ Stop Market SL   │
    └────────────────────────┬────────────────────────────────┘
                             │
                   ┌─────────┼─────────┐
                   ▼         ▼         ▼
            ┌──────────┐ ┌────────┐ ┌──────────┐
            │ Terminal  │ │  Web   │ │ Telegram │
            │ Dashboard │ │ Dash   │ │ Control  │
            │ (local)   │ │(Railway│ │  Panel   │
            └──────────┘ └────────┘ └──────────┘
```

</div>

<br>

## Key Features

<table>
<tr>
<td width="50%">

### Signal Engine
- **9 Proportional Indicators** — zero binary signals
- **Cascade MTF** — 4h BIAS > 1h CTX > 15m ZONE > 5m TRIGGER
- **Regime Detection** — trending vs ranging adaptive thresholds
- **Binance Futures Klines** — primary source, high-volume reference price

</td>
<td width="50%">

### Execution & Risk
- **3-Layer SL** — Wall > POC > ATR with structural-only tightening
- **Trailing Stop** — ATR-based trail activates after TP1 hit
- **Regime-Adaptive TP** — trending: partial TP1 + trailing, ranging: 100% TP1
- **Configurable Risk** — risk%, leverage, TP split, daily limit via Telegram
- **Cooldown FSM** — 3-loss cooldown, full stop, auto-recovery

</td>
</tr>
<tr>
<td width="50%">

### Monitoring & Analysis
- **Rich Terminal** — 4-panel live dashboard (local/VPS)
- **Web Dashboard** — browser-based SSE dashboard with equity curve
- **Telegram Bot** — 16+ commands, inline keyboards
- **Loss Analysis** — 7-category auto-classification
- **Correlation Tracker** — pairwise indicator independence tracking
- **Backtest** — replay signals from DB for parameter validation

</td>
<td width="50%">

### Deployment & Safety
- **Shadow Mode** — no credentials needed, simulate everything
- **Position Reconciliation** — restore orphaned exchange positions on restart
- **Testnet** — real orders with mock USDC
- **Railway PaaS** — one-click cloud deploy
- **72 Unit Tests** — scoring, position, sizing, risk, runtime config

</td>
</tr>
</table>

<br>

---

<br>

## Quick Start

```bash
git clone https://github.com/tylerallen-77/bayesmarket.git
cd bayesmarket
pip install -r bayesmarket/requirements.txt
python -m bayesmarket
```

That's it for shadow mode — no API keys, no `.env` file required.

<br>

---

<br>

## Local Setup (Detailed)

Step-by-step guide to run BayesMarket on your local machine (Windows, macOS, or Linux).

### Step 1: Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Python 3.11+** | Check with `python --version` |
| **pip** | Comes with Python. Update: `python -m pip install --upgrade pip` |
| **Internet** | Needs access to `api.hyperliquid.xyz` (WS) and `fapi.binance.com` (REST + WS) |
| **Git** | For cloning the repo |

> **No API keys needed for shadow mode.** All data feeds (l2Book, trades, klines) use public endpoints.

### Step 2: Clone & Install

```bash
git clone https://github.com/tylerallen-77/bayesmarket.git
cd bayesmarket
pip install -r bayesmarket/requirements.txt
```

<details>
<summary><b>Windows with corporate proxy / Zscaler?</b></summary>

If `pip install` fails with SSL certificate errors:

```bash
pip install --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -r bayesmarket/requirements.txt
```

If WebSocket connections fail at runtime, you may need to configure your proxy to allow:
- `wss://api.hyperliquid.xyz/ws`
- `wss://fstream.binance.com/stream`
- `https://fapi.binance.com/fapi/v1`

</details>

### Step 3: Environment Configuration (Optional)

Shadow mode works out of the box with zero config. For customization, create a `.env` file:

```bash
cp bayesmarket/.env.example .env
```

**Shadow mode (default)** — no changes needed:

```env
LIVE_MODE=false
SIMULATED_CAPITAL=1000.0
DEPLOYMENT_ENV=local
```

**With Telegram control panel** (recommended):

```env
LIVE_MODE=false
SIMULATED_CAPITAL=1000.0
DEPLOYMENT_ENV=local

# Get token from @BotFather, chat ID from @userinfobot
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

**With web dashboard** (browser-based alternative to terminal):

```env
WEB_DASHBOARD=true
PORT=8080
```

### Step 4: Run

```bash
python -m bayesmarket
```

On first launch, a startup wizard guides you through configuration:

```
  ╔══════════════════════════════════════════════════╗
  ║   BAYESMARKET — Startup Wizard                   ║
  ╠══════════════════════════════════════════════════╣
  ║  STEP 1   Operating Mode  (shadow/testnet/live)  ║
  ║  STEP 2   Credentials     (testnet/live only)    ║
  ║  STEP 3   Telegram Bot    (optional)             ║
  ║  STEP 4   Parameters      (thresholds & risk)    ║
  ║  STEP 5   Database        (SQLite path)          ║
  ║  REVIEW   Confirm & save to .env                 ║
  ╚══════════════════════════════════════════════════╝
```

| Platform | Wizard | Notes |
|----------|--------|-------|
| **Local / VPS** | Terminal prompts | Interactive stdin with ANSI colors |
| **Railway** | Telegram `/setup` | Inline buttons, no TTY needed |
| **Existing .env** | Skip option | Launch immediately with saved config |

If the wizard crashes on Windows (Unicode encoding), add a `.env` file manually (Step 3) and the wizard will offer to skip.

### Step 5: Verify Startup

After launching, you should see these logs in order:

```
[info] system_starting          mode=SHADOW capital=1000.0 coin=BTC
[info] bootstrapping_klines
[info] bootstrap_klines_loaded  tf=5m   interval=1m  count=150
[info] bootstrap_klines_loaded  tf=15m  interval=5m  count=150
[info] bootstrap_klines_loaded  tf=1h   interval=15m count=100
[info] bootstrap_klines_loaded  tf=4h   interval=1h  count=100
[info] all_tasks_launching      count=15
[info] hl_book_feed_connected   levels=50 sig_figs=5
[info] hl_trade_feed_connected
[info] binance_kline_feed_connected
```

**What connects where:**

| Feed | Source | Data | Purpose |
|------|--------|------|---------|
| Bootstrap klines | Binance Futures REST | Historical OHLCV | RSI, MACD, EMA, HA, VWAP, POC, ATR |
| Kline stream | Binance Futures WS | Live OHLCV updates | Continuous indicator updates |
| l2Book | Hyperliquid WS | 50-level orderbook | OBI, Depth, Wall detection |
| Trades | Hyperliquid WS | BTC trade stream | CVD calculation |
| Funding | Hyperliquid REST | Funding rate | Funding filter (every 60s) |

### Step 6: Reading the Dashboard

After ~5 seconds, the Rich terminal dashboard appears with 4 panels:

```
┌─── 5m TRIGGER ───────────┐┌─── 15m TIMING ──────────┐
│ CVD:  +1.23  OBI:  +0.45 ││ CVD:  +0.98  OBI:  +0.32│
│ VWAP: +0.80  POC:  -0.20 ││ VWAP: +0.60  POC:  +0.15│
│ RSI:  +0.35  MACD: +0.12 ││ RSI:  +0.28  MACD: +0.08│
│ TOTAL: +5.2  SIGNAL: --- ││ TOTAL: +4.8  ZONE: NONE │
├─── 1h CONTEXT ───────────┤├─── 4h BIAS ─────────────┤
│ FILTER TF                ││ FILTER TF                │
│ VWAP: +0.90  TOTAL: +3.1 ││ VWAP: +1.10  TOTAL: +4.5│
│ CTX: CONFIRMED    REGIME ││ BIAS: LONG       REGIME  │
└──────────────────────────┘└──────────────────────────┘
 POS: FLAT | PnL: $0.00 | RISK: NORMAL | SRC: binance_futures
```

- **RSI/MACD/EMA should show values** (not `---`) in all 4 TFs after bootstrap
- **Walls** appear as `Bid Wall $XX,XXX` or `Ask Wall $XX,XXX` when detected
- **Kline source** in bottom bar should show `binance_futures`

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `bootstrap_klines_all_failed` | Binance unreachable (corporate firewall) | Use VPN or deploy to Railway. Indicators will be empty until synthetic klines build up (~30 min) |
| `hl_book_feed_zero_messages` | HL WebSocket subscription rejected | Check internet access to `api.hyperliquid.xyz`. Check if VPN blocks WebSocket |
| RSI/MACD/EMA show `---` | Not enough klines (bootstrap failed) | Ensure Binance Futures REST is accessible. Check logs for `bootstrap_klines_loaded` |
| `UnicodeEncodeError` on startup | Windows terminal encoding (cp1252) | Create `.env` manually, wizard will skip. Or set `PYTHONIOENCODING=utf-8` |
| Scores never reach threshold | Normal in low-volatility markets | Wait for active trading hours. Check `/scores` via Telegram |
| `binance_kline_feed_disconnected` | Binance WS blocked | System falls back to synthetic klines from HL trades. Indicators still work but may be slower to populate for higher TFs |
| Memory growing unbounded | Trade deque not pruning | Check `TRADE_TTL_SECONDS` in config (default 600s). Restart if needed |

<br>

---

<br>

## How It Works

### Cascade MTF Architecture

BayesMarket uses a **top-down cascade** where each timeframe plays a specific role. Only the 5m timeframe executes trades — higher TFs act as progressive filters.

```
  4h ━━ BIAS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  │    Score > ±3.0 → sets allowed direction (LONG / SHORT / BOTH)
  │
  1h ━━ CONTEXT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  │    Score same sign as 4h → context confirmed
  │    Mismatch → 5m BLOCKED
  │
  15m ━━ TIMING ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  │    Score > threshold & matches bias → entry zone ACTIVE (15min TTL)
  │    Zone expired or inactive → 5m BLOCKED
  │
  5m ━━ TRIGGER ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
       Score > ±7.0 (trending) / ±9.0 (ranging)
       Direction matches zone + bias + risk gates → EXECUTE
```

### 9 Proportional Indicators

All indicators output **graduated scores** — no binary signals. Composite range: **-13.5 to +13.5**.

<details>
<summary><b>Category A: Order Flow (Leading)</b> — max ±6.0</summary>

| Indicator | Formula | Range |
|-----------|---------|-------|
| **CVD** | `z = (cvd - mean) / std` &rarr; `2.0 * tanh(z / 2.0)` | ±2.0 |
| **OBI** | `((bid_vol - ask_vol) / total) * 2.0` | ±2.0 |
| **Depth** | `((bid_depth - ask_depth) / total) * 2.0` | ±2.0 |

</details>

<details>
<summary><b>Category B: Structure (Equilibrium)</b> — max ±4.5</summary>

| Indicator | Formula | Range |
|-----------|---------|-------|
| **VWAP** | `clamp((price - vwap) / vwap * 20, -1.5, +1.5)` | ±1.5 |
| **POC** | `clamp((price - poc) / poc * 20, -1.5, +1.5)` | ±1.5 |
| **Heikin Ashi** | `(streak / 5) * 1.5` where streak in [-5, +5] | ±1.5 |

</details>

<details>
<summary><b>Category C: Momentum (Lagging)</b> — max ±3.0</summary>

| Indicator | Formula | Range |
|-----------|---------|-------|
| **RSI** | Linear map: 30&rarr;+1, 50&rarr;0, 70&rarr;-1 | ±1.0 |
| **MACD** | `clamp(histogram / ATR, -1.0, +1.0)` | ±1.0 |
| **EMA** | `clamp((ema5 - ema20) / ema20 * 200, -1.0, +1.0)` | ±1.0 |

</details>

### Cascade Signal Thresholds

| Role | Regime | Threshold | Action |
|------|--------|-----------|--------|
| 4h **BIAS** | Any | ±3.0 | Set allowed direction |
| 1h **CONTEXT** | Any | Same sign as 4h | Confirm or block |
| 15m **TIMING** | Any | Role threshold | Establish zone (5min TTL) |
| 5m **TRIGGER** | Trending | ±7.0 | Generate trade signal |
| 5m **TRIGGER** | Ranging | ±9.0 | Higher bar for noisy markets |

<br>

---

<br>

## Risk Management

<table>
<tr>
<td width="50%">

### Position Sizing

```python
risk = capital * risk_pct    # configurable 1-5% (default 2%)
sl_dist = abs(entry - sl)    # e.g., $450
size = risk / sl_dist        # e.g., 0.044 BTC

# Modifiers
if cooldown:  size *= 0.5    # Half size
if funding:   size *= 0.75   # Caution tier

# Hard cap (configurable 1-10x, default 5x)
size = min(size, capital * max_leverage / price)
```

</td>
<td width="50%">

### Cooldown State Machine

```
NORMAL
  │ 3 consecutive losses
  ▼
COOLDOWN (50% size)
  │ 2 wins OR 1h elapsed → NORMAL
  │ 3 more losses
  ▼
FULL STOP (no trading)
  │ 4h elapsed → NORMAL
```

</td>
</tr>
</table>

### Stop Loss: 3-Layer Fallback

| Priority | Source | Method |
|:--------:|--------|--------|
| 1 | **Wall** | Nearest bid/ask wall (binned $20, survived 3s) + 0.05% offset |
| 2 | **POC** | Volume Profile point of control + 0.1% offset |
| 3 | **ATR** | 1.5x ATR(14) from entry price |
| Guard | **Ratio** | Cap SL if > 3x TP1 distance |
| Emergency | **Pct** | 3% max distance cap |

> **After entry:** SL only tightens on structural swing shifts. Never chases new walls. Min distance: 0.3x ATR.
> **After TP1 (trending):** Trailing stop activates — trails behind best price, locks in profit.
> **After TP1 (ranging):** 100% exit at TP1 — no trailing, immediately ready for next signal.

### Daily Protection

| Rule | Default | Configurable | Action |
|------|:-------:|:------------:|--------|
| Daily loss limit | 7% | 3-15% via `/set` | 12-hour pause |
| Risk per trade | 2% | 1-5% via `/set` | Per-entry risk cap |
| Max leverage | 5x | 1-10x via `/set` | Hard leverage cap |
| Reset | 00:00 UTC | — | Daily counter reset |
| Capital | Auto-compound | — | Shadow mode PnL compounds |

<br>

---

<br>

## Telegram Bot

Full remote control panel with 16+ commands and inline keyboards.

### Setup

```bash
# 1. @BotFather → /newbot → copy token
# 2. @userinfobot → /start → copy chat ID
# 3. Add to .env:
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

### Commands

| Command | Description |
|:--------|:------------|
| `/start` | Main menu with inline buttons |
| `/setup` | Interactive setup wizard (mode, thresholds, credentials) |
| `/status` | Full status — position, PnL, risk state |
| `/scores` | Live cascade scores (4h &rarr; 1h &rarr; 15m &rarr; 5m) |
| `/report [1d\|7d\|30d\|all]` | Performance report |
| `/dashboard` | Pull: snapshot now |
| `/dashboard auto` | Push: live ticker updated every 30s |
| `/dashboard off` | Stop auto-push |
| `/analysis [period]` | Loss pattern analysis (7 categories) |
| `/mode` | View / switch mode |
| `/live` &bull; `/shadow` | Switch trading mode |
| `/pause [reason]` &bull; `/resume` | Pause / resume trading |
| `/close` | Force close open position |
| `/config` | View active configuration |
| `/set <param> <value>` | Hot-reload parameters |
| `/help` | Command reference |

### Push Dashboard

Auto-updating ASCII dashboard (edits one message every 30s):

```
METRIC          | VALUE       | STATUS
────────────────|─────────────|──────────
PRICE           | $84,250.0   | -
SCORE 5m        | +8.3 ▓▓▓▓░ | ▲ LONG
SCORE 15m       | +6.1 ▓▓▓░░ | - NEUTRAL
CASCADE         | LONG        | CTX:Y Z:LONG
POSITION        | LONG        | OPEN
UNREALIZED      | +$22.50     | +0.27%
RISK STATE      | NORMAL      | W:3 L:0
```

<br>

---

<br>

## Deployment

| Mode | `DEPLOYMENT_ENV` | Dashboard | Monitoring | Wizard |
|:-----|:-----------------|:----------|:-----------|:-------|
| **Local** | `local` | Rich terminal | Terminal + Telegram | Terminal prompts |
| **Railway** | `railway` | Web dashboard (auto) | Web + Telegram | `/setup` command |
| **VPS** | `vps` | Rich terminal (+ web opt-in) | Terminal + Telegram | Terminal prompts |

> **Web Dashboard:** On Railway, a browser-based dashboard is served automatically on the `PORT` assigned by Railway. On local/VPS, enable it with `WEB_DASHBOARD=true` in `.env`. Access at `http://localhost:8080` (or your Railway public URL).

### Deploy to Railway (Recommended for 24/7)

Railway is a PaaS that runs your bot in the cloud. No server management needed.
BayesMarket includes all Railway config files (`Procfile`, `railway.toml`, `Dockerfile`).

#### Step 1: Create Telegram Bot

You need a Telegram bot to control BayesMarket on Railway (no terminal available).

```
1. Open Telegram → search @BotFather
2. Send /newbot
3. Follow the prompts → choose a name (e.g., "BayesMarket Bot")
4. BotFather replies with a token like: 7123456789:AAH...xyz
   → Save this as TELEGRAM_BOT_TOKEN

5. Open Telegram → search @userinfobot
6. Send /start → it replies with your numeric user ID (e.g., 123456789)
   → Save this as TELEGRAM_CHAT_ID
```

#### Step 2: Create Railway Project

```
1. Go to https://railway.com → Sign up / Log in (GitHub OAuth recommended)
2. Click "New Project" → "Deploy from GitHub repo"
3. Select your fork of tylerallen-77/bayesmarket
   (or connect the repo if not listed)
4. Railway auto-detects nixpacks.toml and starts building
```

#### Step 3: Set Environment Variables

Go to **Railway Dashboard → your service → Variables tab** and add:

```env
# Required
DEPLOYMENT_ENV=railway
TELEGRAM_BOT_TOKEN=7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789

# Optional (defaults are fine for shadow mode)
LIVE_MODE=false
SIMULATED_CAPITAL=1000.0
COIN=BTC
BINANCE_SYMBOL=BTCUSDT
DB_PATH=/app/data/bayesmarket.db
HL_REST_URL=https://api.hyperliquid.xyz
HL_WS_URL=wss://api.hyperliquid.xyz/ws

# Live/Testnet only (leave empty for shadow mode)
HL_PRIVATE_KEY=
HL_ACCOUNT_ADDRESS=
```

#### Step 4: Add Persistent Volume

Without a volume, SQLite data is lost on every redeploy.

```
1. Railway Dashboard → your service → "Volumes" tab
2. Click "New Volume"
3. Mount path: /app/data
4. Size: 1 GB (more than enough)
5. Click "Create"
```

This stores `bayesmarket.db` persistently at `/app/data/bayesmarket.db`.

#### Step 5: Enable Public URL (Web Dashboard)

```
1. Railway Dashboard → your service → "Settings" tab
2. Under "Networking" → click "Generate Domain"
3. Railway assigns a public URL like: bayesmarket-production-xxxx.up.railway.app
4. Open that URL in your browser → live web dashboard with real-time updates
5. The dashboard auto-refreshes every 3 seconds via SSE (Server-Sent Events)
```

> The web dashboard has 3 tabs:
> - **Dashboard** — live 4-panel scores (SSE, 3s auto-refresh), position, risk state, cascade info
> - **Config** — read-only deployment status (shadow/testnet/live), scoring, risk, TP strategy
> - **Trades** — equity curve chart, summary stats, recent trade history table

#### Step 6: Deploy & Verify

```
1. Railway auto-deploys on push. Check "Deployments" tab for build logs.
2. Once running, open your Railway public URL → web dashboard should load
3. Open Telegram → send /start to your bot
4. Bot should reply with the main menu
5. Send /status to verify it's connected to Hyperliquid
6. Send /scores to see live cascade scores
7. Send /dashboard auto to enable Telegram push updates every 30s
```

#### Railway Telegram Commands Cheatsheet

Once deployed, control everything via Telegram:

```
/setup              → Setup wizard (mode, thresholds, risk params)
/status             → Position, PnL, capital, risk state
/scores             → All 4 TF cascade scores
/config             → View all active parameters
/set <param> <val>  → Change any parameter live (no redeploy)
/report 7d          → Performance report
/dashboard auto     → Enable live push dashboard
/analysis 7d        → Loss pattern analysis
/live               → Switch to live mode (requires HL credentials)
/shadow             → Switch back to shadow mode
/pause / /resume    → Pause/resume trading
/close              → Force close open position
```

#### Switching to Live Mode on Railway

```
1. Railway Dashboard → Variables → set:
   LIVE_MODE=true
   HL_PRIVATE_KEY=<your API wallet private key>
   HL_ACCOUNT_ADDRESS=<your main wallet address>

2. Or via Telegram: send /live and confirm

Recommended: test on testnet first. Set these variables:
   HL_REST_URL=https://api.hyperliquid-testnet.xyz
   HL_WS_URL=wss://api.hyperliquid-testnet.xyz/ws
   Get mock USDC at: https://app.hyperliquid-testnet.xyz/drip
```

#### Troubleshooting Railway

| Issue | Solution |
|-------|----------|
| Bot not responding | Check Railway logs for errors. Verify `TELEGRAM_BOT_TOKEN` is correct. |
| Data lost on redeploy | Add a volume mounted at `/app/data` (Step 4). |
| Build fails | Check `requirements.txt` for version conflicts in Railway build logs. |
| Memory limit | Railway free tier has 512MB. BayesMarket uses ~100-150MB. |
| Bot starts but no trades | Normal — system needs 5-15 min to build klines before signals generate. |

<details>
<summary><b>VPS (Contabo / Oracle / any Linux server)</b></summary>

```bash
cd bayesmarket/deploy
chmod +x setup.sh
./setup.sh
```

See `bayesmarket/deploy/VPS_GUIDE.md` for detailed instructions.

</details>

<details>
<summary><b>Testnet</b></summary>

Switch to Hyperliquid testnet with env vars only — no code changes:

```bash
LIVE_MODE=true
HL_REST_URL=https://api.hyperliquid-testnet.xyz
HL_WS_URL=wss://api.hyperliquid-testnet.xyz/ws
HL_PRIVATE_KEY=<testnet API wallet key>
HL_ACCOUNT_ADDRESS=<testnet main wallet address>
```

Get mock USDC at https://app.hyperliquid-testnet.xyz/drip

</details>

<br>

---

<br>

## Loss Trade Analysis

When a trade closes with a loss, BayesMarket auto-classifies the failure:

| Category | Severity | Description |
|----------|:--------:|-------------|
| `stale_poc_sl` | `CRIT` | SL based on POC >1% from entry |
| `poor_rr_entry` | `CRIT` | Risk/reward ratio < 0.5 |
| `time_overheld` | `CRIT` | Held >2x the time exit limit |
| `trend_reversal` | `MED` | Score flipped direction during hold |
| `choppy_market` | `MED` | Borderline score in ranging market |
| `cascade_misaligned` | `MED` | Cascade bias/context was weak at entry |
| `normal_sl` | `LOW` | Clean SL hit, no anomaly |

Access via Telegram `/analysis` or stored in the `trades` table.

<br>

---

<br>

## Configuration

### Static Config (`config.py`)

| Parameter | Default | Description |
|-----------|:-------:|-------------|
| `LIVE_MODE` | `false` | Shadow mode (no orders) |
| `SIMULATED_CAPITAL` | `$1,000` | Starting simulation capital |
| `MAX_LEVERAGE` | `5x` | Hard leverage cap |
| `MAX_RISK_PER_TRADE` | `2%` | Risk per trade |
| `DAILY_LOSS_LIMIT` | `7%` | Daily drawdown breaker |
| `CASCADE_BIAS_THRESHOLD` | `3.0` | 4h bias direction threshold |
| `CASCADE_TIMING_ZONE_TTL` | `900s` | 15m zone time-to-live |
| `MAX_SL_TP_RATIO` | `3.0` | SL/TP distance cap |
| `WALL_BIN_SIZE` | `$20` | Price binning for walls |
| `KLINE_SOURCE` | `binance_futures` | Primary kline source |

### Runtime Hot-Reload (Telegram `/set`)

All parameters below can be changed live via Telegram — no restart required.

**Scoring:**

| Parameter | Range | Default | Command |
|-----------|:-----:|:-------:|---------|
| `threshold_5m` | 1.0 - 15.0 | 7.0 | `/set threshold_5m 8.0` |
| `bias_threshold` | 1.0 - 10.0 | 3.0 | `/set bias_threshold 4.0` |
| `vwap_sensitivity` | 1.0 - 500.0 | 20.0 | `/set vwap_sensitivity 30` |
| `poc_sensitivity` | 1.0 - 500.0 | 20.0 | `/set poc_sensitivity 30` |

**Risk:**

| Parameter | Range | Default | Command |
|-----------|:-----:|:-------:|---------|
| `risk_per_trade` | 0.01 - 0.05 | 0.02 (2%) | `/set risk_per_trade 0.03` |
| `max_leverage` | 1.0 - 10.0 | 5.0 | `/set max_leverage 3` |
| `daily_loss_limit` | 0.03 - 0.15 | 0.07 (7%) | `/set daily_loss_limit 0.05` |

**TP Strategy:**

| Parameter | Range | Default | Command |
|-----------|:-----:|:-------:|---------|
| `tp1_size` | 0.3 - 1.0 | 0.60 (60%) | `/set tp1_size 0.8` |
| `trailing_stop` | on / off | on | `/set trailing_stop off` |
| `trail_distance` | 0.3 - 2.0 | 0.75 ATR | `/set trail_distance 1.0` |
| `tp_adaptive` | on / off | on | `/set tp_adaptive off` |

<br>

---

<br>

## Project Structure

<details>
<summary><b>Click to expand full tree</b></summary>

```
bayesmarket/
├── config.py              # All configurable constants
├── main.py                # Entry point — async orchestration (15 tasks)
├── runtime.py             # Mutable RuntimeConfig (hot-reload via Telegram)
├── startup.py             # Interactive startup wizard (terminal + Telegram)
├── __main__.py            # python -m bayesmarket support
│
├── feeds/
│   ├── hyperliquid.py     # HL WebSocket: l2Book, trades + wall tracker
│   ├── binance.py         # Binance FUTURES WebSocket + REST (primary klines)
│   └── synthetic.py       # Synthetic kline builder from HL trades (internal tracking)
│
├── indicators/
│   ├── order_flow.py      # CVD (Z-Score + tanh), OBI, Liquidity Depth
│   ├── structure.py       # VWAP, POC (Volume Profile), Heikin Ashi
│   ├── momentum.py        # RSI, MACD, EMA — all proportional
│   ├── regime.py          # ATR(14), regime detection (trending/ranging)
│   ├── scoring.py         # Composite score + cascade signal generation
│   └── correlation.py     # Pairwise indicator correlation tracking
│
├── engine/
│   ├── timeframe.py       # TimeframeEngine — one instance per TF
│   ├── merge.py           # Cascade execution — 5m trigger pass-through
│   ├── executor.py        # Entry/exit pipeline, SL/TP, trailing stop
│   ├── position.py        # Position state tracking, partial exits
│   ├── reconcile.py       # Position reconciliation on startup (live mode)
│   └── loss_analyzer.py   # Auto-classify losing trades (7 categories)
│
├── risk/
│   ├── sizing.py          # Position sizing (2% rule + 5x leverage cap)
│   ├── limits.py          # Daily loss limit, cooldown, circuit breakers
│   └── funding.py         # Funding rate fetch + 3-tier filter
│
├── data/
│   ├── state.py           # MarketState, TimeframeState, SignalSnapshot
│   ├── storage.py         # SQLite interface — 5 tables (thread-safe)
│   └── recorder.py        # Market snapshot recorder (every 10s)
│
├── dashboard/
│   ├── terminal.py        # Rich 4-panel split screen terminal
│   └── web.py             # Browser dashboard (SSE, aiohttp) for Railway
│
├── telegram_bot/
│   ├── bot.py             # Bot setup, polling loop, push dashboard
│   ├── handlers.py        # 16 command handlers + callback handler
│   ├── alerts.py          # Outbound alerts (entry, exit, TP1, risk, loss)
│   ├── keyboards.py       # Inline keyboard layouts
│   └── dashboard_push.py  # Live ASCII dashboard (edit message every 30s)
│
├── deploy/
│   ├── setup.sh           # VPS setup script
│   ├── bayesmarket.service # systemd unit file
│   └── VPS_GUIDE.md       # Deployment guide
│
├── backtest.py            # Signal replay backtest framework
├── report.py              # CLI performance report tool
├── requirements.txt
├── .env.example           # Full env var template
├── .env.testnet           # Testnet configuration template
├── .env.railway           # Railway environment template
└── CHANGELOG.md           # Improvement log

# Repo root (outside bayesmarket/)
Procfile                   # Railway worker process
railway.toml               # Railway deploy configuration
Dockerfile                 # Docker build for Railway/Railpack
```

</details>

<br>

---

<br>

## Performance Reporting

```bash
python -m bayesmarket.report                    # Today's summary
python -m bayesmarket.report --period 7d        # Last 7 days
python -m bayesmarket.report --period all --detail  # All time + trade detail
python -m bayesmarket.report --signals          # Signal distribution analysis

# Backtest — replay signals from DB
python -m bayesmarket.backtest                  # Default threshold 7.0
python -m bayesmarket.backtest --threshold 8.0  # Test higher threshold
python -m bayesmarket.backtest --capital 500    # Different starting capital
```

<br>

---

<br>

## Validation Checklist

After running 10+ minutes in shadow mode:

- [ ] Terminal shows 4-panel dashboard with live data
- [ ] All 4 TF panels show updating scores (RSI/MACD/EMA not `---`)
- [ ] Cascade state visible (BIAS direction, CTX confirmed, ZONE active)
- [ ] Binance klines updating (check `binance_kline_closed` in logs)
- [ ] Wall detection shows walls (or "none")
- [ ] Kline source shows `binance_futures` in status bar
- [ ] SQLite file created and growing
- [ ] At least some LONG/SHORT signals generated
- [ ] `python -m bayesmarket.report` outputs stats
- [ ] Telegram bot responds to `/status`
- [ ] Memory stable (not growing unbounded)

<br>

---

<br>

## Going Live

> **Shadow mode must pass all validation phases before considering live.**

| Step | Action | Duration |
|:----:|--------|----------|
| 1 | Run shadow mode — validate stability | 7+ days |
| 2 | Evaluate signal quality — review with `/analysis` | 14+ days |
| 3 | Switch to **testnet** — validate with mock USDC | 3+ days |
| 4 | Create API wallet at `app.hyperliquid.xyz/API` | — |
| 5 | Set `LIVE_MODE=true` via `.env` or Telegram `/live` | — |
| 6 | Start with small capital ($200-300) | — |
| 7 | Monitor first trades via Telegram alerts | Ongoing |

<br>

---

<br>

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Async | `asyncio` + `websockets` + `aiohttp` |
| Data | NumPy, SQLite (WAL mode) |
| Dashboard | Rich (terminal) + aiohttp SSE + Chart.js (web) |
| Telegram | python-telegram-bot 21+ |
| Logging | structlog (structured, color-aware) |
| Exchange | Hyperliquid (mainnet + testnet) |
| Klines | Binance Futures (primary source) |
| Deploy | Railway / VPS / Local |

<br>

---

<br>

## What This Is NOT

| | |
|:--:|---|
| **Not a full backtesting engine** | Simple signal replay; not tick-level simulation |
| **Not multi-asset** | BTC-PERP only for MVP |
| **Not HFT** | Accuracy over speed — rejects ~85% of signals |
| **Not financial advice** | Use at your own risk |

<br>

---

<br>

<div align="center">

**MIT License** &bull; See [LICENSE](LICENSE) for details

<br>

<sub>Built with structured conviction. Every signal earned, every trade justified.</sub>

</div>
