"""TimeframeEngine: computes all signals for one TF.

One instance per timeframe, orchestrates indicators to produce SignalSnapshot.
"""

import asyncio

import structlog

from bayesmarket import config
from bayesmarket.data.state import MarketState
from bayesmarket.data.storage import Storage
from bayesmarket.indicators.correlation import CorrelationTracker
from bayesmarket.indicators.scoring import compute_signal

logger = structlog.get_logger()

# Shared correlation tracker across all TF engines
_correlation_tracker = CorrelationTracker()


class TimeframeEngine:
    """Signal computation engine for a single timeframe."""

    def __init__(self, tf_name: str, state: MarketState, storage: Storage) -> None:
        self.tf_name = tf_name
        self.state = state
        self.storage = storage
        self.tf_cfg = config.TIMEFRAMES[tf_name]
        self.refresh_interval = self.tf_cfg["signal_refresh_seconds"]

    async def run(self) -> None:
        """Main signal computation loop."""
        logger.info("timeframe_engine_started", tf=self.tf_name, interval=self.refresh_interval)

        while True:
            try:
                if self.state.mid_price > 0:
                    snap = compute_signal(self.state, self.tf_name)
                    self.storage.insert_signal(snap, self.state.mid_price)

                    # CRITICAL-3: Track indicator correlations
                    _correlation_tracker.record(snap)
                    _correlation_tracker.maybe_log(self.tf_name, self.storage)

                    if snap.signal != "NEUTRAL":
                        logger.info(
                            "signal_generated",
                            tf=self.tf_name,
                            role=self.tf_cfg.get("role", "unknown"),
                            signal=snap.signal,
                            score=round(snap.total_score, 2),
                            threshold=snap.active_threshold,
                            regime=snap.regime,
                            cascade_dir=snap.cascade_allowed_direction,
                            cascade_ctx=snap.cascade_context_confirmed,
                            cascade_zone=snap.cascade_timing_zone_active,
                            blocked=snap.signal_blocked_reason,
                        )
            except Exception as exc:
                logger.error("signal_computation_failed", tf=self.tf_name, error=str(exc))

            await asyncio.sleep(self.refresh_interval)
