"""Core data structures. All state flows through these types."""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bayesmarket.runtime import RuntimeConfig


@dataclass
class TradeEvent:
    """A single trade from the Hyperliquid trade stream."""
    timestamp: float
    price: float
    size: float
    is_buy: bool
    notional: float


@dataclass
class Candle:
    """OHLCV candle — from synthetic builder or Binance fallback."""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool = False


@dataclass
class BookLevel:
    """Single price level in the order book."""
    price: float
    size: float
    num_orders: int = 0


@dataclass
class WallInfo:
    """Detected order book wall using price binning."""
    bin_center: float
    bin_low: float
    bin_high: float
    total_size: float
    side: str  # "bid" or "ask"
    first_seen: float
    last_seen: float
    initial_size: float
    peak_size: float

    @property
    def age_seconds(self) -> float:
        return time.time() - self.first_seen

    @property
    def size_ratio(self) -> float:
        return self.total_size / self.initial_size if self.initial_size > 0 else 0

    @property
    def is_valid(self) -> bool:
        """Valid = survived WALL_PERSISTENCE_SECONDS AND still >= 50% of initial size."""
        from bayesmarket import config
        return (
            self.age_seconds >= config.WALL_PERSISTENCE_SECONDS
            and self.size_ratio >= 0.5
        )


@dataclass
class SignalSnapshot:
    """Computed per TF per cycle."""
    timestamp: float
    timeframe: str

    # Raw values
    cvd_zscore_raw: float = 0.0
    obi_raw: float = 0.0
    depth_ratio: float = 0.0
    vwap_value: float = 0.0
    poc_value: float = 0.0
    ha_streak: int = 0
    rsi_value: Optional[float] = None
    macd_histogram: Optional[float] = None
    ema_short: Optional[float] = None
    ema_long: Optional[float] = None
    atr_value: float = 0.0
    atr_percentile: float = 50.0

    # Scores (each bounded by their weight)
    cvd_score: float = 0.0
    obi_score: float = 0.0
    depth_score: float = 0.0
    vwap_score: float = 0.0
    poc_score: float = 0.0
    ha_score: float = 0.0
    rsi_score: float = 0.0
    macd_score: float = 0.0
    ema_score: float = 0.0

    # Composites
    category_a: float = 0.0
    category_b: float = 0.0
    category_c: float = 0.0
    total_score: float = 0.0

    # Regime
    regime: str = "trending"
    active_threshold: float = 7.0

    # MTF filter (only for execution TFs)
    mtf_vwap: Optional[float] = None
    mtf_aligned_long: bool = True
    mtf_aligned_short: bool = True

    # Funding
    funding_rate: float = 0.0
    funding_tier: str = "safe"

    # Decision
    signal: str = "NEUTRAL"
    signal_blocked_reason: Optional[str] = None


@dataclass
class Position:
    """Open position (max 1 at any time)."""
    side: str  # "long" or "short"
    entry_price: float
    size: float
    remaining_size: float
    entry_time: float
    source_tfs: list = field(default_factory=list)
    entry_score_5m: Optional[float] = None
    entry_score_15m: Optional[float] = None

    sl_price: float = 0.0
    sl_basis: str = "atr"
    sl_wall_info: Optional[WallInfo] = None

    tp1_price: float = 0.0
    tp1_size: float = 0.0
    tp1_hit: bool = False
    tp2_price: float = 0.0
    tp2_size: float = 0.0
    tp2_hit: bool = False

    pnl_realized: float = 0.0

    # Structural swing tracking for SL tightening
    last_swing_low: Optional[float] = None
    last_swing_high: Optional[float] = None

    # Manual force-close flag (set via Telegram /close command)
    _force_close: bool = False


@dataclass
class RiskState:
    """Risk management state machine."""
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
    name: str
    role: str  # "execution" or "filter"
    klines: deque = field(default_factory=lambda: deque(maxlen=200))
    current_kline: Optional[Candle] = None
    signal: Optional[SignalSnapshot] = None
    cvd_history: deque = field(default_factory=lambda: deque(maxlen=100))
    using_fallback: bool = False
    last_hl_trade_time: float = 0.0


@dataclass
class MarketState:
    """Central state. All feeds write here, all engines read from here."""

    # Shared: Order Book
    bids: list = field(default_factory=list)
    asks: list = field(default_factory=list)
    mid_price: float = 0.0
    book_update_time: float = 0.0

    # Shared: Trades
    trades: deque = field(default_factory=lambda: deque(maxlen=10000))

    # Shared: Wall tracking
    tracked_walls: list = field(default_factory=list)

    # Per-TF states
    tf_states: dict = field(default_factory=dict)

    # Position (None = no open position, max 1)
    position: Optional[Position] = None

    # Risk
    risk: RiskState = field(default_factory=RiskState)

    # Funding rate
    funding_rate: float = 0.0
    funding_tier: str = "safe"

    # System
    capital: float = 1000.0
    start_time: float = field(default_factory=time.time)
    kline_source: str = "synthetic"

    # Runtime config (attached in main.py, not default-constructed to avoid import cycle)
    runtime: Optional["RuntimeConfig"] = field(default=None, repr=False)
