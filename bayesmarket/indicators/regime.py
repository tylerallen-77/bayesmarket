"""Regime detection: ATR(14) and trending/ranging classification."""

from collections import deque

import numpy as np
import structlog

from bayesmarket import config

logger = structlog.get_logger()


def compute_atr(klines: deque) -> float:
    """Compute ATR(14) from klines.

    Returns current ATR value, or 0.0 if insufficient data.
    """
    period = config.ATR_PERIOD
    if len(klines) < period + 1:
        return 0.0

    candles = list(klines)
    true_ranges: list[float] = []

    for i in range(1, len(candles)):
        high = candles[i].high
        low = candles[i].low
        prev_close = candles[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return 0.0

    # Wilder smoothing ATR
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period

    return atr


def detect_regime(
    klines: deque,
    scoring_threshold: float,
    scoring_threshold_ranging: float,
) -> tuple[str, float, float, float]:
    """Detect market regime based on ATR percentile.

    Returns (regime, active_threshold, atr_value, atr_percentile).
    """
    period = config.ATR_PERIOD
    lookback = config.ATR_PERCENTILE_LOOKBACK

    if len(klines) < period + lookback:
        # Insufficient data — default to trending
        atr = compute_atr(klines)
        return "trending", scoring_threshold, atr, 50.0

    candles = list(klines)
    true_ranges: list[float] = []
    for i in range(1, len(candles)):
        high = candles[i].high
        low = candles[i].low
        prev_close = candles[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    # Compute ATR for each of the last `lookback` positions
    atr_values: list[float] = []
    for end_idx in range(len(true_ranges) - lookback, len(true_ranges)):
        if end_idx < period:
            continue
        window = true_ranges[end_idx - period : end_idx]
        atr_val = sum(window) / period
        atr_values.append(atr_val)

    if not atr_values:
        atr = compute_atr(klines)
        return "trending", scoring_threshold, atr, 50.0

    current_atr = atr_values[-1]
    percentile = float(np.percentile(atr_values, config.ATR_RANGING_PERCENTILE))

    atr_percentile_rank = float(
        np.sum(np.array(atr_values) <= current_atr) / len(atr_values) * 100
    )

    if atr_percentile_rank < config.ATR_RANGING_PERCENTILE:
        regime = "ranging"
        threshold = scoring_threshold_ranging
    else:
        regime = "trending"
        threshold = scoring_threshold

    return regime, threshold, current_atr, atr_percentile_rank
