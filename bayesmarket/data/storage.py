"""SQLite interface — create tables, insert, query."""

import sqlite3
import time
from pathlib import Path
from typing import Optional

import structlog

from bayesmarket import config
from bayesmarket.data.state import MarketState, Position, SignalSnapshot

logger = structlog.get_logger()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    timeframe TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_signals_tf_ts ON signals(timeframe, timestamp);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_time REAL,
    exit_time REAL,
    side TEXT,
    source_tfs TEXT,
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
    merge_type TEXT,
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
"""


class Storage:
    """Thread-safe SQLite storage for all BayesMarket data."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or config.DB_PATH
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()
        logger.info("storage_initialized", db_path=str(self.db_path))

    def _init_schema(self) -> None:
        """Create all tables if they don't exist."""
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def insert_signal(self, signal: SignalSnapshot, mid_price: float) -> None:
        """Log a signal computation to the signals table."""
        try:
            self.conn.execute(
                """INSERT INTO signals (
                    timestamp, timeframe, mid_price,
                    cvd_score, obi_score, depth_score,
                    vwap_score, poc_score, ha_score,
                    rsi_score, macd_score, ema_score,
                    category_a, category_b, category_c, total_score,
                    regime, active_threshold, atr_value,
                    funding_rate, signal, blocked_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal.timestamp,
                    signal.timeframe,
                    mid_price,
                    signal.cvd_score,
                    signal.obi_score,
                    signal.depth_score,
                    signal.vwap_score,
                    signal.poc_score,
                    signal.ha_score,
                    signal.rsi_score,
                    signal.macd_score,
                    signal.ema_score,
                    signal.category_a,
                    signal.category_b,
                    signal.category_c,
                    signal.total_score,
                    signal.regime,
                    signal.active_threshold,
                    signal.atr_value,
                    signal.funding_rate,
                    signal.signal,
                    signal.signal_blocked_reason,
                ),
            )
            self.conn.commit()
        except Exception as exc:
            logger.error("insert_signal_failed", error=str(exc), tf=signal.timeframe)

    def insert_trade(
        self,
        position: Position,
        exit_price: float,
        exit_reason: str,
        pnl: float,
        pnl_pct: float,
        merge_type: str,
        regime: str,
        funding_cost: float = 0.0,
    ) -> None:
        """Log a completed trade."""
        try:
            self.conn.execute(
                """INSERT INTO trades (
                    entry_time, exit_time, side, source_tfs,
                    entry_price, exit_price, size,
                    sl_price, sl_basis, tp1_price, tp2_price,
                    tp1_hit, tp2_hit, exit_reason,
                    pnl, pnl_pct, entry_score_5m, entry_score_15m,
                    merge_type, funding_cost, cooldown_active, regime
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    position.entry_time,
                    time.time(),
                    position.side,
                    "+".join(position.source_tfs),
                    position.entry_price,
                    exit_price,
                    position.size,
                    position.sl_price,
                    position.sl_basis,
                    position.tp1_price,
                    position.tp2_price,
                    int(position.tp1_hit),
                    int(position.tp2_hit),
                    exit_reason,
                    pnl,
                    pnl_pct,
                    position.entry_score_5m,
                    position.entry_score_15m,
                    merge_type,
                    funding_cost,
                    0,  # cooldown_active tracked separately
                    regime,
                ),
            )
            self.conn.commit()
        except Exception as exc:
            logger.error("insert_trade_failed", error=str(exc))

    def insert_snapshot(self, state: MarketState) -> None:
        """Log a market snapshot every 10s."""
        try:
            now = time.time()
            best_bid = state.bids[0].price if state.bids else 0.0
            best_ask = state.asks[0].price if state.asks else 0.0
            spread = best_ask - best_bid if best_bid and best_ask else 0.0

            # Compute depth within ±0.5% of mid
            mid = state.mid_price
            band = mid * 0.005 if mid > 0 else 0
            bid_depth = sum(
                lvl.price * lvl.size
                for lvl in state.bids
                if lvl.price >= mid - band
            )
            ask_depth = sum(
                lvl.price * lvl.size
                for lvl in state.asks
                if lvl.price <= mid + band
            )

            # Trade count in last 60s
            cutoff = now - 60
            trade_count = sum(1 for t in state.trades if t.timestamp >= cutoff)

            # CVD raw
            cvd_raw = sum(
                t.notional * (1 if t.is_buy else -1)
                for t in state.trades
                if t.timestamp >= now - 300
            )

            self.conn.execute(
                """INSERT INTO market_snapshots (
                    timestamp, mid_price, best_bid, best_ask, spread,
                    bid_depth_05pct, ask_depth_05pct,
                    trade_count_1m, cvd_raw, funding_rate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now,
                    mid,
                    best_bid,
                    best_ask,
                    spread,
                    bid_depth,
                    ask_depth,
                    trade_count,
                    cvd_raw,
                    state.funding_rate,
                ),
            )
            self.conn.commit()
        except Exception as exc:
            logger.error("insert_snapshot_failed", error=str(exc))

    def insert_event(self, event_type: str, details: str) -> None:
        """Log a system event."""
        try:
            self.conn.execute(
                "INSERT INTO events (timestamp, event_type, details) VALUES (?, ?, ?)",
                (time.time(), event_type, details),
            )
            self.conn.commit()
        except Exception as exc:
            logger.error("insert_event_failed", error=str(exc), event_type=event_type)

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
        logger.info("storage_closed")
