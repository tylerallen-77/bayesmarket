"""Order flow indicators: CVD (Z-Score + tanh), OBI, Liquidity Depth."""

import math
import time

import numpy as np
import structlog

from bayesmarket import config
from bayesmarket.data.state import MarketState, TimeframeState

logger = structlog.get_logger()


def compute_cvd_score(state: MarketState, tf_state: TimeframeState) -> tuple[float, float]:
    """Compute CVD z-score and mapped score.

    Returns (cvd_zscore_raw, cvd_score).
    Score range: [-2.0, +2.0].
    """
    now = time.time()
    cutoff = now - config.CVD_WINDOW_SECONDS

    # Sum notional delta over window
    cvd_raw = sum(
        t.notional * (1 if t.is_buy else -1)
        for t in state.trades
        if t.timestamp >= cutoff
    )

    # Append to rolling history for z-score
    tf_state.cvd_history.append(cvd_raw)

    if len(tf_state.cvd_history) < 20:
        return 0.0, 0.0

    history = np.array(tf_state.cvd_history)
    mean = float(np.mean(history))
    std = float(np.std(history))

    z_score = (cvd_raw - mean) / std if std > 0 else 0.0
    cvd_score = 2.0 * math.tanh(z_score / 2.0)

    return z_score, cvd_score


def compute_obi_score(state: MarketState, obi_band_pct: float) -> tuple[float, float]:
    """Compute Order Book Imbalance.

    Returns (obi_raw, obi_score).
    Score range: [-2.0, +2.0].
    """
    mid = state.mid_price
    if mid <= 0 or not state.bids or not state.asks:
        return 0.0, 0.0

    band = mid * obi_band_pct / 100.0

    bid_vol = sum(lvl.size for lvl in state.bids if lvl.price >= mid - band)
    ask_vol = sum(lvl.size for lvl in state.asks if lvl.price <= mid + band)
    total = bid_vol + ask_vol

    if total <= 0:
        return 0.0, 0.0

    obi_raw = (bid_vol - ask_vol) / total
    obi_score = obi_raw * 2.0

    return obi_raw, obi_score


def compute_depth_score(state: MarketState) -> tuple[float, float]:
    """Compute Liquidity Depth score.

    Returns (depth_ratio, depth_score).
    Score range: [-2.0, +2.0].
    """
    mid = state.mid_price
    if mid <= 0 or not state.bids or not state.asks:
        return 0.0, 0.0

    band = mid * config.DEPTH_BAND_PCT / 100.0

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
    total = bid_depth + ask_depth

    if total <= 0:
        return 0.0, 0.0

    depth_ratio = (bid_depth - ask_depth) / total
    depth_score = depth_ratio * 2.0

    return depth_ratio, depth_score
