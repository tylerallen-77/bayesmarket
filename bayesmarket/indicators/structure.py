"""Structure indicators: VWAP, POC (Volume Profile), Heikin Ashi."""

from collections import deque
from typing import Optional

import numpy as np
import structlog

from bayesmarket import config
from bayesmarket.data.state import Candle

logger = structlog.get_logger()


def compute_vwap(klines: deque, mid_price: float) -> tuple[float, float]:
    """Compute VWAP and proportional score.

    Returns (vwap_value, vwap_score).
    Score range: [-1.5, +1.5].
    """
    if not klines or mid_price <= 0:
        return 0.0, 0.0

    total_tp_vol = 0.0
    total_vol = 0.0

    for k in klines:
        tp = (k.high + k.low + k.close) / 3.0
        total_tp_vol += tp * k.volume
        total_vol += k.volume

    if total_vol <= 0:
        return 0.0, 0.0

    vwap = total_tp_vol / total_vol
    deviation = (mid_price - vwap) / vwap
    vwap_score = max(-1.5, min(1.5, deviation * config.VWAP_SENSITIVITY))

    return vwap, vwap_score


def compute_poc(klines: deque, mid_price: float) -> tuple[float, float]:
    """Compute Point of Control (highest volume price bin) and proportional score.

    Returns (poc_value, poc_score).
    Score range: [-1.5, +1.5].
    """
    if not klines or mid_price <= 0:
        return 0.0, 0.0

    lo = min(k.low for k in klines)
    hi = max(k.high for k in klines)

    if hi <= lo:
        return mid_price, 0.0

    num_bins = config.VP_BINS
    bin_size = (hi - lo) / num_bins
    volume_bins = np.zeros(num_bins)

    for k in klines:
        if k.volume <= 0:
            continue
        # Distribute volume across price range of candle
        k_lo = max(k.low, lo)
        k_hi = min(k.high, hi)
        if k_hi <= k_lo:
            bin_idx = min(int((k.close - lo) / bin_size), num_bins - 1)
            volume_bins[max(0, bin_idx)] += k.volume
            continue

        start_bin = max(0, int((k_lo - lo) / bin_size))
        end_bin = min(num_bins - 1, int((k_hi - lo) / bin_size))

        bins_covered = end_bin - start_bin + 1
        vol_per_bin = k.volume / bins_covered if bins_covered > 0 else k.volume
        for b in range(start_bin, end_bin + 1):
            volume_bins[b] += vol_per_bin

    poc_bin = int(np.argmax(volume_bins))
    poc = lo + (poc_bin + 0.5) * bin_size

    deviation = (mid_price - poc) / poc if poc > 0 else 0.0
    poc_score = max(-1.5, min(1.5, deviation * config.POC_SENSITIVITY))

    return poc, poc_score


def compute_ha_score(klines: deque) -> tuple[int, float]:
    """Compute Heikin Ashi streak and proportional score.

    Returns (ha_streak, ha_score).
    Score range: [-1.5, +1.5].
    """
    if len(klines) < 3:
        return 0, 0.0

    # Compute HA candles
    candles = list(klines)
    ha_candles: list[tuple[float, float]] = []  # (ha_open, ha_close)

    for i, k in enumerate(candles):
        ha_close = (k.open + k.high + k.low + k.close) / 4.0
        if i == 0:
            ha_open = (k.open + k.close) / 2.0
        else:
            prev_o, prev_c = ha_candles[-1]
            ha_open = (prev_o + prev_c) / 2.0
        ha_candles.append((ha_open, ha_close))

    # Count streak from last candle backward
    max_streak = config.HA_MAX_STREAK
    streak = 0

    for ha_open, ha_close in reversed(ha_candles):
        is_green = ha_close >= ha_open
        if streak == 0:
            streak = 1 if is_green else -1
        elif streak > 0 and is_green:
            streak += 1
        elif streak < 0 and not is_green:
            streak -= 1
        else:
            break

        if abs(streak) >= max_streak:
            break

    streak = max(-max_streak, min(max_streak, streak))
    ha_score = (streak / 3.0) * 1.5

    return streak, ha_score
