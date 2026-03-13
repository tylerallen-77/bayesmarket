<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Hyperliquid-Mainnet-6C5CE7?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Mode-Shadow-FFA502?style=for-the-badge" />
  <img src="https://img.shields.io/badge/License-MIT-2ED573?style=for-the-badge" />
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
┌─────────────────────────────────────────────────────────────────────┐
│                      LIVE MARKET DATA (MAINNET)                     │
│                                                                     │
│  Hyperliquid WebSocket          Binance Futures (Fallback)          │
│  ├─ l2Book (20 levels)          └─ kline_1m / 5m / 15m / 1h       │
│  └─ trades (BTC)                                                    │
│                                                                     │
│  Synthetic Kline Builder (Primary)                                  │
│  └─ HL trades → OHLCV candles per TF                               │
└────────────────────────┬────────────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   5m ENGINE  │ │  15m ENGINE  │ │  1h ENGINE   │ │  4h ENGINE   │
│  (EXECUTION) │ │  (EXECUTION) │ │  (FILTER)    │ │  (FILTER)    │
│              │ │              │ │              │ │              │
│ 9 indicators │ │ 9 indicators │ │ VWAP export  │ │ VWAP export  │
│ Score → ±13.5│ │ Score → ±13.5│ │ (for 5m)     │ │ (for 15m)    │
└──────┬───────┘ └──────┬───────┘ └──────────────┘ └──────────────┘
       │                │
       ▼                ▼
┌─────────────────────────────────┐
│       SMART MERGE ENGINE        │
│                                 │
│ Same direction → Combined pos.  │
│ Opposite → 15m wins (higher TF) │
│ One signal → Single execution   │
└────────────────┬────────────────┘
                 ▼
┌─────────────────────────────────┐
│        RISK MANAGEMENT          │
│  2% risk │ 5× lev cap │ 7% DD  │
│  Funding filter │ Cooldown FSM  │
└────────────────┬────────────────┘
                 ▼
┌─────────────────────────────────┐
│     SHADOW / LIVE EXECUTION     │
│  Shadow: simulate at mid_price  │
│  Live: Limit ALO / Stop Market  │
└─────────────────────────────────┘
```

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Multi-Timeframe** | 4 parallel TFs (5m, 15m execution + 1h, 4h filter) with skip-one VWAP alignment |
| **9 Indicators** | CVD, OBI, Depth, VWAP, POC, Heikin Ashi, RSI, MACD, EMA — all proportional, zero binary |
| **Smart Merge** | Combines 5m+15m signals when aligned; 15m wins on conflict |
| **3-Layer SL** | Wall (binned $10) → POC → ATR fallback with structural-only tightening |
| **Dual TP** | TP1 at VWAP reversion (60%), TP2 at 2× ATR (40%) |
| **Risk Engine** | 2% per trade, 5× leverage cap, cooldown FSM, 7% daily limit |
| **Synthetic Klines** | Built from HL trades (zero price divergence), Binance Futures fallback |
| **Rich Dashboard** | 4-panel live terminal with scores, walls, regime, position tracking |
| **Full Logging** | Every signal, trade, snapshot → SQLite with CLI report tool |

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
5. Computes signals every 1s (execution TFs) / 3-5s (filter TFs)
6. Renders live 4-panel dashboard
7. Logs everything to `bayesmarket.db`

---

## Dashboard

The terminal dashboard shows all 4 timeframes simultaneously with live data:

```
╔══════════════════════════════╦══════════════════════════════╗
║  BTC 5m  │ Price: $84,250   ║  BTC 15m │ Price: $84,250   ║
║  Score: +8.3 ████████░░     ║  Score: +6.1 ██████░░░░     ║
║  Signal: LONG ✓              ║  Signal: NEUTRAL             ║
║  MTF (1h): ALIGNED ▲         ║  MTF (4h): ALIGNED ▲         ║
║──────────────────────────────║──────────────────────────────║
║  ORDER BOOK                  ║  ORDER BOOK                  ║
║  OBI: +23.4% BULLISH         ║  OBI: +18.1% BULLISH         ║
║  Depth: +0.82                ║  Depth: +0.65                ║
║  Buy Walls: $84,000 (3.2)    ║  Buy Walls: $84,000 (3.2)    ║
║──────────────────────────────║──────────────────────────────║
║  FLOW                        ║  FLOW                        ║
║  CVD Z: +2.1σ (+1.93)        ║  CVD Z: +1.4σ (+1.24)        ║
║  POC: $84,100                ║  POC: $84,050                ║
║──────────────────────────────║──────────────────────────────║
║  TECHNICAL                   ║  TECHNICAL                   ║
║  RSI(14): 42.3 (+0.38)       ║  RSI(14): 45.1 (+0.25)       ║
║  MACD: bullish (+0.62)       ║  MACD: bullish (+0.41)       ║
║  EMA 5>20 (+0.75)            ║  EMA 5>20 (+0.55)            ║
║  VWAP: above (+1.12)         ║  VWAP: above (+0.83)         ║
║  HA: ▲ ▲ ▲ ▼ ▲ ▲ ▼ ▲       ║  HA: ▲ ▲ ▼ ▲ ▲ ▲ ▼ ▲       ║
╠══════════════════════════════╬══════════════════════════════╣
║  BTC 1h  │ Score: +5.2      ║  BTC 4h  │ Score: +3.8      ║
║  FILTER TF │ VWAP: $84,120  ║  FILTER TF │ VWAP: $84,050  ║
╠══════════════════════════════╩══════════════════════════════╣
║ POSITION: LONG 0.015 BTC @ $84,250 │ SL: $83,800 (wall)   ║
║ TP1: $84,500 (VWAP) [60%]  TP2: $84,900 (2×ATR) [40%]     ║
║ PnL: +$22.50 (+0.27%) │ Daily: +$145 (+1.45%)              ║
║ Risk: NORMAL │ Funding: 0.003%/h (safe) │ Regime: TRENDING  ║
╚═════════════════════════════════════════════════════════════╝
```

---

## Indicator Scoring

All indicators output **proportional scores** — no binary signals. The composite score ranges from **-13.5 to +13.5**.

### Category A: Order Flow (Leading) — max ±6.0

| Indicator | Formula | Range |
|-----------|---------|-------|
| **CVD** | `z = (cvd - mean) / std` → `2.0 × tanh(z / 2.0)` | ±2.0 |
| **OBI** | `((bid_vol - ask_vol) / total) × 2.0` | ±2.0 |
| **Depth** | `((bid_depth - ask_depth) / total) × 2.0` | ±2.0 |

### Category B: Structure (Equilibrium) — max ±4.5

| Indicator | Formula | Range |
|-----------|---------|-------|
| **VWAP** | `clamp((price - vwap) / vwap × 150, -1.5, +1.5)` | ±1.5 |
| **POC** | `clamp((price - poc) / poc × 150, -1.5, +1.5)` | ±1.5 |
| **Heikin Ashi** | `(streak / 3) × 1.5` where streak ∈ [-3, +3] | ±1.5 |

### Category C: Momentum (Lagging) — max ±3.0

| Indicator | Formula | Range |
|-----------|---------|-------|
| **RSI** | Linear: 30→+1, 50→0, 70→-1 | ±1.0 |
| **MACD** | `clamp(histogram / ATR, -1.0, +1.0)` | ±1.0 |
| **EMA** | `clamp((ema5 - ema20) / ema20 × 200, -1.0, +1.0)` | ±1.0 |

### Signal Thresholds

| Regime | Threshold | Action |
|--------|-----------|--------|
| Trending | ±7.0 | Generate LONG/SHORT signal |
| Ranging | ±8.5 (15m) / ±9.0 (5m) | Higher bar = fewer false signals |

---

## Signal Flow

```
Score ≥ +7.0 (trending)
    │
    ▼
MTF Filter: price > 1h VWAP? ──── No ──→ BLOCKED (mtf_misaligned)
    │ Yes
    ▼
Funding Filter: danger tier against? ── Yes ──→ BLOCKED (funding_danger)
    │ No
    ▼
Risk Check: cooldown? daily limit? ──── Yes ──→ BLOCKED
    │ No
    ▼
Position open? ──── Yes ──→ BLOCKED (unless merge eligible)
    │ No
    ▼
SMART MERGE with other execution TF
    │
    ▼
SL DETERMINATION: Wall → POC → ATR (3-layer fallback)
    │
    ▼
POSITION SIZING: 2% risk, 5× leverage cap
    │
    ▼
EXECUTE (shadow: simulate │ live: limit ALO)
```

---

## Risk Management

### Position Sizing

```python
risk_amount = capital × 2%                    # $20 on $1,000
sl_distance = abs(entry - sl)                 # e.g., $450
size = risk_amount / sl_distance              # e.g., 0.044 BTC

# Modifiers applied in order:
if cooldown:  size × 0.5                      # Half size during cooldown
if funding_caution: size × 0.75               # Reduced in caution tier
if merged: size × 2.0                         # Double for merged signals

# ALWAYS capped:
final = min(size, capital × MAX_LEVERAGE / price)  # 5× leverage cap
```

### Cooldown State Machine

```
NORMAL ──[3 consecutive losses]──→ COOLDOWN (50% size)
   ▲                                    │
   │                                    │
   ├──[2 wins]──────────────────────────┘
   ├──[1 hour elapsed]─────────────────┘
   │                                    │
   │                              [3 more losses]
   │                                    │
   │                                    ▼
   └──[4h elapsed]───────────── FULL STOP (no trading)
```

### Daily Protection

- **7% daily loss limit** → 12-hour pause
- **Resets at 00:00 UTC** daily
- **Capital auto-compounds** in shadow mode

---

## Stop Loss Strategy

BayesMarket uses **structural SL management** — no wall chasing after entry.

### 3-Layer Fallback (at entry)

| Priority | Source | Method |
|----------|--------|--------|
| 1st | **Wall** | Nearest valid bid/ask wall (binned $10, survived 5s) + 0.05% offset |
| 2nd | **POC** | Volume Profile point of control + 0.1% offset |
| 3rd | **ATR** | 1.5× ATR(14) from entry price |
| Emergency | **Pct** | 3% max distance cap |

### After Entry (Structural Only)

- **NEVER** tightens because a new wall appeared after entry
- **ONLY** tightens on confirmed structural swing low/high shift (0.3% confirmation)
- **ONLY** escalates fallback when the ORIGINAL basis wall decays (< 25%)
- **Minimum distance**: 0.3 × ATR from current price

---

## Project Structure

```
bayesmarket/
├── config.py              # All configurable constants and parameters
├── main.py                # Entry point — async orchestration (14 tasks)
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
│   └── scoring.py         # Composite score + signal generation
│
├── engine/
│   ├── timeframe.py       # TimeframeEngine — one instance per TF
│   ├── merge.py           # Smart merge: 4 conflict resolution cases
│   ├── executor.py        # Entry/exit pipeline, SL/TP management
│   └── position.py        # Position state tracking, partial exits
│
├── risk/
│   ├── sizing.py          # Position sizing (2% rule + 5× leverage cap)
│   ├── limits.py          # Daily loss limit, cooldown, circuit breakers
│   └── funding.py         # Funding rate fetch + 3-tier filter
│
├── data/
│   ├── state.py           # MarketState, TimeframeState, SignalSnapshot, etc.
│   ├── storage.py         # SQLite interface — 4 tables
│   └── recorder.py        # Market snapshot recorder (every 10s)
│
├── dashboard/
│   └── terminal.py        # Rich 4-panel split screen terminal
│
├── report.py              # CLI performance report tool
├── requirements.txt
├── .env.example
└── CHANGELOG.md           # Improvement log during shadow mode
```

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

**Example output:**

```
══════════════════════════════════════════════════════
BAYESMARKET PERFORMANCE REPORT — 7D
══════════════════════════════════════════════════════

SUMMARY
  Total trades:        47
  Win rate:            55.3% (26W / 21L)
  Profit factor:       1.42
  Net PnL:             +$82.30
  Avg PnL per trade:   +$1.75
  Avg duration:        14m 22s

BY SOURCE
  single_5m        22 trades | 50% WR | +$18.40
  single_15m       18 trades | 61% WR | +$52.90
  merged            7 trades | 57% WR | +$11.00

BY EXIT REASON
  tp1_hit          31 (66%)
  tp2_hit          12 (26%)
  sl_hit            4 (8%)
══════════════════════════════════════════════════════
```

---

## Configuration

All parameters are in `bayesmarket/config.py`. Key settings:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `LIVE_MODE` | `False` | Shadow mode (no orders, no credentials) |
| `SIMULATED_CAPITAL` | `$1,000` | Starting capital for simulation |
| `MAX_LEVERAGE` | `5×` | Hard cap on leverage |
| `MAX_RISK_PER_TRADE` | `2%` | Maximum risk per trade |
| `DAILY_LOSS_LIMIT` | `7%` | Daily drawdown circuit breaker |
| `WALL_BIN_SIZE` | `$10` | Price binning for wall detection |
| `WALL_PERSISTENCE_SECONDS` | `5s` | Minimum wall age for SL basis |
| `KLINE_SOURCE` | `synthetic` | Primary: HL trades, fallback: Binance Futures |

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

---

## Going Live

> **Shadow mode must pass all 3 validation phases before considering live.**
> See `CHANGELOG.md` for the improvement protocol.

1. Complete 21-day shadow validation (stability → signal quality → risk)
2. Create API wallet at `app.hyperliquid.xyz/API`
3. Fill `.env` with credentials
4. Set `LIVE_MODE = True` in `config.py`
5. Start with small capital ($200-300)
6. Monitor first trades manually in Hyperliquid UI

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Async | `asyncio` + `websockets` + `aiohttp` |
| Data | NumPy, SQLite (WAL mode) |
| Dashboard | Rich (terminal UI) |
| Logging | structlog (structured JSON-compatible) |
| Exchange | Hyperliquid (mainnet public feeds) |
| Fallback | Binance Futures (klines only) |

---

## What This Is NOT

- **Not a backtesting engine** — real-time signals on live data only
- **Not multi-asset** — BTC-PERP only for MVP
- **Not a web app** — terminal dashboard only
- **Not HFT** — accuracy over speed, rejects ~85% of signals
- **Not financial advice** — use at your own risk

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

<p align="center">
  <sub>Built with structured conviction. Every signal earned, every trade justified.</sub>
</p>
