"""Cascade execution — 5m trigger only (no merge needed).

In cascade mode:
  4h (BIAS) → 1h (CONTEXT) → 15m (TIMING) → 5m (TRIGGER)
  Only 5m generates trade signals. The cascade filtering happens
  in scoring.py. This module is a simple pass-through.
"""

from dataclasses import dataclass
from typing import Optional

import structlog

from bayesmarket.data.state import SignalSnapshot

logger = structlog.get_logger()


@dataclass
class MergeDecision:
    """Result of signal evaluation."""
    action: str              # "none" or "single"
    direction: Optional[str] = None   # "LONG" or "SHORT"
    source_tfs: list = None           # type: ignore[assignment]
    merge_type: str = ""
    size_multiplier: float = 1.0
    sl_source: str = ""
    tp_source: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.source_tfs is None:
            self.source_tfs = []


def evaluate_merge(
    signal_5m: Optional[SignalSnapshot],
) -> MergeDecision:
    """Cascade mode: only 5m triggers trades. No merge needed."""
    s5 = signal_5m.signal if signal_5m else "NEUTRAL"

    if s5 == "NEUTRAL":
        return MergeDecision(action="none")

    logger.info("cascade_trigger", signal=s5)
    return MergeDecision(
        action="single",
        direction=s5,
        source_tfs=["5m"],
        merge_type="cascade_5m",
        size_multiplier=1.0,
        sl_source="5m",
        tp_source="5m",
    )
