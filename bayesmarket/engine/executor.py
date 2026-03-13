"""Entry/exit pipeline, SL/TP management, position monitoring.

Blueprint Section 8 + errata Patch #3 (structural SL) + Patch #4 (order types).
"""

import asyncio
import time
from typing import Optional

import structlog

from bayesmarket import config
from bayesmarket.data.state import MarketState, Position, WallInfo
from bayesmarket.data.storage import Storage
from bayesmarket.engine.merge import MergeDecision, evaluate_merge
from bayesmarket.engine.position import (
    calculate_pnl,
    check_sl,
    check_tp1,
    check_tp2,
    create_position,
)
from bayesmarket.indicators.regime import compute_atr
from bayesmarket.indicators.structure import compute_vwap
from bayesmarket.risk.sizing import calculate_position_size

logger = structlog.get_logger()


async def merge_and_execute_loop(state: MarketState, storage: Storage) -> None:
    """Every 1s: run smart merge + entry evaluation."""
    logger.info("merge_execute_loop_started")

    while True:
        try:
            if state.position is None and state.mid_price > 0:
                _evaluate_entry(state, storage)
        except Exception as exc:
            logger.error("merge_execute_error", error=str(exc))

        await asyncio.sleep(1.0)


def _evaluate_entry(state: MarketState, storage: Storage) -> None:
    """Check if we should enter a new position."""
    sig_5m = state.tf_states.get("5m", None)
    sig_15m = state.tf_states.get("15m", None)

    signal_5m = sig_5m.signal if sig_5m else None
    signal_15m = sig_15m.signal if sig_15m else None

    decision = evaluate_merge(signal_5m, signal_15m)

    if decision.action == "none":
        return

    # Risk check: can we trade?
    risk = state.risk
    if risk.daily_paused or risk.full_stop_active:
        return

    if state.position is not None:
        return

    # Determine entry price (shadow: mid_price)
    entry_price = state.mid_price

    # Determine SL
    sl_price, sl_basis, sl_wall = _determine_sl(
        state, decision.direction, entry_price, decision.sl_source
    )
    if sl_price <= 0:
        return

    # Emergency SL check
    sl_distance_pct = abs(entry_price - sl_price) / entry_price * 100
    if sl_distance_pct > config.EMERGENCY_SL_PCT:
        logger.warning(
            "sl_too_wide_emergency",
            sl_distance_pct=round(sl_distance_pct, 2),
            limit=config.EMERGENCY_SL_PCT,
        )
        return

    # Calculate position size
    is_merged = decision.action == "merged"
    size = calculate_position_size(
        capital=state.capital,
        entry_price=entry_price,
        sl_price=sl_price,
        cooldown_active=risk.cooldown_active,
        funding_tier=state.funding_tier,
        is_merged=is_merged,
    )

    if size is None:
        return

    # Determine TP
    tp1_price, tp2_price = _determine_tp(state, decision, entry_price)

    # Validate TP is in the right direction
    if decision.direction == "LONG":
        if tp1_price <= entry_price:
            tp1_price = entry_price + compute_atr(state.tf_states["5m"].klines) * config.TP1_FALLBACK_ATR_MULT
        if tp2_price <= entry_price:
            tp2_price = entry_price + compute_atr(state.tf_states["5m"].klines) * config.TP2_ATR_MULTIPLIER
    else:
        if tp1_price >= entry_price:
            tp1_price = entry_price - compute_atr(state.tf_states["5m"].klines) * config.TP1_FALLBACK_ATR_MULT
        if tp2_price >= entry_price:
            tp2_price = entry_price - compute_atr(state.tf_states["5m"].klines) * config.TP2_ATR_MULTIPLIER

    # Create position
    score_5m = signal_5m.total_score if signal_5m else None
    score_15m = signal_15m.total_score if signal_15m else None

    position = create_position(
        side=decision.direction.lower(),
        entry_price=entry_price,
        size=size,
        source_tfs=decision.source_tfs,
        sl_price=sl_price,
        sl_basis=sl_basis,
        sl_wall_info=sl_wall,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        score_5m=score_5m,
        score_15m=score_15m,
    )
    state.position = position

    mode = "SHADOW" if not config.LIVE_MODE else "LIVE"
    logger.info(
        f"[{mode}] entry_executed",
        side=decision.direction,
        entry=round(entry_price, 1),
        size=round(size, 6),
        sl=round(sl_price, 1),
        sl_basis=sl_basis,
        tp1=round(tp1_price, 1),
        tp2=round(tp2_price, 1),
        source="+".join(decision.source_tfs),
        merge_type=decision.merge_type,
        score_5m=round(score_5m, 2) if score_5m else None,
        score_15m=round(score_15m, 2) if score_15m else None,
    )

    storage.insert_event(
        "entry",
        f"{mode} {decision.direction} @ {entry_price:.1f} "
        f"size={size:.6f} SL={sl_price:.1f}({sl_basis}) "
        f"TP1={tp1_price:.1f} TP2={tp2_price:.1f} "
        f"src={'+'.join(decision.source_tfs)}",
    )


def _determine_sl(
    state: MarketState,
    direction: str,
    entry_price: float,
    sl_source: str,
) -> tuple[float, str, Optional[WallInfo]]:
    """3-layer SL fallback: Wall -> POC -> ATR."""
    # Layer 1: Wall-based
    valid_walls = [w for w in state.tracked_walls if w.is_valid]

    best_wall: Optional[WallInfo] = None
    if direction == "LONG":
        bid_walls = [w for w in valid_walls if w.side == "bid" and w.bin_high < entry_price]
        if bid_walls:
            best_wall = max(bid_walls, key=lambda w: w.bin_center)
    else:
        ask_walls = [w for w in valid_walls if w.side == "ask" and w.bin_low > entry_price]
        if ask_walls:
            best_wall = min(ask_walls, key=lambda w: w.bin_center)

    if best_wall:
        offset = entry_price * config.WALL_SL_OFFSET_PCT / 100.0
        if direction == "LONG":
            sl = best_wall.bin_low - offset
        else:
            sl = best_wall.bin_high + offset
        return sl, "wall", best_wall

    # Layer 2: POC-based
    # Use 5m TF klines for POC
    tf_5m = state.tf_states.get("5m")
    if tf_5m and tf_5m.klines:
        from bayesmarket.indicators.structure import compute_poc
        poc_val, _ = compute_poc(tf_5m.klines, state.mid_price)
        if poc_val > 0:
            offset = entry_price * config.POC_SL_OFFSET_PCT / 100.0
            if direction == "LONG" and poc_val < entry_price:
                return poc_val - offset, "poc", None
            elif direction == "SHORT" and poc_val > entry_price:
                return poc_val + offset, "poc", None

    # Layer 3: ATR-based
    atr = 0.0
    for tf_name in ["5m", "15m"]:
        tf_state = state.tf_states.get(tf_name)
        if tf_state and tf_state.klines:
            atr = compute_atr(tf_state.klines)
            if atr > 0:
                break

    if atr > 0:
        if direction == "LONG":
            sl = entry_price - config.ATR_SL_MULTIPLIER * atr
        else:
            sl = entry_price + config.ATR_SL_MULTIPLIER * atr
        return sl, "atr", None

    # Emergency: no ATR data, use percentage
    emergency_dist = entry_price * config.EMERGENCY_SL_PCT / 100
    if direction == "LONG":
        return entry_price - emergency_dist, "emergency", None
    else:
        return entry_price + emergency_dist, "emergency", None


def _determine_tp(
    state: MarketState,
    decision: MergeDecision,
    entry_price: float,
) -> tuple[float, float]:
    """Determine TP1 (VWAP or ATR fallback) and TP2 (2x ATR)."""
    # Get ATR
    atr = 0.0
    for tf_name in ["5m", "15m"]:
        tf_state = state.tf_states.get(tf_name)
        if tf_state and tf_state.klines:
            atr = compute_atr(tf_state.klines)
            if atr > 0:
                break

    if atr <= 0:
        atr = entry_price * 0.005  # 0.5% fallback

    # TP1: VWAP reversion (from TP source TF)
    tp_tf = decision.tp_source
    tf_state = state.tf_states.get(tp_tf)
    vwap_val = 0.0
    if tf_state and tf_state.klines:
        vwap_val, _ = compute_vwap(tf_state.klines, state.mid_price)

    if decision.direction == "LONG":
        if vwap_val > entry_price and abs(vwap_val - entry_price) / entry_price > config.TP1_NEAR_VWAP_THRESHOLD:
            tp1 = vwap_val
        else:
            tp1 = entry_price + config.TP1_FALLBACK_ATR_MULT * atr
        tp2 = entry_price + config.TP2_ATR_MULTIPLIER * atr
    else:
        if vwap_val < entry_price and abs(entry_price - vwap_val) / entry_price > config.TP1_NEAR_VWAP_THRESHOLD:
            tp1 = vwap_val
        else:
            tp1 = entry_price - config.TP1_FALLBACK_ATR_MULT * atr
        tp2 = entry_price - config.TP2_ATR_MULTIPLIER * atr

    return tp1, tp2


async def position_monitor_loop(state: MarketState, storage: Storage) -> None:
    """Every 1s: monitor SL/TP/wall health for open position."""
    logger.info("position_monitor_started")

    while True:
        try:
            if state.position is not None and state.mid_price > 0:
                _monitor_position(state, storage)
        except Exception as exc:
            logger.error("position_monitor_error", error=str(exc))

        await asyncio.sleep(1.0)


def _monitor_position(state: MarketState, storage: Storage) -> None:
    """Check SL, TP1, TP2, and wall health for the open position."""
    pos = state.position
    if pos is None:
        return

    mid = state.mid_price
    mode = "SHADOW" if not config.LIVE_MODE else "LIVE"

    # Check SL
    if check_sl(pos, mid):
        pnl = calculate_pnl(pos.side, pos.entry_price, mid, pos.remaining_size)
        pnl_pct = pnl / state.capital * 100 if state.capital > 0 else 0.0

        logger.info(
            f"[{mode}] sl_hit",
            side=pos.side,
            entry=round(pos.entry_price, 1),
            exit=round(mid, 1),
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            sl_basis=pos.sl_basis,
        )

        _close_position(state, storage, mid, "sl_hit", pnl, pnl_pct)
        return

    # Check TP1
    if check_tp1(pos, mid):
        tp1_pnl = calculate_pnl(pos.side, pos.entry_price, pos.tp1_price, pos.tp1_size)
        pos.tp1_hit = True
        pos.remaining_size -= pos.tp1_size
        pos.pnl_realized += tp1_pnl
        state.capital += tp1_pnl

        logger.info(
            f"[{mode}] tp1_hit",
            price=round(pos.tp1_price, 1),
            exit_size=round(pos.tp1_size, 6),
            pnl=round(tp1_pnl, 2),
            remaining=round(pos.remaining_size, 6),
        )

    # Check TP2
    if check_tp2(pos, mid):
        tp2_pnl = calculate_pnl(pos.side, pos.entry_price, pos.tp2_price, pos.tp2_size)
        pnl_total = pos.pnl_realized + tp2_pnl
        pnl_pct = pnl_total / state.capital * 100 if state.capital > 0 else 0.0

        logger.info(
            f"[{mode}] tp2_hit",
            price=round(pos.tp2_price, 1),
            exit_size=round(pos.tp2_size, 6),
            total_pnl=round(pnl_total, 2),
        )

        _close_position(state, storage, pos.tp2_price, "tp2_hit", pnl_total, pnl_pct)
        return

    # Wall health monitoring (structural SL only — errata Patch #3)
    _monitor_sl_wall_health(state, pos)

    # Structural SL tightening
    _check_structural_sl_tighten(state, pos)


def _close_position(
    state: MarketState,
    storage: Storage,
    exit_price: float,
    exit_reason: str,
    pnl: float,
    pnl_pct: float,
) -> None:
    """Close the current position and update risk state."""
    pos = state.position
    if pos is None:
        return

    merge_type = "merged" if len(pos.source_tfs) > 1 else f"single_{pos.source_tfs[0]}"
    regime = "trending"  # Get from current state
    for tf_name in ["5m", "15m"]:
        tf_state = state.tf_states.get(tf_name)
        if tf_state and tf_state.signal:
            regime = tf_state.signal.regime
            break

    storage.insert_trade(
        position=pos,
        exit_price=exit_price,
        exit_reason=exit_reason,
        pnl=pnl,
        pnl_pct=pnl_pct,
        merge_type=merge_type,
        regime=regime,
    )

    # Update capital
    state.capital += pnl - pos.pnl_realized  # Subtract already-realized TP1 pnl

    # Update risk state
    from bayesmarket.risk.limits import update_after_trade
    update_after_trade(state.risk, pnl, state.capital)

    mode = "SHADOW" if not config.LIVE_MODE else "LIVE"
    duration = time.time() - pos.entry_time
    logger.info(
        f"[{mode}] trade_closed",
        side=pos.side,
        entry=round(pos.entry_price, 1),
        exit=round(exit_price, 1),
        pnl=round(pnl, 2),
        pnl_pct=round(pnl_pct, 2),
        duration_s=round(duration, 0),
        exit_reason=exit_reason,
        source="+".join(pos.source_tfs),
    )

    state.position = None


def _monitor_sl_wall_health(state: MarketState, pos: Position) -> None:
    """Monitor the basis wall for decay. Errata Patch #3 Rule 3."""
    if pos.sl_basis != "wall" or pos.sl_wall_info is None:
        return

    original_wall = pos.sl_wall_info

    # Find current wall at the same bin
    current_wall = None
    for w in state.tracked_walls:
        if w.side == original_wall.side and w.bin_low == original_wall.bin_low:
            current_wall = w
            break

    if current_wall is None:
        # Wall disappeared entirely — execute fallback
        logger.warning("sl_basis_wall_disappeared", bin=original_wall.bin_center)
        _escalate_sl_fallback(state, pos)
        return

    ratio = current_wall.total_size / original_wall.initial_size if original_wall.initial_size > 0 else 0

    if ratio < 0.25:
        logger.warning("sl_basis_wall_decayed_critical", ratio=round(ratio, 2))
        _escalate_sl_fallback(state, pos)
    elif ratio < 0.5:
        logger.info("sl_basis_wall_decaying", ratio=round(ratio, 2))


def _escalate_sl_fallback(state: MarketState, pos: Position) -> None:
    """When basis wall decays, search for new SL. Must be tighter or equal."""
    entry = pos.entry_price
    current_sl = pos.sl_price

    # Try POC
    tf_5m = state.tf_states.get("5m")
    if tf_5m and tf_5m.klines:
        from bayesmarket.indicators.structure import compute_poc
        poc_val, _ = compute_poc(tf_5m.klines, state.mid_price)
        offset = entry * config.POC_SL_OFFSET_PCT / 100.0
        if pos.side == "long" and poc_val > 0 and poc_val < entry:
            new_sl = poc_val - offset
            if new_sl >= current_sl:
                pos.sl_price = new_sl
                pos.sl_basis = "poc"
                logger.info("sl_escalated_to_poc", new_sl=round(new_sl, 1))
                return
        elif pos.side == "short" and poc_val > 0 and poc_val > entry:
            new_sl = poc_val + offset
            if new_sl <= current_sl:
                pos.sl_price = new_sl
                pos.sl_basis = "poc"
                logger.info("sl_escalated_to_poc", new_sl=round(new_sl, 1))
                return

    # Try ATR
    atr = 0.0
    for tf_name in ["5m", "15m"]:
        tf_s = state.tf_states.get(tf_name)
        if tf_s and tf_s.klines:
            atr = compute_atr(tf_s.klines)
            if atr > 0:
                break

    if atr > 0:
        if pos.side == "long":
            new_sl = entry - config.ATR_SL_MULTIPLIER * atr
            if new_sl >= current_sl:
                pos.sl_price = new_sl
                pos.sl_basis = "atr"
                logger.info("sl_escalated_to_atr", new_sl=round(new_sl, 1))
        else:
            new_sl = entry + config.ATR_SL_MULTIPLIER * atr
            if new_sl <= current_sl:
                pos.sl_price = new_sl
                pos.sl_basis = "atr"
                logger.info("sl_escalated_to_atr", new_sl=round(new_sl, 1))


def _check_structural_sl_tighten(state: MarketState, pos: Position) -> None:
    """Structural SL tightening (errata Patch #3 Rule 2).

    SL tightens ONLY on confirmed structural swing low/high shift,
    NEVER on new walls appearing after entry.
    """
    mid = state.mid_price
    atr = 0.0
    for tf_name in ["5m", "15m"]:
        tf_s = state.tf_states.get(tf_name)
        if tf_s and tf_s.klines:
            atr = compute_atr(tf_s.klines)
            if atr > 0:
                break

    if atr <= 0:
        return

    min_distance = config.SL_MIN_DISTANCE_ATR_MULT * atr
    confirmation_pct = config.SL_STRUCTURAL_CONFIRMATION_PCT

    if pos.side == "long":
        # Detect potential higher low
        # Simple heuristic: if mid_price has pulled back and recovered
        if pos.last_swing_low is not None:
            # Check if we can move SL up
            if mid > pos.last_swing_low * (1 + confirmation_pct):
                new_sl = pos.last_swing_low - (pos.last_swing_low * 0.001)
                # Ensure minimum distance
                if mid - new_sl < min_distance:
                    new_sl = mid - min_distance
                # Only tighten, never loosen
                if new_sl > pos.sl_price:
                    pos.sl_price = new_sl
                    logger.info("sl_structural_tighten", new_sl=round(new_sl, 1), side="long")

        # Track swing lows (simple: current low in recent klines)
        tf_5m = state.tf_states.get("5m")
        if tf_5m and len(tf_5m.klines) >= 3:
            recent = list(tf_5m.klines)[-3:]
            if recent[1].low < recent[0].low and recent[1].low < recent[2].low:
                swing_low = recent[1].low
                if pos.last_swing_low is None or swing_low > pos.last_swing_low:
                    pos.last_swing_low = swing_low

    else:  # short
        if pos.last_swing_high is not None:
            if mid < pos.last_swing_high * (1 - confirmation_pct):
                new_sl = pos.last_swing_high + (pos.last_swing_high * 0.001)
                if new_sl - mid < min_distance:
                    new_sl = mid + min_distance
                if new_sl < pos.sl_price:
                    pos.sl_price = new_sl
                    logger.info("sl_structural_tighten", new_sl=round(new_sl, 1), side="short")

        tf_5m = state.tf_states.get("5m")
        if tf_5m and len(tf_5m.klines) >= 3:
            recent = list(tf_5m.klines)[-3:]
            if recent[1].high > recent[0].high and recent[1].high > recent[2].high:
                swing_high = recent[1].high
                if pos.last_swing_high is None or swing_high < pos.last_swing_high:
                    pos.last_swing_high = swing_high
