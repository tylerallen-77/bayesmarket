"""Position state tracking, partial exit handling."""

import time
from typing import Optional

import structlog

from bayesmarket import config
from bayesmarket.data.state import MarketState, Position, WallInfo

logger = structlog.get_logger()


def create_position(
    side: str,
    entry_price: float,
    size: float,
    source_tfs: list[str],
    sl_price: float,
    sl_basis: str,
    sl_wall_info: Optional[WallInfo],
    tp1_price: float,
    tp2_price: float,
    score_5m: Optional[float],
    score_15m: Optional[float],
) -> Position:
    """Create a new Position with TP sizes calculated from total size."""
    tp1_size = size * config.TP1_SIZE_PCT
    tp2_size = size * config.TP2_SIZE_PCT

    return Position(
        side=side,
        entry_price=entry_price,
        size=size,
        remaining_size=size,
        entry_time=time.time(),
        source_tfs=source_tfs,
        entry_score_5m=score_5m,
        entry_score_15m=score_15m,
        sl_price=sl_price,
        sl_basis=sl_basis,
        sl_wall_info=sl_wall_info,
        tp1_price=tp1_price,
        tp1_size=tp1_size,
        tp1_hit=False,
        tp2_price=tp2_price,
        tp2_size=tp2_size,
        tp2_hit=False,
        pnl_realized=0.0,
    )


def check_tp1(position: Position, mid_price: float) -> bool:
    """Check if TP1 is hit. Returns True if TP1 just triggered."""
    if position.tp1_hit:
        return False

    if position.side == "long" and mid_price >= position.tp1_price:
        return True
    if position.side == "short" and mid_price <= position.tp1_price:
        return True
    return False


def check_tp2(position: Position, mid_price: float) -> bool:
    """Check if TP2 is hit. Returns True if TP2 just triggered."""
    if position.tp2_hit or not position.tp1_hit:
        return False

    if position.side == "long" and mid_price >= position.tp2_price:
        return True
    if position.side == "short" and mid_price <= position.tp2_price:
        return True
    return False


def check_sl(position: Position, mid_price: float) -> bool:
    """Check if SL is hit. Returns True if SL just triggered."""
    if position.side == "long" and mid_price <= position.sl_price:
        return True
    if position.side == "short" and mid_price >= position.sl_price:
        return True
    return False


def calculate_pnl(side: str, entry_price: float, exit_price: float, size: float) -> float:
    """Calculate PnL for a position exit."""
    if side == "long":
        return (exit_price - entry_price) * size
    else:
        return (entry_price - exit_price) * size


def calculate_unrealized_pnl(position: Position, mid_price: float) -> float:
    """Calculate unrealized PnL for an open position."""
    return calculate_pnl(position.side, position.entry_price, mid_price, position.remaining_size)
