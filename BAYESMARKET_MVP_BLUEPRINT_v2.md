# BAYESMARKET MVP — COMPREHENSIVE BLUEPRINT v2
## Production-Ready Hyperliquid Perpetual Trading Engine

> **Purpose:** Complete, unambiguous specification for building an MVP automated perpetual
> futures trading system on Hyperliquid. Every decision is made. Every formula is defined.
> Every parameter is locked. A developer (or Claude Code) should be able to build the
> entire system from this document alone without asking any clarifying questions.

> **Target:** Hyperliquid perpetual futures (NOT spot). BTC-PERP only for MVP.

> **Mode:** Shadow mode — real-time signals on Hyperliquid mainnet public feeds, full
> logging to SQLite, simulated position tracking, NO actual order execution.
> Execution module is built but gated behind a `LIVE_MODE = False` flag.

> **Credentials:** HL_PRIVATE_KEY and HL_ACCOUNT_ADDRESS are NOT required for shadow mode.
> All data feeds used (l2Book, trades, metaAndAssetCtxs) are public endpoints.
> Credentials only needed when LIVE_MODE is activated in the future.

---

## TABLE OF ALL LOCKED DECISIONS

| # | Decision | Choice |
|---|----------|--------|
| 1 | Target market | Hyperliquid perpetual futures, BTC only |
| 2 | Data source | Hybrid: Order Book + Trades from Hyperliquid, Klines from Binance |
| 3 | Architecture | 4-TF parallel: 2 execution TFs (5m, 15m) + 2 filter TFs (1h, 4h) |
| 4 | MTF filter chain | Skip-one: 5m←1h VWAP, 15m←4h VWAP |
| 5 | Position management | Smart merge: same direction = combine, opposite = first-mover wins |
| 6 | Liquidity Depth formula | Normalized Difference: `((Bid-Ask)/(Bid+Ask)) × 2.0` |
| 7 | Z-Score CVD mapping | Tanh: `2.0 × tanh(z_score / 2.0)` |
| 8 | Indicator scaling | ALL proportional (zero binary indicators) |
| 9 | Funding rate filter | 3-tier aggressive |
| 10 | Position sizing | 2% risk per trade |
| 11 | Daily loss limit | 7% → pause 12 hours |
| 12 | Stop loss strategy | Wall → POC → ATR (3-layer fallback) |
| 13 | Cooldown protocol | 3 consecutive losses → 50% size, reset after 2 wins OR 1 hour |
| 14 | Wall persistence filter | 15 seconds + size ≥ 3× average level size |
| 15 | ATR fallback SL | 1.5× ATR(14) from entry |
| 16 | Take profit | Dual: TP1 exit 60%, TP2 exit 40% |
| 17 | TP placement | TP1 = VWAP reversion, TP2 = 2.0× ATR from entry |
| 18 | Polymarket integration | Deferred to v2 |
| 19 | Paper trading mode | Shadow mode on mainnet (log only, no execution, no credentials needed) |
| 20 | Data storage | SQLite |
| 21 | Dashboard | Full Rich terminal — split screen 4 panels (one per TF) |
| 22 | HL credentials | Optional — not required for shadow mode |

---

## 1. ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DATA FEEDS (SHARED)                         │
│                                                                     │
│  Hyperliquid WebSocket          Binance WebSocket + REST            │
│  ├─ l2Book (BTC, 20 levels)    ├─ kline_1m  (for 5m scoring)      │
│  └─ trades (BTC)               ├─ kline_5m  (for 15m scoring)     │
│                                 ├─ kline_15m (for 1h scoring)      │
│                                 └─ kline_1h  (for 4h scoring)      │
│                                                                     │
│  All feeds write to ONE shared MarketState object                   │
└────────────────────────┬────────────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   5m ENGINE  │ │  15m ENGINE  │ │  1h ENGINE   │ │  4h ENGINE   │
│  (EXECUTION) │ │  (EXECUTION) │ │  (FILTER)    │ │  (FILTER)    │
│              │ │              │ │              │ │              │
│ 9 indicators │ │ 9 indicators │ │ 9 indicators │ │ 9 indicators │
│ Scoring      │ │ Scoring      │ │ Scoring      │ │ Scoring      │
│ Signal gen   │ │ Signal gen   │ │ VWAP export  │ │ VWAP export  │
│              │ │              │ │ (for 5m)     │ │ (for 15m)    │
│ MTF filter:  │ │ MTF filter:  │ │              │ │              │
│ 1h VWAP ────►│ │ 4h VWAP ────►│ │              │ │              │
└──────┬───────┘ └──────┬───────┘ └──────────────┘ └──────────────┘
       │                │
       ▼                ▼
┌─────────────────────────────────┐
│       SMART MERGE ENGINE        │
│                                 │
│ IF 5m=LONG AND 15m=LONG:       │
│   → Combined position (bigger)  │
│ IF 5m=LONG AND 15m=SHORT:      │
│   → First trigger wins          │
│ IF only one triggers:           │
│   → That one executes alone     │
└────────────────┬────────────────┘
                 ▼
┌─────────────────────────────────┐
│        RISK MANAGEMENT          │
│  Position sizing (2% rule)      │
│  Funding filter (3-tier)        │
│  Daily limit (7%)               │
│  Cooldown (3 losses)            │
└────────────────┬────────────────┘
                 ▼
┌─────────────────────────────────┐
│     EXECUTION (gated)           │
│  Shadow: log simulated trades   │
│  Live: Hyperliquid SDK orders   │
└────────────────┬────────────────┘
                 ▼
┌─────────────────────────────────┐
│  ┌─────┐┌─────┐┌─────┐┌─────┐  │
│  │ 5m  ││ 15m ││ 1h  ││ 4h  │  │
│  │panel││panel││panel││panel│  │
│  └─────┘└─────┘└─────┘└─────┘  │
│    RICH TERMINAL DASHBOARD      │
│    (Split screen, 4 panels)     │
└─────────────────────────────────┘
```

**Key architectural rules:**
- ALL 4 TFs compute ALL 9 indicators and display scores on dashboard
- ONLY 5m and 15m generate executable signals
- 1h and 4h compute everything for display BUT their primary role is providing VWAP filter values
- Order book data (OBI, Depth, Walls) is SHARED — same HL l2Book feed for all TFs
- Each TF has its OWN kline stream from Binance at appropriate interval
- ONE shared position tracker — smart merge handles conflicts

---

## 2. PROJECT STRUCTURE

```
bayesmarket/
├── config.py              # All configurable constants and parameters
├── main.py                # Entry point — async orchestration, menu
│
├── feeds/
│   ├── __init__.py
│   ├── hyperliquid.py     # HL WebSocket: l2Book, trades (shared for all TFs)
│   └── binance.py         # Binance WebSocket + REST: klines per TF interval
│
├── indicators/
│   ├── __init__.py
│   ├── order_flow.py      # CVD (Z-Score), OBI, Liquidity Depth
│   ├── structure.py       # VWAP, POC (Volume Profile), Heikin Ashi
│   ├── momentum.py        # RSI, MACD, EMA
│   ├── regime.py          # ATR, regime detection
│   └── scoring.py         # Composite bias score aggregation per TF
│
├── engine/
│   ├── __init__.py
│   ├── timeframe.py       # TimeframeEngine class — one instance per TF
│   ├── merge.py           # Smart merge logic for 5m+15m signal conflicts
│   ├── executor.py        # Entry/exit pipeline, SL/TP management
│   └── position.py        # Position state tracking, partial exits
│
├── risk/
│   ├── __init__.py
│   ├── sizing.py          # Position sizing (2% rule)
│   ├── limits.py          # Daily loss limit, cooldown, circuit breakers
│   └── funding.py         # Funding rate fetch + 3-tier filter
│
├── data/
│   ├── __init__.py
│   ├── state.py           # MarketState, TimeframeState, SignalSnapshot
│   ├── storage.py         # SQLite interface for all logging
│   └── recorder.py        # Continuous data capture pipeline
│
├── dashboard/
│   ├── __init__.py
│   └── terminal.py        # Full Rich terminal UI — 4-panel split screen
│
├── requirements.txt
└── .env.example           # Template (credentials optional for shadow mode)
```

---

## 3. DEPENDENCIES

```
# requirements.txt

# Core async + networking
websockets>=14.0
requests>=2.32.0
aiohttp>=3.9.0

# Hyperliquid SDK (needed for future LIVE_MODE, install now)
hyperliquid-python-sdk>=0.8.0

# Data processing
numpy>=1.26.0

# Terminal dashboard
rich>=14.0.0

# Environment variables
python-dotenv>=1.0.0

# Structured logging
structlog>=24.0.0
```

```
# .env.example
# HL credentials — NOT REQUIRED for shadow mode
# Only needed when LIVE_MODE = True
# HL_PRIVATE_KEY=
# HL_ACCOUNT_ADDRESS=
```

---

## 4. CONFIGURATION — `config.py`

```python
"""
BayesMarket MVP Configuration — All parameters locked.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════════════════════════
# MODE
# ══════════════════════════════════════════════════════════════════
LIVE_MODE = False          # False = shadow mode (no orders, no credentials needed)

# ══════════════════════════════════════════════════════════════════
# ASSET
# ══════════════════════════════════════════════════════════════════
COIN = "BTC"                       # Hyperliquid perp asset name
BINANCE_SYMBOL = "BTCUSDT"         # Binance spot symbol for klines

# ══════════════════════════════════════════════════════════════════
# MULTI-TIMEFRAME ARCHITECTURE
# ══════════════════════════════════════════════════════════════════
# Execution TFs: generate tradeable signals
# Filter TFs: compute scores for display + provide VWAP filter for execution TFs

TIMEFRAMES = {
    "5m": {
        "role": "execution",           # Can trigger trades
        "kline_interval": "1m",        # Binance candle interval for TA
        "kline_bootstrap": 150,        # Candles fetched on startup
        "kline_max": 200,              # Max candles in memory
        "mtf_filter_tf": "1h",         # VWAP filter comes from 1h engine
        "scoring_threshold": 7.0,      # Entry threshold (trending regime)
        "scoring_threshold_ranging": 9.0,  # Entry threshold (ranging regime)
        "obi_band_pct": 0.5,          # Tighter band for faster TF
        "z_score_lookback": 100,       # 100 × 1m candles = 100 min
        "signal_refresh_seconds": 1.0, # Compute signals every 1s
    },
    "15m": {
        "role": "execution",
        "kline_interval": "5m",
        "kline_bootstrap": 150,
        "kline_max": 200,
        "mtf_filter_tf": "4h",         # VWAP filter comes from 4h engine
        "scoring_threshold": 7.0,
        "scoring_threshold_ranging": 8.5,
        "obi_band_pct": 0.75,
        "z_score_lookback": 100,       # 100 × 5m candles = 500 min
        "signal_refresh_seconds": 1.0,
    },
    "1h": {
        "role": "filter",              # Display + provides VWAP for 5m
        "kline_interval": "15m",
        "kline_bootstrap": 100,
        "kline_max": 150,
        "mtf_filter_tf": None,         # No filter for filter TFs
        "scoring_threshold": 7.0,
        "scoring_threshold_ranging": 8.0,
        "obi_band_pct": 1.0,
        "z_score_lookback": 100,       # 100 × 15m candles = 25 hours
        "signal_refresh_seconds": 3.0, # Slower refresh for filter TFs
    },
    "4h": {
        "role": "filter",              # Display + provides VWAP for 15m
        "kline_interval": "1h",
        "kline_bootstrap": 100,
        "kline_max": 150,
        "mtf_filter_tf": None,
        "scoring_threshold": 7.0,
        "scoring_threshold_ranging": 8.0,
        "obi_band_pct": 1.0,
        "z_score_lookback": 100,       # 100 × 1h candles = 100 hours
        "signal_refresh_seconds": 5.0,
    },
}

# ══════════════════════════════════════════════════════════════════
# CONNECTIONS
# ══════════════════════════════════════════════════════════════════
HL_WS_URL = "wss://api.hyperliquid.xyz/ws"
HL_REST_URL = "https://api.hyperliquid.xyz"
HL_L2_BOOK_LEVELS = 20
HL_L2_SIG_FIGS = 5

BINANCE_WS_URL = "wss://stream.binance.com/stream"
BINANCE_REST_URL = "https://api.binance.com/api/v3"

# HL credentials (optional — only for LIVE_MODE)
HL_PRIVATE_KEY = os.getenv("HL_PRIVATE_KEY", "")
HL_ACCOUNT_ADDRESS = os.getenv("HL_ACCOUNT_ADDRESS", "")

# ══════════════════════════════════════════════════════════════════
# DATA RETENTION
# ══════════════════════════════════════════════════════════════════
TRADE_TTL_SECONDS = 600      # Keep 10 min of HL trades in memory
OB_SNAPSHOT_INTERVAL = 0.5   # HL pushes l2Book per block (~0.5s)

# ══════════════════════════════════════════════════════════════════
# SCORING WEIGHTS (same for all TFs)
# ══════════════════════════════════════════════════════════════════
WEIGHTS = {
    # Category A: Order Flow (leading) — max ±6.0 total
    "cvd":   2.0,
    "obi":   2.0,
    "depth": 2.0,
    # Category B: Structure & Equilibrium — max ±4.5 total
    "vwap":  1.5,
    "poc":   1.5,
    "ha":    1.5,
    # Category C: Momentum (lagging) — max ±3.0 total
    "rsi":   1.0,
    "macd":  1.0,
    "ema":   1.0,
}
# Theoretical max: ±13.5
# Neutral zone: -6.5 to +6.5 (no action)

# ══════════════════════════════════════════════════════════════════
# INDICATOR PARAMETERS (shared across TFs unless overridden)
# ══════════════════════════════════════════════════════════════════

# CVD
CVD_WINDOW_SECONDS = 300           # 5-min CVD accumulation window
CVD_MAPPING = "tanh"               # 2.0 × tanh(z / 2.0)

# OBI
# obi_band_pct is per-TF (see TIMEFRAMES dict above)
# OBI score = obi_raw × 2.0

# Liquidity Depth
DEPTH_BAND_PCT = 0.5               # ±0.5% from mid
# depth_score = ((bid_depth - ask_depth) / (bid_depth + ask_depth)) × 2.0

# VWAP
VWAP_SENSITIVITY = 150.0           # 1% deviation from VWAP = ±1.5 (max)

# POC
VP_BINS = 30
POC_SENSITIVITY = 150.0            # Same scaling as VWAP

# Heikin Ashi
HA_MAX_STREAK = 3                  # ha_score = (streak / 3) × 1.5
HA_DISPLAY_COUNT = 8               # Candles shown on dashboard

# RSI
RSI_PERIOD = 14
RSI_OB = 70
RSI_OS = 30

# MACD
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
# macd_score = clamp(histogram / atr, -1.0, +1.0)

# EMA
EMA_SHORT = 5
EMA_LONG = 20
EMA_SENSITIVITY = 200.0            # 0.5% spread = ±1.0 (max)

# ══════════════════════════════════════════════════════════════════
# REGIME DETECTION
# ══════════════════════════════════════════════════════════════════
ATR_PERIOD = 14
ATR_RANGING_PERCENTILE = 30
ATR_PERCENTILE_LOOKBACK = 100

# ══════════════════════════════════════════════════════════════════
# MTF ALIGNMENT (Skip-One Chain)
# ══════════════════════════════════════════════════════════════════
# 5m execution  → filtered by 1h VWAP
# 15m execution → filtered by 4h VWAP
# Rule: LONG only if price > filter_tf VWAP
#        SHORT only if price < filter_tf VWAP
# If filter VWAP unavailable (insufficient data): allow trade (skip filter)

# ══════════════════════════════════════════════════════════════════
# SMART MERGE (Execution TF Conflict Resolution)
# ══════════════════════════════════════════════════════════════════
# When BOTH 5m and 15m trigger simultaneously:
#   SAME direction: merge into 1 combined position
#     - Combined size = 5m_size + 15m_size (capped at 2× single max)
#     - SL = tighter of the two SLs
#     - TP1/TP2 = from the LARGER timeframe (15m) — more structural
#     - Log: "MERGED 5m+15m LONG"
#
#   OPPOSITE direction: first-mover wins
#     - Whichever signal was generated first in the same cycle gets the position
#     - The other signal is blocked until position is closed
#     - If truly simultaneous (same cycle): 15m wins (larger TF = higher conviction)
#     - Log: "CONFLICT 5m=LONG vs 15m=SHORT → 15m wins"
#
# When only ONE execution TF triggers: normal single-TF execution
#
# MAX 1 open position at any time (merged or single)

MERGE_MAX_SIZE_MULTIPLIER = 2.0    # Merged position max = 2× normal single-TF size

# ══════════════════════════════════════════════════════════════════
# RISK MANAGEMENT
# ══════════════════════════════════════════════════════════════════

# Position sizing
MAX_RISK_PER_TRADE = 0.02          # 2% of total capital
MIN_ORDER_VALUE_USD = 10.0         # Hyperliquid minimum notional

# Daily limits
DAILY_LOSS_LIMIT = 0.07            # 7% max drawdown per day
DAILY_PAUSE_HOURS = 12
DAILY_RESET_HOUR_UTC = 0

# Cooldown
COOLDOWN_TRIGGER_LOSSES = 3
COOLDOWN_SIZE_MULTIPLIER = 0.5
COOLDOWN_RESET_WINS = 2
COOLDOWN_RESET_SECONDS = 3600      # 1 hour
FULL_STOP_TRIGGER_LOSSES = 3       # Losses during cooldown → full stop
FULL_STOP_DURATION_SECONDS = 14400 # 4 hours

# ══════════════════════════════════════════════════════════════════
# STOP LOSS — 3-LAYER FALLBACK
# ══════════════════════════════════════════════════════════════════

# Layer 1: Wall-based
WALL_PERSISTENCE_SECONDS = 15
WALL_MIN_SIZE_MULTIPLIER = 3.0     # Wall size ≥ 3× avg level size
WALL_SL_OFFSET_PCT = 0.05         # SL placed 0.05% beyond wall

# Layer 2: POC-based
POC_SL_OFFSET_PCT = 0.1

# Layer 3: ATR-based
ATR_SL_MULTIPLIER = 1.5           # SL = entry ± 1.5 × ATR(14)

# Emergency
EMERGENCY_SL_PCT = 3.0            # If all fallbacks > 3% → market exit

# SL monitoring after entry
SL_WALL_SIZE_DECAY_THRESHOLD = 0.5 # Wall size drops below 50% → warning/fallback
SL_ONLY_TIGHTENS = True

# ══════════════════════════════════════════════════════════════════
# TAKE PROFIT — DUAL TP
# ══════════════════════════════════════════════════════════════════
TP1_SIZE_PCT = 0.60                # Exit 60% at TP1
TP1_TARGET = "vwap"
TP1_FALLBACK_ATR_MULT = 1.0       # If near VWAP, use 1.0× ATR instead
TP1_NEAR_VWAP_THRESHOLD = 0.001   # 0.1% = "near VWAP"

TP2_SIZE_PCT = 0.40                # Exit remaining 40% at TP2
TP2_ATR_MULTIPLIER = 2.0          # TP2 = entry ± 2.0 × ATR(14)

# ══════════════════════════════════════════════════════════════════
# FUNDING RATE FILTER — 3 TIER
# ══════════════════════════════════════════════════════════════════
FUNDING_TIER_SAFE = 0.0001         # < 0.01%/hour
FUNDING_TIER_CAUTION = 0.0005      # 0.01% - 0.05%/hour → reduce size 25%
# > 0.05%/hour = danger → skip trade if against position direction
FUNDING_CAUTION_SIZE_MULT = 0.75
FUNDING_POLL_INTERVAL = 60         # Fetch funding rate every 60 seconds

# ══════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════
DASHBOARD_REFRESH_SECONDS = 3.0
# Layout: 4-panel split screen
# Each panel shows: Score, Signal, Order Book stats, HA candles, Volume Profile, indicators
# Bottom bar: Position status, PnL, Risk state, Funding rate

# ══════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════
DB_PATH = "bayesmarket.db"
```

---

## 5. DATA MODELS — `data/state.py`

```python
"""
Core data structures. All state flows through these types.
"""
from dataclasses import dataclass, field
from collections import deque
from typing import Optional
import time
import math


@dataclass
class TradeEvent:
    timestamp: float        # Unix epoch seconds
    price: float
    size: float             # Base asset units
    is_buy: bool            # True = buyer is taker
    notional: float         # price × size (USD)


@dataclass
class Candle:
    timestamp: float        # Open time
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool = False


@dataclass
class BookLevel:
    price: float
    size: float
    num_orders: int


@dataclass
class WallInfo:
    price: float
    size: float
    side: str               # "bid" or "ask"
    first_seen: float
    last_seen: float
    initial_size: float

    @property
    def age_seconds(self) -> float:
        return time.time() - self.first_seen

    @property
    def size_ratio(self) -> float:
        return self.size / self.initial_size if self.initial_size > 0 else 0

    @property
    def is_valid(self) -> bool:
        """Valid = survived 15s AND still ≥ threshold size."""
        return self.age_seconds >= 15.0 and self.size_ratio >= 0.5


@dataclass
class SignalSnapshot:
    """Computed per TF per cycle."""
    timestamp: float
    timeframe: str          # "5m", "15m", "1h", "4h"

    # Raw values
    cvd_zscore_raw: float
    obi_raw: float
    depth_ratio: float
    vwap_value: float
    poc_value: float
    ha_streak: int
    rsi_value: Optional[float]
    macd_histogram: Optional[float]
    ema_short: Optional[float]
    ema_long: Optional[float]
    atr_value: float
    atr_percentile: float

    # Scores (each bounded by their weight)
    cvd_score: float            # [-2.0, +2.0]
    obi_score: float            # [-2.0, +2.0]
    depth_score: float          # [-2.0, +2.0]
    vwap_score: float           # [-1.5, +1.5]
    poc_score: float            # [-1.5, +1.5]
    ha_score: float             # [-1.5, +1.5]
    rsi_score: float            # [-1.0, +1.0]
    macd_score: float           # [-1.0, +1.0]
    ema_score: float            # [-1.0, +1.0]

    # Composites
    category_a: float           # CVD + OBI + Depth [-6.0, +6.0]
    category_b: float           # VWAP + POC + HA [-4.5, +4.5]
    category_c: float           # RSI + MACD + EMA [-3.0, +3.0]
    total_score: float          # [-13.5, +13.5]

    # Regime
    regime: str                 # "trending" or "ranging"
    active_threshold: float

    # MTF filter (only for execution TFs)
    mtf_vwap: Optional[float]
    mtf_aligned_long: bool
    mtf_aligned_short: bool

    # Funding
    funding_rate: float
    funding_tier: str           # "safe", "caution", "danger"

    # Decision
    signal: str                 # "LONG", "SHORT", "NEUTRAL"
    signal_blocked_reason: Optional[str]


@dataclass
class Position:
    side: str                   # "long" or "short"
    entry_price: float
    size: float                 # Total position size (base asset)
    remaining_size: float
    entry_time: float
    source_tfs: list            # ["5m"], ["15m"], or ["5m", "15m"] if merged
    entry_score_5m: Optional[float]
    entry_score_15m: Optional[float]

    sl_price: float
    sl_basis: str               # "wall", "poc", "atr"
    sl_wall_info: Optional[WallInfo]

    tp1_price: float
    tp1_size: float             # 60% of total
    tp1_hit: bool = False
    tp2_price: float
    tp2_size: float             # 40% of total
    tp2_hit: bool = False

    pnl_realized: float = 0.0


@dataclass
class RiskState:
    daily_pnl: float = 0.0
    daily_pnl_reset_time: float = 0.0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    cooldown_active: bool = False
    cooldown_start_time: float = 0.0
    full_stop_active: bool = False
    full_stop_until: float = 0.0
    daily_paused: bool = False
    daily_pause_until: float = 0.0
    trades_today: int = 0


@dataclass
class TimeframeState:
    """Per-TF state. Each of 4 TFs has one instance."""
    name: str                   # "5m", "15m", "1h", "4h"
    role: str                   # "execution" or "filter"
    klines: deque = field(default_factory=lambda: deque(maxlen=200))
    current_kline: Optional[Candle] = None
    signal: Optional[SignalSnapshot] = None
    cvd_history: deque = field(default_factory=lambda: deque(maxlen=100))
    # ^ rolling buffer of CVD raw values for Z-Score computation


@dataclass
class MarketState:
    """Central state. All feeds write here, all engines read from here."""
    # Shared: Order Book (from Hyperliquid)
    bids: list = field(default_factory=list)
    asks: list = field(default_factory=list)
    mid_price: float = 0.0
    book_update_time: float = 0.0

    # Shared: Trades (from Hyperliquid)
    trades: deque = field(default_factory=lambda: deque(maxlen=10000))

    # Shared: Wall tracking
    tracked_walls: list = field(default_factory=list)

    # Per-TF states
    tf_states: dict = field(default_factory=dict)
    # Populated on init: {"5m": TimeframeState, "15m": ..., "1h": ..., "4h": ...}

    # Position (None = no open position, max 1)
    position: Optional[Position] = None

    # Risk
    risk: RiskState = field(default_factory=RiskState)

    # Funding rate (fetched every 60s)
    funding_rate: float = 0.0
    funding_tier: str = "safe"

    # System
    capital: float = 10000.0    # Starting capital for shadow mode simulation
```

---

## 6. INDICATOR FORMULAS — Complete Specification

All formulas apply identically across all 4 TFs. The only per-TF differences are:
- `obi_band_pct` (tighter for faster TFs)
- `z_score_lookback` (same count but different candle intervals → different time windows)
- Kline data source (different Binance intervals per TF)

### 6.1 CVD (Cumulative Volume Delta)
```
INPUT: state.trades (last CVD_WINDOW_SECONDS = 300s), tf_state.cvd_history
CALC:
  1. cvd_raw = SUM(t.notional × (+1 if t.is_buy else -1)) for trades in window
  2. Append cvd_raw to tf_state.cvd_history (deque, maxlen=z_score_lookback)
  3. If len(cvd_history) < 20: return score = 0.0 (insufficient data)
  4. mean = numpy.mean(cvd_history)
     std = numpy.std(cvd_history)
  5. z_score = (cvd_raw - mean) / std if std > 0 else 0.0
  6. cvd_score = 2.0 × math.tanh(z_score / 2.0)
OUTPUT: cvd_score ∈ [-2.0, +2.0]
```

### 6.2 OBI (Order Book Imbalance)
```
INPUT: state.bids, state.asks, state.mid_price, tf_config.obi_band_pct
CALC:
  1. band = mid_price × obi_band_pct / 100
  2. bid_vol = SUM(level.size for bids where price >= mid - band)
  3. ask_vol = SUM(level.size for asks where price <= mid + band)
  4. total = bid_vol + ask_vol
  5. obi_raw = (bid_vol - ask_vol) / total if total > 0 else 0.0
  6. obi_score = obi_raw × 2.0
OUTPUT: obi_score ∈ [-2.0, +2.0]
```

### 6.3 Liquidity Depth
```
INPUT: state.bids, state.asks, state.mid_price
CALC:
  1. band = mid_price × DEPTH_BAND_PCT / 100  (0.5%)
  2. bid_depth = SUM(level.price × level.size for bids where price >= mid - band)
  3. ask_depth = SUM(level.price × level.size for asks where price <= mid + band)
  4. total = bid_depth + ask_depth
  5. depth_score = ((bid_depth - ask_depth) / total) × 2.0 if total > 0 else 0.0
OUTPUT: depth_score ∈ [-2.0, +2.0]
```

### 6.4 VWAP
```
INPUT: tf_state.klines
CALC:
  1. tp = (k.high + k.low + k.close) / 3 for each kline
  2. vwap = SUM(tp × k.volume) / SUM(k.volume)
  3. deviation = (mid_price - vwap) / vwap
  4. vwap_score = clamp(deviation × VWAP_SENSITIVITY, -1.5, +1.5)
OUTPUT: vwap_score ∈ [-1.5, +1.5]
EDGE CASE: If total volume == 0 or no klines: vwap_score = 0.0
```

### 6.5 POC (Point of Control)
```
INPUT: tf_state.klines
CALC:
  1. lo = min(all k.low), hi = max(all k.high)
  2. bin_size = (hi - lo) / VP_BINS if hi > lo else 1
  3. Create VP_BINS bins, distribute each kline's volume across its price range
  4. poc = midpoint of bin with max volume
  5. deviation = (mid_price - poc) / poc
  6. poc_score = clamp(deviation × POC_SENSITIVITY, -1.5, +1.5)
OUTPUT: poc_score ∈ [-1.5, +1.5]
```

### 6.6 Heikin Ashi
```
INPUT: tf_state.klines (last HA_MAX_STREAK + 2 minimum)
CALC:
  1. Compute HA candles:
     ha_close = (o + h + l + c) / 4
     ha_open = (prev_ha_o + prev_ha_c) / 2  (first: (o + c) / 2)
     green = ha_close >= ha_open
  2. Count streak backward from last candle (max 3):
     +N = N consecutive green, -N = N consecutive red
  3. ha_score = (streak / 3.0) × 1.5
OUTPUT: ha_score ∈ [-1.5, +1.5]
```

### 6.7 RSI
```
INPUT: tf_state.klines (minimum RSI_PERIOD + 1)
CALC:
  1. Standard Wilder RSI(14)
  2. Score:
     rsi <= 30: +1.0
     rsi >= 70: -1.0
     30 < rsi < 50: +(50 - rsi) / 20
     50 <= rsi < 70: -(rsi - 50) / 20
OUTPUT: rsi_score ∈ [-1.0, +1.0]
```

### 6.8 MACD
```
INPUT: tf_state.klines, atr_value from regime detection
CALC:
  1. Standard MACD(12, 26, 9) → histogram
  2. normalized = histogram / atr_value if atr_value > 0 else 0.0
  3. macd_score = clamp(normalized, -1.0, +1.0)
OUTPUT: macd_score ∈ [-1.0, +1.0]
```

### 6.9 EMA Cross
```
INPUT: tf_state.klines (minimum EMA_LONG candles)
CALC:
  1. Compute EMA(5) and EMA(20)
  2. spread = (ema5 - ema20) / ema20
  3. ema_score = clamp(spread × EMA_SENSITIVITY, -1.0, +1.0)
OUTPUT: ema_score ∈ [-1.0, +1.0]
```

### 6.10 Regime Detection
```
INPUT: tf_state.klines (minimum ATR_PERIOD + ATR_PERCENTILE_LOOKBACK)
CALC:
  1. Compute ATR(14) for each of last 100 candles
  2. Current ATR percentile among those 100 values
  3. If percentile < ATR_RANGING_PERCENTILE (30):
       regime = "ranging", threshold = scoring_threshold_ranging
     Else:
       regime = "trending", threshold = scoring_threshold
OUTPUT: regime, active_threshold, atr_value, atr_percentile
```

### 6.11 Composite Score
```
INPUT: All individual scores
CALC:
  1. category_a = cvd_score + obi_score + depth_score
  2. category_b = vwap_score + poc_score + ha_score
  3. category_c = rsi_score + macd_score + ema_score
  4. total_score = category_a + category_b + category_c
  5. Signal decision (only for execution TFs):
     total >= active_threshold → "LONG"
     total <= -active_threshold → "SHORT"
     else → "NEUTRAL"
  6. Apply MTF filter (execution TFs only):
     "LONG" but price < mtf_vwap → blocked ("mtf_misaligned")
     "SHORT" but price > mtf_vwap → blocked ("mtf_misaligned")
  7. Apply funding filter:
     "danger" tier AND against direction → blocked ("funding_danger")
  8. Apply risk checks:
     daily_paused → blocked ("daily_paused")
     full_stop → blocked ("full_stop")
     position_open AND not a merge → blocked ("position_open")
OUTPUT: SignalSnapshot
```

---

## 7. SMART MERGE ENGINE — `engine/merge.py`

```
FUNCTION evaluate_merge(signal_5m: SignalSnapshot, signal_15m: SignalSnapshot, state) → MergeDecision:

  s5 = signal_5m.signal   # "LONG", "SHORT", or "NEUTRAL"
  s15 = signal_15m.signal

  # Case 1: Neither triggers
  IF s5 == "NEUTRAL" AND s15 == "NEUTRAL":
    RETURN NoAction

  # Case 2: Only one triggers
  IF s5 != "NEUTRAL" AND s15 == "NEUTRAL":
    RETURN SingleExecution(source="5m", signal=s5)
  IF s5 == "NEUTRAL" AND s15 != "NEUTRAL":
    RETURN SingleExecution(source="15m", signal=s15)

  # Case 3: Both trigger, SAME direction
  IF s5 == s15:
    RETURN MergedExecution(
      direction=s5,
      source_tfs=["5m", "15m"],
      size_multiplier=MERGE_MAX_SIZE_MULTIPLIER,  # 2.0
      sl_source="tighter",    # Use the tighter (closer to entry) SL of the two
      tp_source="15m",        # Use 15m TP targets (more structural)
    )

  # Case 4: Both trigger, OPPOSITE direction
  IF s5 != s15:
    # 15m wins (larger TF = higher conviction)
    RETURN SingleExecution(
      source="15m",
      signal=s15,
      note="conflict_resolved_15m_wins"
    )

POSITION SIZING FOR MERGED:
  - Single execution: normal 2% risk sizing
  - Merged execution: 2% risk × MERGE_MAX_SIZE_MULTIPLIER (2.0) = effectively 4% risk
    BUT capped at 2× normal size (not 2× risk — the SL distance still matters)
    Formula: merged_size = min(size_5m + size_15m, normal_max_size × 2.0)
```

---

## 8. EXECUTION ENGINE — `engine/executor.py`

```
MAIN LOOP (runs per execution TF at its signal_refresh_seconds interval):

  FOR EACH execution TF (5m, 15m):
    1. Compute SignalSnapshot for this TF
    2. Log signal to SQLite
    3. Store in tf_state.signal

  AFTER BOTH execution TFs have computed:
    4. Run smart merge evaluation
    5. IF merge decision = NoAction → continue
    6. IF merge decision = execution:

       ENTRY PIPELINE:
       i.   risk.can_trade(state) → if False, log reason, skip
       ii.  Determine SL (3-layer fallback):
            Wall → POC → ATR → Emergency check
       iii. Calculate position size:
            risk_amount = capital × 0.02
            sl_distance_pct = abs(entry - sl) / entry
            raw_size = risk_amount / (sl_distance_pct × entry)
            Apply: cooldown (×0.5), funding (×0.75), merge (×2.0 if merged)
            Ensure: size × price >= $10
       iv.  Determine TP:
            TP1 = VWAP (or 1.0×ATR fallback if near VWAP)
            TP2 = 2.0×ATR from entry
            (If merged: use 15m TF's VWAP for TP1)
       v.   Create Position object
       vi.  Log full trade plan
       vii. IF LIVE_MODE: submit orders via HL SDK
            IF SHADOW: log "WOULD EXECUTE"

  POSITION MONITORING (every 1 second while position is open):
    1. Wall health check:
       If sl_basis == "wall":
         If wall gone or size < 50% → escalate to next fallback
         SL only tightens, never loosens
    2. TP1 check: if price crosses tp1_price and not tp1_hit → partial exit 60%
    3. TP2 check: if price crosses tp2_price → full exit remaining 40%
    4. SL check: if price crosses sl_price → full exit 100%, record loss
    5. After any exit: update risk state, log to SQLite
```

---

## 9. FEEDS SPECIFICATION

### 9.1 Hyperliquid (shared, one connection)
```
URL: wss://api.hyperliquid.xyz/ws

SUBSCRIPTIONS:
  {"method": "subscribe", "subscription": {"type": "l2Book", "coin": "BTC", "nSigFigs": 5}}
  {"method": "subscribe", "subscription": {"type": "trades", "coin": "BTC"}}

l2Book handler:
  - Parse levels[0]=bids, levels[1]=asks as BookLevel objects
  - Update state.bids, state.asks, state.mid_price, state.book_update_time
  - Run wall tracker update

trades handler:
  - Parse: side "B"=buy taker, "A"=sell taker
  - Create TradeEvent, append to state.trades
  - Prune trades older than TRADE_TTL_SECONDS

RECONNECT: Exponential backoff 1s→2s→4s→8s→max 30s. Resubscribe on reconnect.
```

### 9.2 Binance (4 kline streams, one multiplexed connection)
```
BOOTSTRAP (on startup, 4 REST calls):
  GET /klines?symbol=BTCUSDT&interval=1m&limit=150   → tf_states["5m"].klines
  GET /klines?symbol=BTCUSDT&interval=5m&limit=150   → tf_states["15m"].klines
  GET /klines?symbol=BTCUSDT&interval=15m&limit=100  → tf_states["1h"].klines
  GET /klines?symbol=BTCUSDT&interval=1h&limit=100   → tf_states["4h"].klines

WEBSOCKET (single multiplexed connection):
  URL: wss://stream.binance.com/stream?streams=btcusdt@kline_1m/btcusdt@kline_5m/btcusdt@kline_15m/btcusdt@kline_1h

  Handler per stream:
    - Route by stream name to correct tf_state
    - Update current_kline
    - If candle closed (k.x == True): append to klines, trim to max
```

### 9.3 Wall Tracker (runs on every l2Book update)
```
  1. avg_size = mean(all level sizes both sides)
  2. threshold = avg_size × WALL_MIN_SIZE_MULTIPLIER (3.0)
  3. For each level:
     size >= threshold → add/update in tracked_walls
     size < threshold → remove from tracked_walls
  4. Prune walls not seen in last 2 updates
  5. Wall is "valid for SL" if: age >= 15s AND still above threshold
```

### 9.4 Funding Rate Poller
```
Every FUNDING_POLL_INTERVAL (60s):
  POST {HL_REST_URL}/info  body: {"type": "metaAndAssetCtxs"}
  Parse: find BTC context, extract "funding" field
  Update state.funding_rate and state.funding_tier
```

---

## 10. RISK MODULE

### 10.1 Position Sizing
```
FUNCTION(capital, entry, sl, state, is_merged):
  risk = capital × 0.02
  sl_dist = abs(entry - sl)
  raw_size = risk / sl_dist
  IF cooldown: raw_size × 0.5
  IF funding caution: raw_size × 0.75
  IF is_merged: raw_size × 2.0 (max)
  IF raw_size × entry < $10: SKIP
  RETURN raw_size
```

### 10.2 Limits & Cooldown
```
can_trade():
  Check: daily_paused → False
  Check: full_stop → False
  Check: daily_pnl <= -(capital × 0.07) → set daily_paused, False
  Check: position exists → False
  → True

update_after_trade(pnl):
  daily_pnl += pnl
  trades_today += 1
  IF pnl < 0:
    consecutive_losses += 1, consecutive_wins = 0
    IF consecutive_losses >= 3:
      IF already cooldown → full_stop 4h
      ELSE → activate cooldown
  IF pnl >= 0:
    consecutive_wins += 1, consecutive_losses = 0
    IF cooldown AND consecutive_wins >= 2: deactivate cooldown

cooldown time reset:
  IF cooldown AND elapsed >= 1h: deactivate

daily reset at 00:00 UTC:
  Reset all counters and flags
```

### 10.3 Funding Filter
```
FUNCTION(funding_rate, intended_side):
  against = (rate > 0 AND side == "long") OR (rate < 0 AND side == "short")
  IF NOT against: return ("safe", 1.0)
  IF abs(rate) < 0.0001: return ("safe", 1.0)
  IF abs(rate) < 0.0005: return ("caution", 0.75)
  ELSE: return ("danger", 0.0)  # skip
```

---

## 11. DASHBOARD — `dashboard/terminal.py`

### Layout: 4-Panel Split Screen

```
╔══════════════════════════════╦══════════════════════════════╗
║  BTC 5m  │ Price: $84,250   ║  BTC 15m │ Price: $84,250   ║
║  Score: +8.3 ■■■■■■■■░░     ║  Score: +6.1 ■■■■■■░░░░     ║
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
║  Delta 1m: +$45K ↑           ║  Delta 1m: +$45K ↑           ║
║  POC: $84,100                ║  POC: $84,050                ║
║  [VP bars]                   ║  [VP bars]                   ║
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
║  [Same indicator layout]     ║  [Same indicator layout]     ║
║  [Compact version]           ║  [Compact version]           ║
╠══════════════════════════════╩══════════════════════════════╣
║ POSITION: LONG 0.015 BTC @ $84,250 │ SL: $83,800 (wall)   ║
║ TP1: $84,500 (VWAP) [60%]  TP2: $84,900 (2×ATR) [40%]     ║
║ PnL: +$22.50 (+0.27%) │ Daily: +$145 (+1.45%)              ║
║ Risk: NORMAL │ Funding: 0.003%/h (safe) │ Regime: TRENDING  ║
║ Source: 5m │ Trades today: 4 │ W:3 L:1                      ║
╚═════════════════════════════════════════════════════════════╝
```

**Dashboard components per panel:**
1. **Header:** TF name, role (EXECUTION/FILTER), price, score bar, signal, MTF status
2. **Order Book section:** OBI score + label, Depth score, Buy/Sell walls with prices
3. **Flow section:** CVD Z-Score (raw σ + mapped score), Delta 1m, POC price, Volume Profile bars
4. **Technical section:** RSI + score, MACD + score, EMA cross + score, VWAP + score, HA candle visualization

**Bottom bar (always visible):**
- Current position (if any): side, size, entry, SL, TP1, TP2
- Unrealized PnL (amount + percentage)
- Daily PnL
- Risk state: NORMAL / COOLDOWN / FULL_STOP / DAILY_PAUSED
- Funding rate + tier
- Regime: TRENDING / RANGING
- Source TFs for current position
- Trade stats: count, wins, losses

**Filter TF panels (1h, 4h):**
- Same layout but COMPACT (fewer rows)
- No "Signal" output (marked as "FILTER TF")
- VWAP value prominently displayed (this is what execution TFs use)

---

## 12. DATABASE SCHEMA — `data/storage.py`

```sql
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    timeframe TEXT NOT NULL,        -- "5m", "15m", "1h", "4h"
    mid_price REAL,
    cvd_score REAL,
    obi_score REAL,
    depth_score REAL,
    vwap_score REAL,
    poc_score REAL,
    ha_score REAL,
    rsi_score REAL,
    macd_score REAL,
    ema_score REAL,
    category_a REAL,
    category_b REAL,
    category_c REAL,
    total_score REAL,
    regime TEXT,
    active_threshold REAL,
    atr_value REAL,
    funding_rate REAL,
    signal TEXT,
    blocked_reason TEXT
);
CREATE INDEX idx_signals_tf_ts ON signals(timeframe, timestamp);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_time REAL,
    exit_time REAL,
    side TEXT,
    source_tfs TEXT,               -- "5m", "15m", or "5m+15m"
    entry_price REAL,
    exit_price REAL,
    size REAL,
    sl_price REAL,
    sl_basis TEXT,
    tp1_price REAL,
    tp2_price REAL,
    tp1_hit INTEGER,
    tp2_hit INTEGER,
    exit_reason TEXT,
    pnl REAL,
    pnl_pct REAL,
    entry_score_5m REAL,
    entry_score_15m REAL,
    merge_type TEXT,               -- "single_5m", "single_15m", "merged", "conflict_15m_wins"
    funding_cost REAL,
    cooldown_active INTEGER,
    regime TEXT
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    mid_price REAL,
    best_bid REAL,
    best_ask REAL,
    spread REAL,
    bid_depth_05pct REAL,
    ask_depth_05pct REAL,
    trade_count_1m INTEGER,
    cvd_raw REAL,
    funding_rate REAL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    event_type TEXT,
    details TEXT
);
```

---

## 13. MAIN ENTRY POINT — `main.py`

```
STARTUP:
  1. Load config
  2. Init SQLite (create tables)
  3. Init MarketState with 4 TimeframeState objects
  4. Bootstrap klines from Binance REST (4 calls, one per TF interval)
  5. Log "System starting in SHADOW mode — no credentials required"

ASYNC TASKS (asyncio.gather):
  1. hl_book_feed(state)           — l2Book WebSocket (shared)
  2. hl_trade_feed(state)          — trades WebSocket (shared)
  3. binance_kline_feed(state)     — 4 kline streams multiplexed
  4. signal_loop_5m(state)         — Every 1s: compute 5m signals
  5. signal_loop_15m(state)        — Every 1s: compute 15m signals
  6. signal_loop_1h(state)         — Every 3s: compute 1h signals
  7. signal_loop_4h(state)         — Every 5s: compute 4h signals
  8. merge_and_execute_loop(state) — Every 1s: run smart merge + execution check
  9. position_monitor_loop(state)  — Every 1s: monitor SL/TP/wall health
  10. funding_poller(state)        — Every 60s: fetch funding rate
  11. daily_reset_loop(state)      — Every 60s: check daily reset
  12. snapshot_recorder(state)     — Every 10s: log market snapshot to SQLite
  13. dashboard_loop(state)        — Every 3s: render Rich terminal

SHUTDOWN (SIGINT/SIGTERM):
  Close WebSockets, log final state, close SQLite, exit
```

---

## 14. SHADOW MODE BEHAVIOR

```
Everything is REAL except order placement:
  ✓ All feeds on MAINNET (real market data)
  ✓ All indicators computed on real data
  ✓ All signals logged per TF
  ✓ Smart merge evaluated on real signals
  ✓ Positions simulated (fill at mid_price, no slippage)
  ✓ SL/TP monitored against real price movements
  ✓ PnL calculated on simulated fills
  ✓ Risk state fully functional (cooldown, daily limits)
  ✓ Full dashboard with all panels
  ✗ No orders placed on Hyperliquid
  ✗ No funds at risk
  ✗ No HL credentials needed

SIMULATED FILL LOGIC:
  Entry: fill at state.mid_price at the moment of signal
  TP/SL: triggered when mid_price crosses the level
  Partial exits: calculated proportionally

LOG FORMAT:
  [SHADOW] ENTRY LONG @ $84,250 | Score: 5m=+8.3, 15m=+7.2 | MERGED
  [SHADOW] Size: 0.030 BTC (2×merged) | SL: $83,800 (wall) | TP1: $84,500 | TP2: $84,900
  [SHADOW] TP1 HIT @ $84,500 | Exit 60% (0.018 BTC) | PnL: +$45.00
  [SHADOW] TP2 HIT @ $84,900 | Exit 40% (0.012 BTC) | PnL: +$78.00
  [SHADOW] TRADE CLOSED | Total: +$123.00 (+1.46%) | Duration: 12m | Source: 5m+15m merged
```

---

## 15. ERROR HANDLING

```
RULE: No bare `except: pass` anywhere.

WebSocket: ConnectionClosed → log + exponential backoff + reconnect
           InvalidMessage → log raw message + skip
Indicators: DivisionByZero → return 0.0 + log warning
            InsufficientData → return 0.0 (silent, expected during warmup)
            NaN/Inf → clamp to 0.0 + log error
API:        Funding fetch fail → use last known rate + log warning
            Bootstrap fail → retry 3× with 5s delay, then exit
SQLite:     Write fail → log to stderr + continue
System:     Unhandled exception in async task → log traceback + continue others
            All feeds down >30s → pause trading + log critical
```

---

## 16. VALIDATION CHECKLIST

```
SHADOW MODE VALIDATION (minimum 3 days):
  □ All WebSocket feeds stable (< 5 disconnects/day)
  □ All 4 TF signal computations running at correct intervals
  □ Smart merge triggers logged correctly (merged, single, conflict)
  □ At least 20+ shadow trades logged
  □ SL/TP/wall monitoring functioning (check trade exit reasons)
  □ Risk management triggers (did cooldown activate on 3 losses?)
  □ Daily reset works at 00:00 UTC
  □ Dashboard renders correctly (4 panels, bottom bar, no crashes)
  □ SQLite growth rate acceptable (< 100MB/day)
  □ Memory stable over 24h (no leaks)

METRICS TO EVALUATE:
  □ Win rate per TF and merged
  □ Average PnL per trade
  □ Profit factor (gross profit / gross loss)
  □ Max consecutive losses
  □ Average trade duration
  □ How often MTF filter blocked a signal (and was it correct?)
  □ How often funding filter blocked a signal
  □ Merge frequency (how often do 5m+15m agree?)
```

---

## 17. KNOWN LIMITATIONS

```
1. BTC perp only — no multi-asset
2. Shadow mode fills assume mid_price (no slippage or spread modeling)
3. Order book limited to 20 levels (HL public API limit)
4. Funding rate polled every 60s (not real-time WebSocket)
5. No trailing stop — TP1+TP2 only
6. No Polymarket integration (v2)
7. No alerting (Telegram/Discord) — terminal + SQLite only
8. Binance klines used for TA (not HL native — price correlation is >99.9% but not identical)
9. No web dashboard — Rich terminal only
10. Smart merge logic untested in extreme conditions (flash crashes)
```
