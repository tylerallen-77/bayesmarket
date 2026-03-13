"""Smart merge logic for 5m + 15m signal conflicts.

4 cases: neither triggers, one triggers, same direction, opposite direction.
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

    action: str  # "none", "single", "merged"
    direction: Optional[str] = None  # "LONG" or "SHORT"
    source_tfs: list = None  # type: ignore[assignment]
    merge_type: str = ""  # "single_5m", "single_15m", "merged", "conflict_15m_wins"
    size_multiplier: float = 1.0
    sl_source: str = ""  # "5m", "15m", or "tighter"
    tp_source: str = ""  # "5m", "15m"
    note: str = ""

    def __post_init__(self) -> None:
        if self.source_tfs is None:
            self.source_tfs = []


def evaluate_merge(
    signal_5m: Optional[SignalSnapshot],
    signal_15m: Optional[SignalSnapshot],
) -> MergeDecision:
    """Evaluate smart merge between 5m and 15m signals.

    Returns a MergeDecision describing what action to take.
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

    # Case 4: Both trigger, SAME direction
    if s5 == s15:
        logger.info("merge_combined", direction=s5)
        return MergeDecision(
            action="merged",
            direction=s5,
            source_tfs=["5m", "15m"],
            merge_type="merged",
            size_multiplier=config.MERGE_MAX_SIZE_MULTIPLIER,
            sl_source="tighter",
            tp_source="15m",
        )

    # Case 5: Both trigger, OPPOSITE direction — 15m wins
    logger.info("merge_conflict_15m_wins", s5=s5, s15=s15)
    return MergeDecision(
        action="single",
        direction=s15,
        source_tfs=["15m"],
        merge_type="conflict_15m_wins",
        size_multiplier=1.0,
        sl_source="15m",
        tp_source="15m",
        note=f"conflict_resolved: 5m={s5} vs 15m={s15} -> 15m wins",
    )
