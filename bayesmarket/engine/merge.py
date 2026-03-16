"""Smart merge logic for 5m + 15m signal alignment.

Philosophy: MTF signals are COMPLEMENTARY, not competing.
  - Same direction = confirmed entry (boost size)
  - One neutral   = single TF entry (normal size)
  - Conflict      = SKIP (choppy market, no edge)

Changed from original:
  Case 5 (conflict) → skip trade entirely, not 15m wins.
  Rationale: 5m SHORT + 15m LONG = ranging/choppy = no clear bias.
"""

from dataclasses import dataclass
from typing import Optional

import structlog

from bayesmarket import config
from bayesmarket.data.state import SignalSnapshot

logger = structlog.get_logger()


@dataclass
class MergeDecision:
    """Result of merge evaluation."""
    action: str              # "none", "single", "merged"
    direction: Optional[str] = None   # "LONG" or "SHORT"
    source_tfs: list = None           # type: ignore[assignment]
    merge_type: str = ""
    size_multiplier: float = 1.0
    sl_source: str = ""               # "5m", "15m", or "tighter"
    tp_source: str = ""               # "5m", "15m"
    note: str = ""

    def __post_init__(self) -> None:
        if self.source_tfs is None:
            self.source_tfs = []


def evaluate_merge(
    signal_5m: Optional[SignalSnapshot],
    signal_15m: Optional[SignalSnapshot],
) -> MergeDecision:
    """Evaluate 5m + 15m signal alignment.

    Returns MergeDecision describing action to take.
    """
    s5 = signal_5m.signal if signal_5m else "NEUTRAL"
    s15 = signal_15m.signal if signal_15m else "NEUTRAL"

    # Case 1: Neither triggers
    if s5 == "NEUTRAL" and s15 == "NEUTRAL":
        return MergeDecision(action="none")

    # Case 2: Only 5m triggers
    if s5 != "NEUTRAL" and s15 == "NEUTRAL":
        logger.info("merge_single_5m", signal=s5)
        return MergeDecision(
            action="single",
            direction=s5,
            source_tfs=["5m"],
            merge_type="single_5m",
            size_multiplier=1.0,
            sl_source="5m",
            tp_source="5m",
        )

    # Case 3: Only 15m triggers
    if s5 == "NEUTRAL" and s15 != "NEUTRAL":
        logger.info("merge_single_15m", signal=s15)
        return MergeDecision(
            action="single",
            direction=s15,
            source_tfs=["15m"],
            merge_type="single_15m",
            size_multiplier=1.0,
            sl_source="15m",
            tp_source="15m",
        )

    # Case 4: Both trigger, SAME direction — confirmed, boost size
    if s5 == s15:
        logger.info("merge_confirmed", direction=s5)
        return MergeDecision(
            action="merged",
            direction=s5,
            source_tfs=["5m", "15m"],
            merge_type="merged",
            size_multiplier=config.MERGE_MAX_SIZE_MULTIPLIER,
            sl_source="tighter",
            tp_source="15m",
        )

    # Case 5: Both trigger, OPPOSITE direction — SKIP (market is choppy)
    # Original behavior was: 15m wins. Changed to: skip.
    # Rationale: conflicting signals = no clear directional bias = no edge.
    logger.info("merge_conflict_skip", s5=s5, s15=s15)
    return MergeDecision(
        action="none",
        note=f"conflict_skip: 5m={s5} vs 15m={s15}, no trade",
    )
