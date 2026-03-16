"""Indicator correlation tracking (CRITICAL-3).

Tracks pairwise Pearson correlations between the 9 indicator scores
over a rolling window. Logs to DB every CORRELATION_LOG_INTERVAL_SECONDS.
Purpose: identify correlated indicators that inflate composite scores.
"""

import math
import time
from collections import deque

import structlog

from bayesmarket.data.state import SignalSnapshot
from bayesmarket.data.storage import Storage

logger = structlog.get_logger()

INDICATOR_NAMES = [
    "cvd", "obi", "depth", "vwap", "poc", "ha", "rsi", "macd", "ema",
]

# Log correlations every 5 minutes
CORRELATION_LOG_INTERVAL_SECONDS = 300
# Minimum samples needed before computing
CORRELATION_MIN_SAMPLES = 30
# Max rolling window size
CORRELATION_WINDOW_SIZE = 200


class CorrelationTracker:
    """Collects indicator scores per TF and computes pairwise correlations."""

    def __init__(self) -> None:
        self._buffers: dict[str, dict[str, deque]] = {}
        self._last_log_time: dict[str, float] = {}

    def _ensure_tf(self, tf_name: str) -> None:
        if tf_name not in self._buffers:
            self._buffers[tf_name] = {
                name: deque(maxlen=CORRELATION_WINDOW_SIZE)
                for name in INDICATOR_NAMES
            }
            self._last_log_time[tf_name] = 0.0

    def record(self, snap: SignalSnapshot) -> None:
        """Record indicator scores from a signal snapshot."""
        tf = snap.timeframe
        self._ensure_tf(tf)
        buf = self._buffers[tf]
        buf["cvd"].append(snap.cvd_score)
        buf["obi"].append(snap.obi_score)
        buf["depth"].append(snap.depth_score)
        buf["vwap"].append(snap.vwap_score)
        buf["poc"].append(snap.poc_score)
        buf["ha"].append(snap.ha_score)
        buf["rsi"].append(snap.rsi_score)
        buf["macd"].append(snap.macd_score)
        buf["ema"].append(snap.ema_score)

    def maybe_log(self, tf_name: str, storage: Storage) -> None:
        """Log correlations to DB if enough time has passed."""
        now = time.time()
        if now - self._last_log_time.get(tf_name, 0) < CORRELATION_LOG_INTERVAL_SECONDS:
            return

        if tf_name not in self._buffers:
            return

        buf = self._buffers[tf_name]
        n = len(buf["cvd"])
        if n < CORRELATION_MIN_SAMPLES:
            return

        pairs = _compute_all_pairs(buf, n)
        if pairs:
            storage.insert_correlations(now, tf_name, n, pairs)
            self._last_log_time[tf_name] = now
            logger.info(
                "correlation_logged",
                tf=tf_name,
                samples=n,
                pairs=len(pairs),
            )


def _pearson(x: deque, y: deque, n: int) -> float:
    """Compute Pearson correlation coefficient between two deques."""
    if n < 2:
        return 0.0

    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(a * b for a, b in zip(x, y))
    sum_x2 = sum(a * a for a in x)
    sum_y2 = sum(b * b for b in y)

    numerator = n * sum_xy - sum_x * sum_y
    denom_x = n * sum_x2 - sum_x * sum_x
    denom_y = n * sum_y2 - sum_y * sum_y

    if denom_x <= 0 or denom_y <= 0:
        return 0.0

    return numerator / math.sqrt(denom_x * denom_y)


def _compute_all_pairs(
    buf: dict[str, deque], n: int,
) -> list[tuple[str, float]]:
    """Compute pairwise correlations for all indicator pairs."""
    results: list[tuple[str, float]] = []
    for i in range(len(INDICATOR_NAMES)):
        for j in range(i + 1, len(INDICATOR_NAMES)):
            name_a = INDICATOR_NAMES[i]
            name_b = INDICATOR_NAMES[j]
            corr = _pearson(buf[name_a], buf[name_b], n)
            results.append((f"{name_a}_vs_{name_b}", round(corr, 4)))
    return results
