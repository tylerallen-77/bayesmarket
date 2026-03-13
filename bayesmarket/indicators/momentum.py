"""Momentum indicators: RSI, MACD, EMA — all proportional scoring."""

from collections import deque
from typing import Optional

import numpy as np
import structlog

from bayesmarket import config

logger = structlog.get_logger()


def compute_rsi(klines: deque) -> tuple[Optional[float], float]:
    """Compute Wilder RSI(14) and proportional score.

    Returns (rsi_value, rsi_score).
    Score range: [-1.0, +1.0].
    """
    period = config.RSI_PERIOD
    if len(klines) < period + 1:
        return None, 0.0

    closes = [k.close for k in klines]
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # Initial averages
    gains = [d if d > 0 else 0.0 for d in deltas[:period]]
    losses = [-d if d < 0 else 0.0 for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # Wilder smoothing
    for d in deltas[period:]:
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        rsi = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

    # Proportional scoring: 30->+1, 50->0, 70->-1
    if rsi <= 30:
        rsi_score = 1.0
    elif rsi >= 70:
        rsi_score = -1.0
    elif rsi < 50:
        rsi_score = (50 - rsi) / 20.0
    else:
        rsi_score = -(rsi - 50) / 20.0

    return rsi, rsi_score


def compute_macd(klines: deque, atr_value: float) -> tuple[Optional[float], float]:
    """Compute MACD histogram and ATR-normalized score.

    Returns (macd_histogram, macd_score).
    Score range: [-1.0, +1.0].
    """
    fast = config.MACD_FAST
    slow = config.MACD_SLOW
    signal_period = config.MACD_SIGNAL

    if len(klines) < slow + signal_period:
        return None, 0.0

    closes = np.array([k.close for k in klines], dtype=np.float64)

    # EMA computation
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = ema_fast - ema_slow

    signal_line = _ema(macd_line, signal_period)
    histogram = float(macd_line[-1] - signal_line[-1])

    if atr_value <= 0:
        return histogram, 0.0

    normalized = histogram / atr_value
    macd_score = max(-1.0, min(1.0, normalized))

    return histogram, macd_score


def compute_ema_score(klines: deque) -> tuple[Optional[float], Optional[float], float]:
    """Compute EMA(5) vs EMA(20) cross score.

    Returns (ema_short, ema_long, ema_score).
    Score range: [-1.0, +1.0].
    """
    if len(klines) < config.EMA_LONG:
        return None, None, 0.0

    closes = np.array([k.close for k in klines], dtype=np.float64)
    ema_short_arr = _ema(closes, config.EMA_SHORT)
    ema_long_arr = _ema(closes, config.EMA_LONG)

    ema5 = float(ema_short_arr[-1])
    ema20 = float(ema_long_arr[-1])

    if ema20 == 0:
        return ema5, ema20, 0.0

    spread = (ema5 - ema20) / ema20
    ema_score = max(-1.0, min(1.0, spread * config.EMA_SENSITIVITY))

    return ema5, ema20, ema_score


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """Compute Exponential Moving Average."""
    alpha = 2.0 / (period + 1)
    result = np.empty_like(data)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result
