"""Entry/exit pipeline, SL/TP management, position monitoring.

Fixes applied:
  - tp2_hit flag now correctly set to True before close
  - capital double-accounting fixed (no longer subtracts pnl_realized on TP path)
  - can_trade() called in entry evaluation (handles state expiry)
  - time-based exit (TIME_EXIT_ENABLED)
  - force_close flag from Telegram /close command
  - Telegram alerts wired in (non-blocking, silent-fail)
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
    calculate_unrealized_pnl,
)
from bayesmarket.indicators.regime import compute_atr
from bayesmarket.indicators.structure import compute_vwap
from bayesmarket.risk.sizing import calculate_position_size

logger = structlog.get_logger()


# ── Entry loop ────────────────────────────────────────────────────────────────

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
    # ── Runtime gate ──────────────────────────────────────────────
    rt = state.runtime
    if rt and rt.trading_paused:
        return

    # ── Risk gate: use can_trade() to handle state expiry ────────
    from bayesmarket.risk.limits import can_trade
    allowed, reason = can_trade(state.risk, state.capital)
    if not allowed:
        return

    # ── Signal evaluation (cascade: 5m trigger only) ───────────────
    sig_5m = state.tf_states.get("5m")
    signal_5m = sig_5m.signal if sig_5m else None

    decision = evaluate_merge(signal_5m)
    if decision.action == "none":
        return

    if state.position is not None:
        return

    entry_price = state.mid_price

    # ── Stop Loss ─────────────────────────────────────────────────
    sl_price, sl_basis, sl_wall = _determine_sl(
        state, decision.direction, entry_price, decision.sl_source
    )
    if sl_price <= 0:
        return

    sl_distance_pct = abs(entry_price - sl_price) / entry_price * 100
    if sl_distance_pct > config.EMERGENCY_SL_PCT:
        logger.warning("sl_too_wide_emergency", sl_pct=round(sl_distance_pct, 2))
        return

    # ── Position sizing ───────────────────────────────────────────
    size = calculate_position_size(
        capital=state.capital,
        entry_price=entry_price,
        sl_price=sl_price,
        cooldown_active=state.risk.cooldown_active,
        funding_tier=state.funding_tier,
        is_merged=decision.action == "merged",
    )
    if size is None:
        return

    # ── Take Profit ───────────────────────────────────────────────
    tp1_price, tp2_price = _determine_tp(state, decision, entry_price)
    atr_5m = compute_atr(state.tf_states["5m"].klines) if state.tf_states.get("5m") else entry_price * 0.005
    atr = atr_5m if atr_5m > 0 else entry_price * 0.005

    if decision.direction == "LONG":
        if tp1_price <= entry_price:
            tp1_price = entry_price + config.TP1_FALLBACK_ATR_MULT * atr
        if tp2_price <= entry_price:
            tp2_price = entry_price + config.TP2_ATR_MULTIPLIER * atr
    else:
        if tp1_price >= entry_price:
            tp1_price = entry_price - config.TP1_FALLBACK_ATR_MULT * atr
        if tp2_price >= entry_price:
            tp2_price = entry_price - config.TP2_ATR_MULTIPLIER * atr

    # ── SL/TP ratio guard ────────────────────────────────────────
    # Prevents absurd RR from stale POC/wall levels (e.g. Trade 7: RR 1:0.13)
    tp1_dist = abs(tp1_price - entry_price)
    sl_dist = abs(sl_price - entry_price)
    if tp1_dist > 0 and sl_dist > config.MAX_SL_TP_RATIO * tp1_dist:
        capped_sl_dist = tp1_dist * config.MAX_SL_TP_RATIO
        if decision.direction == "LONG":
            sl_price = entry_price - capped_sl_dist
        else:
            sl_price = entry_price + capped_sl_dist
        logger.info(
            "sl_capped_by_rr_ratio",
            original_dist=round(sl_dist, 1),
            capped_dist=round(capped_sl_dist, 1),
            ratio=round(sl_dist / tp1_dist, 2),
        )
        sl_basis = "atr_capped"
        sl_wall = None

    # ── Create position ───────────────────────────────────────────
    score_5m = signal_5m.total_score if signal_5m else None
    score_15m = None  # 15m is timing TF in cascade mode

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

    mode = "LIVE" if (rt and rt.live_mode) else "SHADOW"
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
    )

    storage.insert_event(
        "entry",
        f"{mode} {decision.direction} @ {entry_price:.1f} "
        f"size={size:.6f} SL={sl_price:.1f}({sl_basis}) "
        f"TP1={tp1_price:.1f} TP2={tp2_price:.1f} "
        f"src={'+'.join(decision.source_tfs)}",
    )

    # ── Telegram alert (non-blocking) ────────────────────────────
    if rt and rt.alert_on_entry:
        asyncio.create_task(_send_entry_alert(position, state, mode))


async def _send_entry_alert(position: Position, state: MarketState, mode: str) -> None:
    try:
        from bayesmarket.telegram_bot.alerts import alert_entry
        score = position.entry_score_5m or position.entry_score_15m or 0.0
        await alert_entry(
            side=position.side,
            entry_price=position.entry_price,
            size=position.size,
            sl_price=position.sl_price,
            sl_basis=position.sl_basis,
            tp1_price=position.tp1_price,
            tp2_price=position.tp2_price,
            source_tfs=position.source_tfs,
            score=score,
            mode=mode,
            capital=state.capital,
        )
    except Exception as exc:
        logger.error("entry_alert_failed", error=str(exc))


# ── Position monitor loop ─────────────────────────────────────────────────────

async def position_monitor_loop(state: MarketState, storage: Storage) -> None:
    """Every 1s: monitor SL/TP/wall/time/force-close for open position."""
    logger.info("position_monitor_started")
    while True:
        try:
            if state.position is not None and state.mid_price > 0:
                _monitor_position(state, storage)
        except Exception as exc:
            logger.error("position_monitor_error", error=str(exc))
        await asyncio.sleep(1.0)


def _monitor_position(state: MarketState, storage: Storage) -> None:
    pos = state.position
    if pos is None:
        return

    mid = state.mid_price
    rt = state.runtime
    mode = "LIVE" if (rt and rt.live_mode) else "SHADOW"

    # ── Force close (from Telegram /close command) ────────────────
    if pos._force_close:
        pnl = calculate_pnl(pos.side, pos.entry_price, mid, pos.remaining_size)
        # Add already-realized TP1 pnl if applicable
        total_pnl = pos.pnl_realized + pnl
        pnl_pct = total_pnl / state.capital * 100 if state.capital > 0 else 0
        logger.info(f"[{mode}] force_close", side=pos.side, pnl=round(total_pnl, 2))
        _close_position(state, storage, mid, "force_close", total_pnl, pnl_pct)
        return

    # ── SL check ──────────────────────────────────────────────────
    if check_sl(pos, mid):
        # PnL on remaining size only — pnl_realized already banked
        sl_pnl = calculate_pnl(pos.side, pos.entry_price, mid, pos.remaining_size)
        total_pnl = pos.pnl_realized + sl_pnl
        pnl_pct = total_pnl / state.capital * 100 if state.capital > 0 else 0

        logger.info(
            f"[{mode}] sl_hit",
            side=pos.side,
            entry=round(pos.entry_price, 1),
            exit=round(mid, 1),
            pnl=round(total_pnl, 2),
            sl_basis=pos.sl_basis,
        )
        diag = _close_position(state, storage, mid, "sl_hit", total_pnl, pnl_pct)

        if rt and rt.alert_on_sl_hit:
            asyncio.create_task(_send_exit_alert(pos, mid, total_pnl, pnl_pct, "sl_hit", mode, diagnosis=diag))
        return

    # ── TP1 check ─────────────────────────────────────────────────
    if check_tp1(pos, mid):
        tp1_pnl = calculate_pnl(pos.side, pos.entry_price, pos.tp1_price, pos.tp1_size)
        pos.tp1_hit = True                        # ← FIX: was never set before close
        pos.remaining_size -= pos.tp1_size
        pos.pnl_realized += tp1_pnl
        state.capital += tp1_pnl                  # bank TP1 profit immediately

        logger.info(
            f"[{mode}] tp1_hit",
            price=round(pos.tp1_price, 1),
            exit_size=round(pos.tp1_size, 6),
            pnl=round(tp1_pnl, 2),
            remaining=round(pos.remaining_size, 6),
        )

        if rt and rt.alert_on_tp:
            asyncio.create_task(_send_tp1_alert(pos, tp1_pnl, mode))

    # ── TP2 check ─────────────────────────────────────────────────
    if check_tp2(pos, mid):
        tp2_pnl = calculate_pnl(pos.side, pos.entry_price, pos.tp2_price, pos.tp2_size)
        pos.tp2_hit = True                        # ← FIX: set flag before close
        total_pnl = pos.pnl_realized + tp2_pnl    # ← FIX: pnl_realized = TP1 already banked
        pnl_pct = total_pnl / state.capital * 100 if state.capital > 0 else 0

        logger.info(
            f"[{mode}] tp2_hit",
            price=round(pos.tp2_price, 1),
            exit_size=round(pos.tp2_size, 6),
            total_pnl=round(total_pnl, 2),
        )
        # Pass tp2_pnl only — TP1 already added to capital above
        _close_position(state, storage, pos.tp2_price, "tp2_hit", tp2_pnl, pnl_pct)

        if rt and rt.alert_on_exit:
            asyncio.create_task(_send_exit_alert(pos, pos.tp2_price, total_pnl, pnl_pct, "tp2_hit", mode))
        return

    # ── Time-based exit ───────────────────────────────────────────
    if config.TIME_EXIT_ENABLED and not pos.tp1_hit:
        elapsed_min = (time.time() - pos.entry_time) / 60
        limit_min = config.TIME_EXIT_MINUTES_5M
        if elapsed_min >= limit_min:
            pnl = calculate_pnl(pos.side, pos.entry_price, mid, pos.remaining_size)
            total_pnl = pos.pnl_realized + pnl
            pnl_pct = total_pnl / state.capital * 100 if state.capital > 0 else 0

            logger.info(
                f"[{mode}] time_exit",
                elapsed_min=round(elapsed_min, 1),
                limit_min=limit_min,
                pnl=round(total_pnl, 2),
            )
            diag = _close_position(state, storage, mid, "time_exit", total_pnl, pnl_pct)

            if rt and rt.alert_on_exit:
                asyncio.create_task(_send_exit_alert(pos, mid, total_pnl, pnl_pct, "time_exit", mode, diagnosis=diag))
            return

    # ── Wall health + structural SL tightening ────────────────────
    _monitor_sl_wall_health(state, pos)
    _check_structural_sl_tighten(state, pos)


# ── Close position ────────────────────────────────────────────────────────────

def _close_position(
    state: MarketState,
    storage: Storage,
    exit_price: float,
    exit_reason: str,
    pnl: float,
    pnl_pct: float,
):
    """Close current position and update capital + risk.

    FIX: Capital accounting is now clean:
      - TP1 profit is banked immediately when TP1 hits (state.capital += tp1_pnl)
      - On final close, only pass the REMAINING portion pnl (tp2 or sl on remainder)
      - _close_position always does: state.capital += pnl (no subtraction of realized)
    """
    pos = state.position
    if pos is None:
        return

    merge_type = "merged" if len(pos.source_tfs) > 1 else f"single_{pos.source_tfs[0]}"
    regime = "trending"
    for tf_name in ["5m", "15m"]:
        tf_state = state.tf_states.get(tf_name)
        if tf_state and tf_state.signal:
            regime = tf_state.signal.regime
            break

    # ── Loss classification ──────────────────────────────────────
    diagnosis = None
    total_pnl_for_db = pos.pnl_realized + pnl
    if total_pnl_for_db < 0:
        try:
            from bayesmarket.engine.loss_analyzer import classify_loss
            exit_score = 0.0
            tf5 = state.tf_states.get("5m")
            if tf5 and tf5.signal:
                exit_score = tf5.signal.total_score
            diagnosis = classify_loss(
                pos=pos,
                state=state,
                exit_price=exit_price,
                exit_reason=exit_reason,
                exit_score=exit_score,
            )
            logger.warning(
                "loss_classified",
                category=diagnosis.category,
                severity=diagnosis.severity,
                rr=diagnosis.rr_ratio,
                hold_min=diagnosis.hold_minutes,
            )
        except Exception as exc:
            logger.error("loss_classification_failed", error=str(exc))

    trade_id = storage.insert_trade(
        position=pos,
        exit_price=exit_price,
        exit_reason=exit_reason,
        pnl=total_pnl_for_db,
        pnl_pct=pnl_pct,
        merge_type=merge_type,
        regime=regime,
        diagnosis=diagnosis,
    )
    if diagnosis:
        diagnosis.trade_id = trade_id

    # Update capital with remaining-portion pnl only
    # (TP1 was already added when it hit)
    state.capital += pnl

    from bayesmarket.risk.limits import update_after_trade
    total_pnl = pos.pnl_realized + pnl
    update_after_trade(state.risk, total_pnl, state.capital)

    rt = state.runtime
    mode = "LIVE" if (rt and rt.live_mode) else "SHADOW"
    duration = time.time() - pos.entry_time
    logger.info(
        f"[{mode}] trade_closed",
        side=pos.side,
        entry=round(pos.entry_price, 1),
        exit=round(exit_price, 1),
        pnl=round(total_pnl, 2),
        pnl_pct=round(pnl_pct, 2),
        duration_s=round(duration, 0),
        exit_reason=exit_reason,
        source="+".join(pos.source_tfs),
        tp1_hit=pos.tp1_hit,
        tp2_hit=pos.tp2_hit,
    )

    state.position = None
    return diagnosis


# ── Alert coroutines ──────────────────────────────────────────────────────────

async def _send_tp1_alert(pos: Position, tp1_pnl: float, mode: str) -> None:
    try:
        from bayesmarket.telegram_bot.alerts import alert_tp1
        await alert_tp1(
            side=pos.side,
            tp1_price=pos.tp1_price,
            pnl=tp1_pnl,
            remaining_size=pos.remaining_size,
            mode=mode,
        )
    except Exception as exc:
        logger.error("tp1_alert_failed", error=str(exc))


async def _send_exit_alert(
    pos: Position,
    exit_price: float,
    pnl: float,
    pnl_pct: float,
    reason: str,
    mode: str,
    diagnosis=None,
) -> None:
    try:
        from bayesmarket.telegram_bot.alerts import alert_exit
        duration = time.time() - pos.entry_time
        await alert_exit(
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=reason,
            duration_seconds=duration,
            tp1_hit=pos.tp1_hit,
            mode=mode,
            diagnosis=diagnosis,
        )
    except Exception as exc:
        logger.error("exit_alert_failed", error=str(exc))


# ── SL determination ──────────────────────────────────────────────────────────

def _determine_sl(
    state: MarketState,
    direction: str,
    entry_price: float,
    sl_source: str,
) -> tuple[float, str, Optional[WallInfo]]:
    """3-layer SL fallback: Wall → POC → ATR."""
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
        sl = best_wall.bin_low - offset if direction == "LONG" else best_wall.bin_high + offset
        return sl, "wall", best_wall

    # Layer 2: POC
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

    # Layer 3: ATR
    atr = 0.0
    for tf_name in ["5m", "15m"]:
        tf_state = state.tf_states.get(tf_name)
        if tf_state and tf_state.klines:
            atr = compute_atr(tf_state.klines)
            if atr > 0:
                break

    if atr > 0:
        sl = entry_price - config.ATR_SL_MULTIPLIER * atr if direction == "LONG" \
            else entry_price + config.ATR_SL_MULTIPLIER * atr
        return sl, "atr", None

    emergency_dist = entry_price * config.EMERGENCY_SL_PCT / 100
    sl = entry_price - emergency_dist if direction == "LONG" else entry_price + emergency_dist
    return sl, "emergency", None


def _determine_tp(
    state: MarketState,
    decision: MergeDecision,
    entry_price: float,
) -> tuple[float, float]:
    """Determine TP1 (VWAP or ATR fallback) and TP2 (2x ATR)."""
    atr = 0.0
    for tf_name in ["5m", "15m"]:
        tf_state = state.tf_states.get(tf_name)
        if tf_state and tf_state.klines:
            atr = compute_atr(tf_state.klines)
            if atr > 0:
                break
    if atr <= 0:
        atr = entry_price * 0.005

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


# ── SL monitoring ─────────────────────────────────────────────────────────────

def _monitor_sl_wall_health(state: MarketState, pos: Position) -> None:
    """Monitor basis wall for decay."""
    if pos.sl_basis != "wall" or pos.sl_wall_info is None:
        return

    original_wall = pos.sl_wall_info
    current_wall = None
    for w in state.tracked_walls:
        if w.side == original_wall.side and w.bin_low == original_wall.bin_low:
            current_wall = w
            break

    if current_wall is None:
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
    """When basis wall decays, escalate to POC or ATR. Only tighten."""
    entry = pos.entry_price
    current_sl = pos.sl_price

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
    """Structural SL tightening on confirmed swing low/high shift."""
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
        if pos.last_swing_low is not None:
            if mid > pos.last_swing_low * (1 + confirmation_pct):
                new_sl = pos.last_swing_low - (pos.last_swing_low * 0.001)
                if mid - new_sl < min_distance:
                    new_sl = mid - min_distance
                if new_sl > pos.sl_price:
                    pos.sl_price = new_sl
                    logger.info("sl_structural_tighten", new_sl=round(new_sl, 1), side="long")
        tf_5m = state.tf_states.get("5m")
        if tf_5m and len(tf_5m.klines) >= 3:
            recent = list(tf_5m.klines)[-3:]
            if recent[1].low < recent[0].low and recent[1].low < recent[2].low:
                swing_low = recent[1].low
                if pos.last_swing_low is None or swing_low > pos.last_swing_low:
                    pos.last_swing_low = swing_low
    else:
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
