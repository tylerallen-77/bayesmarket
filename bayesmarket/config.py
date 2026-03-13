"""BayesMarket MVP Configuration — All parameters locked."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════════════════════════
# CAPITAL & MODE
# ══════════════════════════════════════════════════════════════════
LIVE_MODE = False  # False = shadow mode (no orders, no credentials needed)

# Capital source depends on mode:
# - Shadow mode: uses SIMULATED_CAPITAL below (no API call)
# - Live mode: auto-fetches actual USDC balance from Hyperliquid account
#   Falls back to SIMULATED_CAPITAL if fetch fails (with warning log)
SIMULATED_CAPITAL = 1000.0

# ══════════════════════════════════════════════════════════════════
# ASSET
# ══════════════════════════════════════════════════════════════════
COIN = "BTC"
BINANCE_SYMBOL = "BTCUSDT"  # Used for FUTURES, not spot

# ══════════════════════════════════════════════════════════════════
# MULTI-TIMEFRAME ARCHITECTURE
# ══════════════════════════════════════════════════════════════════
TIMEFRAMES = {
    "5m": {
        "role": "execution",
        "kline_interval": "1m",
        "kline_interval_seconds": 60,
        "kline_bootstrap": 150,
        "kline_max": 200,
        "mtf_filter_tf": "1h",
        "scoring_threshold": 7.0,
        "scoring_threshold_ranging": 9.0,
        "obi_band_pct": 0.5,
        "z_score_lookback": 100,
        "signal_refresh_seconds": 1.0,
    },
    "15m": {
        "role": "execution",
        "kline_interval": "5m",
        "kline_interval_seconds": 300,
        "kline_bootstrap": 150,
        "kline_max": 200,
        "mtf_filter_tf": "4h",
        "scoring_threshold": 7.0,
        "scoring_threshold_ranging": 8.5,
        "obi_band_pct": 0.75,
        "z_score_lookback": 100,
        "signal_refresh_seconds": 1.0,
    },
    "1h": {
        "role": "filter",
        "kline_interval": "15m",
        "kline_interval_seconds": 900,
        "kline_bootstrap": 100,
        "kline_max": 150,
        "mtf_filter_tf": None,
        "scoring_threshold": 7.0,
        "scoring_threshold_ranging": 8.0,
        "obi_band_pct": 1.0,
        "z_score_lookback": 100,
        "signal_refresh_seconds": 3.0,
    },
    "4h": {
        "role": "filter",
        "kline_interval": "1h",
        "kline_interval_seconds": 3600,
        "kline_bootstrap": 100,
        "kline_max": 150,
        "mtf_filter_tf": None,
        "scoring_threshold": 7.0,
        "scoring_threshold_ranging": 8.0,
        "obi_band_pct": 1.0,
        "z_score_lookback": 100,
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

# Binance FUTURES (fallback only — not primary)
BINANCE_FUTURES_WS_URL = "wss://fstream.binance.com/stream"
BINANCE_FUTURES_REST_URL = "https://fapi.binance.com/fapi/v1"

# HL credentials (optional — only for LIVE_MODE)
HL_PRIVATE_KEY = os.getenv("HL_PRIVATE_KEY", "")
HL_ACCOUNT_ADDRESS = os.getenv("HL_ACCOUNT_ADDRESS", "")

# ══════════════════════════════════════════════════════════════════
# KLINE SOURCE
# ══════════════════════════════════════════════════════════════════
KLINE_SOURCE = "synthetic"  # "synthetic" = build from HL trades (primary)
KLINE_FALLBACK_ENABLED = True
KLINE_FALLBACK_STALE_SECONDS = 10  # If no HL trade for 10s, switch to fallback

# ══════════════════════════════════════════════════════════════════
# DATA RETENTION
# ══════════════════════════════════════════════════════════════════
TRADE_TTL_SECONDS = 600  # Keep 10 min of HL trades in memory
OB_SNAPSHOT_INTERVAL = 0.5

# ══════════════════════════════════════════════════════════════════
# SCORING WEIGHTS (same for all TFs)
# ══════════════════════════════════════════════════════════════════
WEIGHTS = {
    # Category A: Order Flow (leading) — max ±6.0 total
    "cvd": 2.0,
    "obi": 2.0,
    "depth": 2.0,
    # Category B: Structure & Equilibrium — max ±4.5 total
    "vwap": 1.5,
    "poc": 1.5,
    "ha": 1.5,
    # Category C: Momentum (lagging) — max ±3.0 total
    "rsi": 1.0,
    "macd": 1.0,
    "ema": 1.0,
}
# Theoretical max: ±13.5

# ══════════════════════════════════════════════════════════════════
# INDICATOR PARAMETERS
# ══════════════════════════════════════════════════════════════════

# CVD
CVD_WINDOW_SECONDS = 300
CVD_MAPPING = "tanh"

# OBI — obi_band_pct is per-TF (see TIMEFRAMES dict)

# Liquidity Depth
DEPTH_BAND_PCT = 0.5

# VWAP
VWAP_SENSITIVITY = 150.0

# POC
VP_BINS = 30
POC_SENSITIVITY = 150.0

# Heikin Ashi
HA_MAX_STREAK = 3
HA_DISPLAY_COUNT = 8

# RSI
RSI_PERIOD = 14
RSI_OB = 70
RSI_OS = 30

# MACD
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# EMA
EMA_SHORT = 5
EMA_LONG = 20
EMA_SENSITIVITY = 200.0

# ══════════════════════════════════════════════════════════════════
# REGIME DETECTION
# ══════════════════════════════════════════════════════════════════
ATR_PERIOD = 14
ATR_RANGING_PERCENTILE = 30
ATR_PERCENTILE_LOOKBACK = 100

# ══════════════════════════════════════════════════════════════════
# SMART MERGE
# ══════════════════════════════════════════════════════════════════
MERGE_MAX_SIZE_MULTIPLIER = 2.0

# ══════════════════════════════════════════════════════════════════
# RISK MANAGEMENT
# ══════════════════════════════════════════════════════════════════

# Position sizing
MAX_RISK_PER_TRADE = 0.02
MAX_LEVERAGE = 5.0
MIN_ORDER_VALUE_USD = 10.0

# Daily limits
DAILY_LOSS_LIMIT = 0.07
DAILY_PAUSE_HOURS = 12
DAILY_RESET_HOUR_UTC = 0

# Cooldown
COOLDOWN_TRIGGER_LOSSES = 3
COOLDOWN_SIZE_MULTIPLIER = 0.5
COOLDOWN_RESET_WINS = 2
COOLDOWN_RESET_SECONDS = 3600
FULL_STOP_TRIGGER_LOSSES = 3
FULL_STOP_DURATION_SECONDS = 14400

# ══════════════════════════════════════════════════════════════════
# STOP LOSS — 3-LAYER FALLBACK
# ══════════════════════════════════════════════════════════════════

# Layer 1: Wall-based (with price binning)
WALL_BIN_SIZE = 10.0  # Group levels into $10 bins
WALL_PERSISTENCE_SECONDS = 5.0  # Reduced from 15s (with binning this is sufficient)
WALL_MIN_SIZE_MULTIPLIER = 3.0
WALL_SL_OFFSET_PCT = 0.05

# Layer 2: POC-based
POC_SL_OFFSET_PCT = 0.1

# Layer 3: ATR-based
ATR_SL_MULTIPLIER = 1.5

# Emergency
EMERGENCY_SL_PCT = 3.0

# SL monitoring after entry
SL_WALL_SIZE_DECAY_THRESHOLD = 0.5
SL_ONLY_TIGHTENS = True
SL_TIGHTEN_MODE = "structural"
SL_MIN_DISTANCE_ATR_MULT = 0.3
SL_STRUCTURAL_CONFIRMATION_PCT = 0.003

# ══════════════════════════════════════════════════════════════════
# TAKE PROFIT — DUAL TP
# ══════════════════════════════════════════════════════════════════
TP1_SIZE_PCT = 0.60
TP1_TARGET = "vwap"
TP1_FALLBACK_ATR_MULT = 1.0
TP1_NEAR_VWAP_THRESHOLD = 0.001

TP2_SIZE_PCT = 0.40
TP2_ATR_MULTIPLIER = 2.0

# ══════════════════════════════════════════════════════════════════
# FUNDING RATE FILTER — 3 TIER
# ══════════════════════════════════════════════════════════════════
FUNDING_TIER_SAFE = 0.0001
FUNDING_TIER_CAUTION = 0.0005
FUNDING_CAUTION_SIZE_MULT = 0.75
FUNDING_POLL_INTERVAL = 60

# ══════════════════════════════════════════════════════════════════
# ORDER TYPES (for LIVE_MODE)
# ══════════════════════════════════════════════════════════════════
ENTRY_ORDER_TYPE = "limit_post_only"
ENTRY_ORDER_TIMEOUT_SECONDS = 5
ENTRY_ORDER_MAX_RETRIES = 3
ENTRY_ORDER_PRICE_OFFSET_TICKS = 1
SL_ORDER_TYPE = "stop_market"
TP_ORDER_TYPE = "limit_gtc"
TP_PARTIAL_FILL_TIMEOUT = 60
TP_PARTIAL_FILL_ESCALATION = "ioc"

# ══════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════
DASHBOARD_REFRESH_SECONDS = 3.0

# ══════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════
DB_PATH = Path("bayesmarket.db")
