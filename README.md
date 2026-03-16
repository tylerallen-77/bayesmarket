<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Hyperliquid-Mainnet%20%7C%20Testnet-6C5CE7?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Mode-Shadow%20%7C%20Live-FFA502?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Deploy-Local%20%7C%20Railway%20%7C%20VPS-2ED573?style=for-the-badge" />
</p>

<h1 align="center">
  <br>
  BayesMarket
  <br>
  <sub>Automated BTC-PERP Trading Engine for Hyperliquid</sub>
</h1>

<p align="center">
  <b>Accuracy over speed.</b> A multi-timeframe signal engine that rejects ~85% of signals through conviction filters — when it acts, conviction is high.
</p>

---

## Overview

BayesMarket is an automated perpetual futures trading engine designed for **Hyperliquid BTC-PERP**. It runs in **shadow mode** by default — computing real-time signals from live mainnet data, simulating trades, and logging everything to SQLite — without placing actual orders or requiring any credentials.

```
                        LIVE MARKET DATA
                             |
         Hyperliquid WebSocket          Binance Futures (Fallback)
         +- l2Book (50 levels)          +- kline 1m/5m/15m/1h
         +- trades (BTC)
         +- Synthetic Kline Builder (Primary)
                             |
        +--------------------+--------------------+
        v                    v                    v
  +-----------+  +-----------+  +-----------+  +-----------+
  | 4h ENGINE |  | 1h ENGINE |  |15m ENGINE |  | 5m ENGINE |
  |   BIAS    |  |  CONTEXT  |  |  TIMING   |  |  TRIGGER  |
  | direction |  | confirms  |  | entry zone|  | execution |
  +-----+-----+  +-----+-----+  +-----+-----+  +-----+-----+
        |               |              |               |
        v               v              v               v
  +-----------------------------------------------------------+
  |              CASCADE FILTER (top-down)                     |
  |  4h score > ±3.0 -> LONG/SHORT bias                       |
  |  1h same sign -> context confirmed                        |
  |  15m threshold -> timing zone active (5min TTL)           |
  |  5m score > threshold + all gates pass -> TRIGGER         |
  +----------------------------+------------------------------+
                               v
  +---------------------------+
  |     RISK MANAGEMENT       |
  |  2% risk | 5x lev | 7% DD|
  |  Funding filter | Cooldown|
  +------------+--------------+
               v
  +---------------------------+
  |  SHADOW / LIVE EXECUTION  |
  |  Shadow: simulate @ mid   |
  |  Live: Limit ALO / Stop   |
  +---------------------------+
               |
      +--------+--------+
      v                  v
  +--------+    +---------------+
  |Terminal |    |Telegram Bot   |
  |Dashboard|    |Control Panel  |
  |(local)  |    |+ Push Dashboard|
  +--------+    +---------------+
```

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Cascade MTF** | Top-down: 4h BIAS -> 1h CONTEXT -> 15m TIMING -> 5m TRIGGER. Only 5m executes trades |
| **9 Indicators** | CVD, OBI, Depth, VWAP, POC, Heikin Ashi, RSI, MACD, EMA — all proportional, zero binary |
| **3-Layer SL** | Wall (binned $20) -> POC -> ATR fallback with structural-only tightening |
| **SL/TP Ratio Guard** | MAX_SL_TP_RATIO=3.0 caps absurd SL from stale POC levels |
| **Dual TP** | TP1 at VWAP reversion (60%), TP2 at 2x ATR (40%) |
| **Time-Based Exit** | Auto-close after 30m if TP1 not hit |
| **Risk Engine** | 2% per trade, 5x leverage cap, cooldown FSM, 7% daily limit |
| **Synthetic Klines** | Built from HL trades (zero price divergence), Binance Futures fallback |
| **Rich Dashboard** | 4-panel live terminal with scores, walls, regime, position tracking |
| **Telegram Bot** | Full control panel: 15+ commands, inline keyboards, live push dashboard |
| **Loss Analysis** | Auto-classification of losing trades (7 categories) with recommendations |
| **Testnet Support** | Single env var swap to switch between mainnet and testnet |
| **Railway Deploy** | One-click deploy to Railway PaaS (headless, Telegram-only monitoring) |
| **Full Logging** | Every signal, trade, snapshot -> SQLite with CLI report tool |

---

## Quick Start

### Prerequisites

- **Python 3.11+**
- Internet connection (Hyperliquid + Binance public WebSocket feeds)
- No API keys needed for shadow mode

### Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/bayesmarket.git
cd bayesmarket

# Install dependencies
pip install -r bayesmarket/requirements.txt

# Run in shadow mode (no credentials needed)
python -m bayesmarket
```

> **Windows with Zscaler/SSL issues?**
> ```bash
> pip install --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -r bayesmarket/requirements.txt
> ```

### What Happens on Launch

1. Bootstraps kline history from Binance Futures REST (4 calls)
2. Connects to Hyperliquid WebSocket (l2Book + trades)
3. Connects to Binance Futures WebSocket (fallback klines)
4. Starts synthetic kline builders from HL trades
5. Runs cascade: 4h bias -> 1h context -> 15m timing -> 5m trigger
6. Renders live 4-panel dashboard (local) or push dashboard (Telegram)
7. Logs everything to `bayesmarket.db`

---

## Telegram Bot

BayesMarket includes a full Telegram control panel for remote monitoring and control.

### Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) -> `/newbot`
2. Get your Chat ID via [@userinfobot](https://t.me/userinfobot)
3. Set in `.env`:
   ```
   TELEGRAM_BOT_TOKEN=your_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```

### Commands

| Command | Description |
|---------|-------------|
| `/start` | Main menu with inline buttons |
| `/status` | Full status (position, PnL, risk state) |
| `/scores` | Live scores for all 4 timeframes |
| `/report [1d\|7d\|30d\|all]` | Performance report |
| `/dashboard` | Pull: snapshot now |
| `/dashboard auto` | Push: live ticker updated every 30s |
| `/dashboard off` | Stop auto-push |
| `/analysis [1d\|7d\|30d\|all]` | Loss pattern analysis |
| `/mode` | View/switch mode |
| `/live` / `/shadow` | Switch trading mode |
| `/pause [reason]` / `/resume` | Pause/resume trading |
| `/close` | Force close open position |
| `/config` | View active config |
| `/set <param> <value>` | Hot-reload parameters |
| `/help` | Command reference |

### Push Dashboard

The push dashboard edits a single Telegram message every 30 seconds with a live ASCII table:

```
METRIC          | VALUE       | STATUS
----------------|-------------|--------
PRICE           | $84,250.0   | -
SCORE 5m        | +8.3 ▓▓▓▓░ | ▲ LONG
SCORE 15m       | +6.1 ▓▓▓░░ | - NEUTRAL
POSITION        | LONG        | OPEN
UNREALIZED      | +$22.50     | +0.27%
RISK STATE      | NORMAL      | W:3 L:0
```

---

## Deployment

BayesMarket supports three deployment modes, switchable via env vars:

| Mode | `DEPLOYMENT_ENV` | Dashboard | Monitoring |
|------|-----------------|-----------|------------|
| **Local** | `local` | Rich terminal | Terminal + Telegram |
| **Railway** | `railway` | Disabled (no TTY) | Telegram only |
| **VPS** | `vps` | Rich terminal | Terminal + Telegram |

### Railway (PaaS)

```bash
# Files included: Procfile, railway.toml, nixpacks.toml
# Set variables in Railway Dashboard -> Service -> Variables
# Mount volume at /app/data for persistent SQLite
```

See `.env.railway` for the full variable template.

### VPS (Contabo/Oracle)

```bash
cd bayesmarket/deploy
chmod +x setup.sh
./setup.sh
```

See `bayesmarket/deploy/VPS_GUIDE.md` for detailed instructions.

### Testnet

Switch to Hyperliquid testnet with env vars only — no code changes:

```bash
LIVE_MODE=true
HL_REST_URL=https://api.hyperliquid-testnet.xyz
HL_WS_URL=wss://api.hyperliquid-testnet.xyz/ws
HL_PRIVATE_KEY=<testnet API wallet key>
HL_ACCOUNT_ADDRESS=<testnet main wallet address>
```

See `.env.testnet` for the full template. Get mock USDC at https://app.hyperliquid-testnet.xyz/drip

---

## Indicator Scoring

All indicators output **proportional scores** — no binary signals. The composite score ranges from **-13.5 to +13.5**.

### Category A: Order Flow (Leading) — max +/-6.0

| Indicator | Formula | Range |
|-----------|---------|-------|
| **CVD** | `z = (cvd - mean) / std` -> `2.0 * tanh(z / 2.0)` | +/-2.0 |
| **OBI** | `((bid_vol - ask_vol) / total) * 2.0` | +/-2.0 |
| **Depth** | `((bid_depth - ask_depth) / total) * 2.0` | +/-2.0 |

### Category B: Structure (Equilibrium) — max +/-4.5

| Indicator | Formula | Range |
|-----------|---------|-------|
| **VWAP** | `clamp((price - vwap) / vwap * 20, -1.5, +1.5)` | +/-1.5 |
| **POC** | `clamp((price - poc) / poc * 20, -1.5, +1.5)` | +/-1.5 |
| **Heikin Ashi** | `(streak / 5) * 1.5` where streak in [-5, +5] | +/-1.5 |

### Category C: Momentum (Lagging) — max +/-3.0

| Indicator | Formula | Range |
|-----------|---------|-------|
| **RSI** | Linear: 30->+1, 50->0, 70->-1 | +/-1.0 |
| **MACD** | `clamp(histogram / ATR, -1.0, +1.0)` | +/-1.0 |
| **EMA** | `clamp((ema5 - ema20) / ema20 * 200, -1.0, +1.0)` | +/-1.0 |

### Signal Thresholds

| Role | Regime | Threshold | Action |
|------|--------|-----------|--------|
| 4h BIAS | Any | +/-3.0 | Set allowed direction (LONG/SHORT/BOTH) |
| 1h CONTEXT | Any | Same sign as 4h | Confirm or block cascade |
| 15m TIMING | Any | Role threshold | Establish entry zone (5min TTL) |
| 5m TRIGGER (trending) | Trending | +/-7.0 | Generate trade signal |
| 5m TRIGGER (ranging) | Ranging | +/-9.0 | Higher bar = fewer false signals |

---

## Signal Flow (Cascade)

```
4h ENGINE (BIAS) — score > ±3.0?
    |-- Yes: allowed_direction = LONG or SHORT
    |-- No:  allowed_direction = BOTH (no filter)
    v
1h ENGINE (CONTEXT) — score same sign as 4h?
    |-- Yes: context_confirmed = true
    |-- No:  context_confirmed = false -> 5m BLOCKED
    v
15m ENGINE (TIMING) — score > threshold & matches bias?
    |-- Yes: timing_zone = ACTIVE (5min TTL)
    |-- No:  timing_zone = INACTIVE -> 5m BLOCKED
    v
5m ENGINE (TRIGGER) — score > ±7.0 (trending) / ±9.0 (ranging)
    |-- Direction matches zone & bias?
    |       |-- No: BLOCKED (trigger_against_zone / trigger_against_bias)
    |       v
    |   Funding Filter: danger tier against? --- Yes --> BLOCKED
    |       | No
    |       v
    |   Risk Check: cooldown? daily limit? ---- Yes --> BLOCKED
    |       | No
    |       v
    |   Position open? --- Yes --> BLOCKED
    |       | No
    |       v
    |   SL DETERMINATION: Wall -> POC -> ATR (3-layer fallback)
    |       |-- SL/TP ratio > 3.0? Cap SL distance
    |       v
    |   POSITION SIZING: 2% risk, 5x leverage cap
    |       |
    |       v
    |   EXECUTE (shadow: simulate | live: limit ALO)
```

---

## Risk Management

### Position Sizing

```python
risk_amount = capital * 2%                    # $20 on $1,000
sl_distance = abs(entry - sl)                 # e.g., $450
size = risk_amount / sl_distance              # e.g., 0.044 BTC

# Modifiers applied in order:
if cooldown:  size *= 0.5                     # Half size during cooldown
if funding_caution: size *= 0.75              # Reduced in caution tier
if merged: size *= 2.0                        # Double for merged signals

# ALWAYS capped:
final = min(size, capital * MAX_LEVERAGE / price)  # 5x leverage cap
```

### Cooldown State Machine

```
NORMAL --[3 consecutive losses]--> COOLDOWN (50% size)
   ^                                    |
   |--[2 wins]-------------------------+
   |--[1 hour elapsed]----------------+
   |                              [3 more losses]
   |                                    |
   |                                    v
   +--[4h elapsed]------------- FULL STOP (no trading)
```

### Daily Protection

- **7% daily loss limit** -> 12-hour pause
- **Resets at 00:00 UTC** daily
- **Capital auto-compounds** in shadow mode

---

## Loss Trade Analysis

When a trade closes with a loss, BayesMarket automatically classifies the failure into one of 7 categories:

| Category | Severity | Description |
|----------|----------|-------------|
| `stale_poc_sl` | Critical | SL based on POC >1% away from entry |
| `poor_rr_entry` | Critical | Risk/reward ratio < 0.5 at entry |
| `time_overheld` | Critical | Held >2x the time exit limit |
| `trend_reversal` | Moderate | Score flipped direction during hold |
| `choppy_market` | Moderate | Borderline entry score in ranging market |
| `cascade_misaligned` | Moderate | Cascade bias/context was weak at entry |
| `normal_sl` | Minor | Clean SL, no anomaly detected |

Diagnosis is stored in the `trades` table and surfaced via:
- Telegram `/analysis` command (pattern summary by period)
- Rich loss alerts with per-trade diagnostics and recommendations

---

## Stop Loss Strategy

BayesMarket uses **structural SL management** — no wall chasing after entry.

### 3-Layer Fallback (at entry)

| Priority | Source | Method |
|----------|--------|--------|
| 1st | **Wall** | Nearest valid bid/ask wall (binned $20, survived 3s) + 0.05% offset |
| 2nd | **POC** | Volume Profile point of control + 0.1% offset |
| 3rd | **ATR** | 1.5x ATR(14) from entry price |
| Guard | **Ratio** | If SL > 3x TP1 distance, cap SL |
| Emergency | **Pct** | 3% max distance cap |

### After Entry (Structural Only)

- **NEVER** tightens because a new wall appeared after entry
- **ONLY** tightens on confirmed structural swing low/high shift (0.3% confirmation)
- **ONLY** escalates fallback when the ORIGINAL basis wall decays (< 25%)
- **Minimum distance**: 0.3 x ATR from current price

---

## Project Structure

```
bayesmarket/
├── config.py              # All configurable constants and parameters
├── main.py                # Entry point — async orchestration (15 tasks)
├── runtime.py             # Mutable RuntimeConfig (hot-reload via Telegram)
├── __main__.py            # python -m bayesmarket support
│
├── feeds/
│   ├── hyperliquid.py     # HL WebSocket: l2Book, trades + wall tracker
│   ├── binance.py         # Binance FUTURES WebSocket + REST (FALLBACK)
│   └── synthetic.py       # Synthetic kline builder from HL trades
│
├── indicators/
│   ├── order_flow.py      # CVD (Z-Score + tanh), OBI, Liquidity Depth
│   ├── structure.py       # VWAP, POC (Volume Profile), Heikin Ashi
│   ├── momentum.py        # RSI, MACD, EMA — all proportional
│   ├── regime.py          # ATR(14), regime detection (trending/ranging)
│   └── scoring.py         # Composite score + cascade signal generation
│
├── engine/
│   ├── timeframe.py       # TimeframeEngine — one instance per TF
│   ├── merge.py           # Cascade execution — 5m trigger only
│   ├── executor.py        # Entry/exit pipeline, SL/TP, time exit, loss analysis
│   ├── position.py        # Position state tracking, partial exits
│   └── loss_analyzer.py   # Auto-classify losing trades (7 categories)
│
├── risk/
│   ├── sizing.py          # Position sizing (2% rule + 5x leverage cap)
│   ├── limits.py          # Daily loss limit, cooldown, circuit breakers
│   └── funding.py         # Funding rate fetch + 3-tier filter
│
├── data/
│   ├── state.py           # MarketState, TimeframeState, SignalSnapshot, etc.
│   ├── storage.py         # SQLite interface — 4 tables + v3 migration
│   └── recorder.py        # Market snapshot recorder (every 10s)
│
├── dashboard/
│   └── terminal.py        # Rich 4-panel split screen terminal
│
├── telegram_bot/
│   ├── bot.py             # Bot setup, polling loop, push dashboard init
│   ├── handlers.py        # 15 command handlers + callback handler
│   ├── alerts.py          # Outbound alerts (entry, exit, TP1, risk, loss)
│   ├── keyboards.py       # Inline keyboard layouts
│   └── dashboard_push.py  # Live ASCII dashboard (edit message every 30s)
│
├── deploy/
│   ├── setup.sh           # VPS setup script
│   ├── bayesmarket.service # systemd unit file
│   └── VPS_GUIDE.md       # Deployment guide
│
├── report.py              # CLI performance report tool
├── requirements.txt
├── .env.example           # Full env var template
├── .env.testnet           # Testnet configuration template
├── .env.railway           # Railway environment template
├── Procfile               # Railway/Heroku worker process
├── railway.toml           # Railway deploy configuration
├── nixpacks.toml          # Nixpacks build configuration
└── CHANGELOG.md           # Improvement log
```

---

## Configuration

All parameters are in `bayesmarket/config.py`. Key settings:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `LIVE_MODE` | `False` | Shadow mode (no orders, no credentials) |
| `SIMULATED_CAPITAL` | `$1,000` | Starting capital for simulation |
| `MAX_LEVERAGE` | `5x` | Hard cap on leverage |
| `MAX_RISK_PER_TRADE` | `2%` | Maximum risk per trade |
| `DAILY_LOSS_LIMIT` | `7%` | Daily drawdown circuit breaker |
| `MAX_SL_TP_RATIO` | `3.0` | SL/TP distance ratio cap |
| `WALL_BIN_SIZE` | `$20` | Price binning for wall detection |
| `WALL_PERSISTENCE_SECONDS` | `3s` | Minimum wall age for SL basis |
| `KLINE_SOURCE` | `synthetic` | Primary: HL trades, fallback: Binance Futures |
| `IS_TESTNET` | Auto-detect | `true` if HL_REST_URL contains "testnet" |
| `DEPLOYMENT_ENV` | `local` | `local` / `railway` / `vps` |

### Runtime Hot-Reload (via Telegram `/set`)

| Parameter | Range | Default |
|-----------|-------|---------|
| `threshold_5m` | 1.0 - 15.0 | 7.0 |
| `bias_threshold` | 1.0 - 10.0 | 3.0 |
| `vwap_sensitivity` | 1.0 - 500.0 | 20.0 |
| `poc_sensitivity` | 1.0 - 500.0 | 20.0 |

---

## Performance Reporting

```bash
# Today's summary
python -m bayesmarket.report

# Last 7 days
python -m bayesmarket.report --period 7d

# All time with trade detail
python -m bayesmarket.report --period all --detail

# Signal distribution analysis
python -m bayesmarket.report --signals
```

---

## Shadow Mode Validation

After running for 10+ minutes, verify:

- [ ] Terminal shows 4-panel dashboard with live data
- [ ] All 4 TF panels show updating scores
- [ ] Synthetic klines incrementing (check candle timestamps)
- [ ] Wall detection shows walls (or "none" if none exist)
- [ ] SQLite file created and growing (`bayesmarket.db`)
- [ ] At least some LONG/SHORT signals generated
- [ ] `python -m bayesmarket.report` outputs stats
- [ ] No errors in terminal output
- [ ] Memory stable (not growing unbounded)
- [ ] Telegram bot responds to `/status` (if configured)

---

## Going Live

> **Shadow mode must pass all validation phases before considering live.**

1. Run shadow mode for 21+ days (stability -> signal quality -> risk)
2. Switch to **testnet** first — validate with mock USDC
3. Create API wallet at `app.hyperliquid.xyz/API`
4. Fill `.env` with credentials
5. Set `LIVE_MODE=true` via `.env` or Telegram `/live`
6. Start with small capital ($200-300)
7. Monitor first trades via Telegram alerts

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Async | `asyncio` + `websockets` + `aiohttp` |
| Data | NumPy, SQLite (WAL mode) |
| Dashboard | Rich (terminal UI) |
| Telegram | python-telegram-bot 21+ |
| Logging | structlog (structured, color-aware) |
| Exchange | Hyperliquid (mainnet + testnet) |
| Fallback | Binance Futures (klines only) |
| Deploy | Railway / VPS / Local |

---

## What This Is NOT

- **Not a backtesting engine** — real-time signals on live data only
- **Not multi-asset** — BTC-PERP only for MVP
- **Not HFT** — accuracy over speed, rejects ~85% of signals
- **Not financial advice** — use at your own risk

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

<p align="center">
  <sub>Built with structured conviction. Every signal earned, every trade justified.</sub>
</p>
