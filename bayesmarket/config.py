"""BayesMarket Configuration.

CARA SWITCH MODE:
  1. Via Telegram: /live atau /shadow command
  2. Via .env:  LIVE_MODE=True dan restart bot
  3. Via config: ubah LIVE_MODE di bawah dan restart

Semua parameter yang bisa diubah saat runtime ada di RuntimeConfig (runtime.py).
Config ini adalah default / static values.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════════════════════════
# MODE — Bisa diubah via Telegram tanpa restart
# ══════════════════════════════════════════════════════════════════
LIVE_MODE = os.getenv("LIVE_MODE", "false").lower() == "true"
# False = shadow mode (default, aman, tidak ada order nyata)
# True  = live mode (order nyata di Hyperliquid)

SIMULATED_CAPITAL = float(os.getenv("SIMULATED_CAPITAL", "1000.0"))

# ══════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# Cara dapatkan:
#   Token: @BotFather → /newbot
#   Chat ID: @userinfobot atau kirim pesan ke bot lalu cek /getUpdates

# ══════════════════════════════════════════════════════════════════
# ASSET
# ══════════════════════════════════════════════════════════════════
COIN = os.getenv("COIN", "BTC")
BINANCE_SYMBOL = os.getenv("BINANCE_SYMBOL", "BTCUSDT")

# ══════════════════════════════════════════════════════════════════
# MULTI-TIMEFRAME CASCADE ARCHITECTURE
# 4h (BIAS) → 1h (CONTEXT) → 15m (TIMING) → 5m (TRIGGER)
# ══════════════════════════════════════════════════════════════════
TIMEFRAMES = {
    "5m": {
        "role": "trigger",
        "cascade_parent": "15m",
        "kline_interval": "1m",
        "kline_interval_seconds": 60,
        "kline_bootstrap": 150,
        "kline_max": 200,
        "scoring_threshold": 7.0,
        "scoring_threshold_ranging": 9.0,
        "obi_band_pct": 0.5,
        "z_score_lookback": 100,
        "signal_refresh_seconds": 1.0,
    },
    "15m": {
        "role": "timing",
        "cascade_parent": "1h",
        "kline_interval": "5m",
        "kline_interval_seconds": 300,
        "kline_bootstrap": 150,
        "kline_max": 200,
        "scoring_threshold": 7.0,
        "scoring_threshold_ranging": 8.5,
        "obi_band_pct": 0.75,
        "z_score_lookback": 100,
        "signal_refresh_seconds": 1.0,
    },
    "1h": {
        "role": "context",
        "cascade_parent": "4h",
        "kline_interval": "15m",
        "kline_interval_seconds": 900,
        "kline_bootstrap": 100,
        "kline_max": 150,
        "scoring_threshold": 7.0,
        "scoring_threshold_ranging": 8.0,
        "obi_band_pct": 1.0,
        "z_score_lookback": 100,
        "signal_refresh_seconds": 3.0,
    },
    "4h": {
        "role": "bias",
        "cascade_parent": None,
        "kline_interval": "1h",
        "kline_interval_seconds": 3600,
        "kline_bootstrap": 100,
        "kline_max": 150,
        "scoring_threshold": 7.0,
        "scoring_threshold_ranging": 8.0,
        "obi_band_pct": 1.0,
        "z_score_lookback": 100,
        "signal_refresh_seconds": 5.0,
    },
}

# ══════════════════════════════════════════════════════════════════
# CASCADE MTF PARAMETERS
# ══════════════════════════════════════════════════════════════════
CASCADE_BIAS_THRESHOLD = 3.0       # 4h score > +3 = LONG only, < -3 = SHORT only
CASCADE_CONTEXT_SAME_SIGN = True   # 1h must match 4h direction sign
CASCADE_TIMING_ZONE_TTL = 900      # 15m zone valid for 15 minutes (1x period of 15m candle)

# ══════════════════════════════════════════════════════════════════
# CONNECTIONS
# ══════════════════════════════════════════════════════════════════
HL_REST_URL = os.getenv("HL_REST_URL", "https://api.hyperliquid.xyz")
HL_WS_URL   = os.getenv("HL_WS_URL",   "wss://api.hyperliquid.xyz/ws")
IS_TESTNET  = "testnet" in HL_REST_URL

DEPLOYMENT_ENV = os.getenv("DEPLOYMENT_ENV", "local")
# Values: "railway" | "vps" | "local"
# Used to toggle features incompatible with Railway (e.g. terminal dashboard)

IS_RAILWAY = DEPLOYMENT_ENV == "railway"
HL_L2_BOOK_LEVELS = 50          # ditingkatkan dari 20 untuk wall detection
HL_L2_SIG_FIGS = 5              # 5 sig figs → $1 resolution for precise wall detection

BINANCE_FUTURES_WS_URL = "wss://fstream.binance.com/stream"
BINANCE_FUTURES_REST_URL = "https://fapi.binance.com/fapi/v1"

HL_PRIVATE_KEY = os.getenv("HL_PRIVATE_KEY", "")
HL_ACCOUNT_ADDRESS = os.getenv("HL_ACCOUNT_ADDRESS", "")

# ══════════════════════════════════════════════════════════════════
# KLINE SOURCE
# ══════════════════════════════════════════════════════════════════
KLINE_SOURCE = "binance_futures"
KLINE_FALLBACK_ENABLED = True
KLINE_FALLBACK_STALE_SECONDS = 10

# ══════════════════════════════════════════════════════════════════
# DATA RETENTION
# ══════════════════════════════════════════════════════════════════
TRADE_TTL_SECONDS = 600
OB_SNAPSHOT_INTERVAL = 0.5

# ══════════════════════════════════════════════════════════════════
# SCORING WEIGHTS — dokumentasi; baked ke indicator functions
# ══════════════════════════════════════════════════════════════════
WEIGHTS = {
    "cvd": 2.0,    # compute_cvd returns [-2, +2]
    "obi": 2.0,    # compute_obi returns [-2, +2]
    "depth": 2.0,  # compute_depth returns [-2, +2]
    "vwap": 1.5,   # compute_vwap clamped to ±1.5
    "poc": 1.5,    # compute_poc clamped to ±1.5
    "ha": 1.5,     # compute_ha clamped to ±1.5
    "rsi": 1.0,    # compute_rsi returns [-1, +1]
    "macd": 1.0,   # compute_macd clamped to ±1.0
    "ema": 1.0,    # compute_ema clamped to ±1.0
}
# Max total: ±13.5

# ══════════════════════════════════════════════════════════════════
# INDICATOR PARAMETERS
# ══════════════════════════════════════════════════════════════════
CVD_WINDOW_SECONDS = 300
CVD_MAPPING = "tanh"

DEPTH_BAND_PCT = 0.5

# VWAP — diturunkan dari 150 ke 20 untuk mengurangi saturation
VWAP_SENSITIVITY = 20.0

# POC — sama
POC_SENSITIVITY = 20.0
VP_BINS = 30

HA_MAX_STREAK = 5              # dinaikkan dari 3 → butuh lebih banyak konfirmasi

RSI_PERIOD = 14
RSI_OB = 70
RSI_OS = 30

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

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
# EXECUTION (cascade mode: 5m trigger only, no merge)
# ══════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════
# RISK MANAGEMENT
# ══════════════════════════════════════════════════════════════════
MAX_RISK_PER_TRADE = 0.02
MAX_LEVERAGE = 5.0
MIN_ORDER_VALUE_USD = 10.0

DAILY_LOSS_LIMIT = 0.07
DAILY_PAUSE_HOURS = 12
DAILY_RESET_HOUR_UTC = 0

COOLDOWN_TRIGGER_LOSSES = 3
COOLDOWN_SIZE_MULTIPLIER = 0.5
COOLDOWN_RESET_WINS = 2
COOLDOWN_RESET_SECONDS = 3600
FULL_STOP_TRIGGER_LOSSES = 3
FULL_STOP_DURATION_SECONDS = 14400

# ══════════════════════════════════════════════════════════════════
# STOP LOSS
# ══════════════════════════════════════════════════════════════════
WALL_BIN_SIZE = 20.0           # ditingkatkan dari 10 → aggregasi lebih baik
WALL_PERSISTENCE_SECONDS = 3.0 # diturunkan dari 5 → lebih responsif
WALL_PRUNE_SECONDS = 6.0       # baru: prune setelah 6s (> persistence)
WALL_MIN_SIZE_MULTIPLIER = 1.5 # diturunkan dari 2.0 → more sensitive for HL volume
WALL_SL_OFFSET_PCT = 0.05

POC_SL_OFFSET_PCT = 0.1
POC_SL_MIN_DISTANCE_PCT = 0.3     # skip POC as SL if too close to entry
ATR_SL_MULTIPLIER = 1.5
EMERGENCY_SL_PCT = 3.0

SL_WALL_SIZE_DECAY_THRESHOLD = 0.5
SL_ONLY_TIGHTENS = True
SL_TIGHTEN_MODE = "structural"
SL_MIN_DISTANCE_ATR_MULT = 0.3
SL_STRUCTURAL_CONFIRMATION_PCT = 0.003

# Trailing stop after TP1 hit (MOD-5)
TRAILING_STOP_ENABLED = True
TRAILING_STOP_ACTIVATION_ATR = 0.5   # activate trail after price moves 0.5 ATR past entry
TRAILING_STOP_DISTANCE_ATR = 0.75    # trail SL at 0.75 ATR behind highest/lowest price

# SL/TP ratio guard — prevents absurd RR from stale POC/wall levels
# If SL distance > MAX_SL_TP_RATIO * TP1 distance, cap SL to ratio limit
MAX_SL_TP_RATIO = 3.0

# ══════════════════════════════════════════════════════════════════
# TAKE PROFIT
# ══════════════════════════════════════════════════════════════════
TP1_SIZE_PCT = 0.60
TP1_TARGET = "vwap"
TP1_FALLBACK_ATR_MULT = 1.0
TP1_NEAR_VWAP_THRESHOLD = 0.001

TP2_SIZE_PCT = 0.40
TP2_ATR_MULTIPLIER = 2.0

# Time-based exit: tutup posisi jika tidak hit TP1 dalam X menit
TIME_EXIT_ENABLED = True
TIME_EXIT_MINUTES_5M = 30   # 5m TF: max 30 menit
TIME_EXIT_MINUTES_15M = 90  # 15m TF: max 90 menit

# ══════════════════════════════════════════════════════════════════
# FUNDING RATE
# ══════════════════════════════════════════════════════════════════
FUNDING_TIER_SAFE = 0.0001
FUNDING_TIER_CAUTION = 0.0005
FUNDING_CAUTION_SIZE_MULT = 0.75
FUNDING_POLL_INTERVAL = 60

# ══════════════════════════════════════════════════════════════════
# ORDER TYPES (LIVE MODE)
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
WEB_DASHBOARD = os.getenv("WEB_DASHBOARD", "false").lower() == "true" or IS_RAILWAY
WEB_DASHBOARD_PORT = int(os.getenv("PORT", "8080"))

# ══════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════
DB_PATH = Path(os.getenv("DB_PATH", "bayesmarket.db"))
