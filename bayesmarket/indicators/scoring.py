"""Composite bias score aggregation per TF — cascade signal generation.

Architecture: 4h (BIAS) → 1h (CONTEXT) → 15m (TIMING) → 5m (TRIGGER)
- 4h determines allowed direction (LONG/SHORT/BOTH)
- 1h confirms 4h bias (same sign check)
- 15m establishes entry zone within confirmed bias
- 5m triggers execution only within active 15m zone
"""

import time
from typing import Optional

import structlog

from bayesmarket import config
from bayesmarket.data.state import MarketState, SignalSnapshot, TimeframeState
from bayesmarket.indicators.order_flow import (
    compute_cvd_score,
    compute_depth_score,
    compute_obi_score,
)
from bayesmarket.indicators.structure import compute_ha_score, compute_poc, compute_vwap
from bayesmarket.indicators.momentum import compute_ema_score, compute_macd, compute_rsi
from bayesmarket.indicators.regime import detect_regime

logger = structlog.get_logger()


def compute_signal(state: MarketState, tf_name: str) -> SignalSnapshot:
    """Compute all indicators and route to cascade role handler."""
    tf_state = state.tf_states[tf_name]
    tf_cfg = config.TIMEFRAMES[tf_name]
    mid = state.mid_price

    snap = SignalSnapshot(timestamp=time.time(), timeframe=tf_name)

    # ── Category A: Order Flow ─────────────────────────────────────
    snap.cvd_zscore_raw, snap.cvd_score = compute_cvd_score(state, tf_state)
    snap.obi_raw, snap.obi_score = compute_obi_score(state, tf_cfg["obi_band_pct"])
    snap.depth_ratio, snap.depth_score = compute_depth_score(state)

    # ── Category B: Structure ──────────────────────────────────────
    rt = state.runtime
    vwap_sens = rt.vwap_sensitivity if rt else config.VWAP_SENSITIVITY
    poc_sens = rt.poc_sensitivity if rt else config.POC_SENSITIVITY
    snap.vwap_value, snap.vwap_score = compute_vwap(tf_state.klines, mid, vwap_sens)
    snap.poc_value, snap.poc_score = compute_poc(tf_state.klines, mid, poc_sens)
    snap.ha_streak, snap.ha_score = compute_ha_score(tf_state.klines)

    # ── Regime ────────────────────────────────────────────────────
    regime, threshold, atr, atr_pct = detect_regime(
        tf_state.klines,
        tf_cfg["scoring_threshold"],
        tf_cfg["scoring_threshold_ranging"],
    )
    snap.regime = regime
    snap.active_threshold = threshold
    snap.atr_value = atr
    snap.atr_percentile = atr_pct

    # ── Category C: Momentum ──────────────────────────────────────
    snap.rsi_value, snap.rsi_score = compute_rsi(tf_state.klines)
    snap.macd_histogram, snap.macd_score = compute_macd(tf_state.klines, atr)
    snap.ema_short, snap.ema_long, snap.ema_score = compute_ema_score(tf_state.klines)

    # ── Composites ────────────────────────────────────────────────
    snap.category_a = snap.cvd_score + snap.obi_score + snap.depth_score
    snap.category_b = snap.vwap_score + snap.poc_score + snap.ha_score
    snap.category_c = snap.rsi_score + snap.macd_score + snap.ema_score
    snap.total_score = snap.category_a + snap.category_b + snap.category_c

    # ── Funding ───────────────────────────────────────────────────
    snap.funding_rate = state.funding_rate
    snap.funding_tier = state.funding_tier

    # ── Cascade role dispatch ─────────────────────────────────────
    role = tf_cfg["role"]

    if role == "bias":
        _evaluate_bias(snap, state)
    elif role == "context":
        _evaluate_context(snap, state)
    elif role == "timing":
        _evaluate_timing(snap, state, tf_state)
    elif role == "trigger":
        _evaluate_trigger(snap, state, tf_state)

    tf_state.signal = snap
    return snap


# ── Cascade evaluation functions ─────────────────────────────────────────────


def _evaluate_bias(snap: SignalSnapshot, state: MarketState) -> None:
    """4h BIAS: determines allowed trading direction for entire cascade."""
    threshold = config.CASCADE_BIAS_THRESHOLD
    if snap.total_score >= threshold:
        direction = "LONG"
    elif snap.total_score <= -threshold:
        direction = "SHORT"
    else:
        direction = "BOTH"

    state.cascade_allowed_direction = direction
    snap.cascade_allowed_direction = direction
    snap.signal = "NEUTRAL"  # bias TF never generates trade signals


def _evaluate_context(snap: SignalSnapshot, state: MarketState) -> None:
    """1h CONTEXT: must confirm 4h bias direction."""
    allowed = state.cascade_allowed_direction
    snap.cascade_allowed_direction = allowed

    if allowed == "BOTH":
        # 4h neutral — context passes through
        confirmed = True
    elif allowed == "LONG":
        confirmed = snap.total_score > 0
    elif allowed == "SHORT":
        confirmed = snap.total_score < 0
    else:
        confirmed = False

    state.cascade_context_confirmed = confirmed
    snap.cascade_context_confirmed = confirmed
    snap.signal = "NEUTRAL"  # context TF never generates trade signals


def _evaluate_timing(
    snap: SignalSnapshot, state: MarketState, tf_state: TimeframeState
) -> None:
    """15m TIMING: identifies entry zone when 4h+1h agree."""
    snap.cascade_allowed_direction = state.cascade_allowed_direction
    snap.cascade_context_confirmed = state.cascade_context_confirmed

    # If context not confirmed, clear zone
    if not state.cascade_context_confirmed:
        tf_state.active_zone_direction = None
        tf_state.active_zone_timestamp = 0.0
        snap.cascade_timing_zone_active = False
        snap.cascade_blocked_reason = "context_not_confirmed"
        snap.signal = "NEUTRAL"
        return

    # Threshold check
    threshold = snap.active_threshold
    raw_signal = "NEUTRAL"
    if snap.total_score >= threshold:
        raw_signal = "LONG"
    elif snap.total_score <= -threshold:
        raw_signal = "SHORT"

    # Direction must match allowed direction from 4h bias
    allowed = state.cascade_allowed_direction
    if raw_signal != "NEUTRAL" and allowed != "BOTH" and raw_signal != allowed:
        raw_signal = "NEUTRAL"
        snap.cascade_blocked_reason = "timing_against_bias"

    if raw_signal != "NEUTRAL":
        # Establish or refresh zone
        tf_state.active_zone_direction = raw_signal
        tf_state.active_zone_timestamp = time.time()
        snap.cascade_timing_zone_active = True
        snap.cascade_timing_zone_direction = raw_signal
        snap.cascade_timing_zone_timestamp = time.time()
    else:
        # Check TTL on existing zone
        if tf_state.active_zone_direction:
            age = time.time() - tf_state.active_zone_timestamp
            if age > config.CASCADE_TIMING_ZONE_TTL:
                tf_state.active_zone_direction = None
                tf_state.active_zone_timestamp = 0.0
                snap.cascade_timing_zone_active = False
            else:
                # Zone still valid from earlier
                snap.cascade_timing_zone_active = True
                snap.cascade_timing_zone_direction = tf_state.active_zone_direction
                snap.cascade_timing_zone_timestamp = tf_state.active_zone_timestamp
        else:
            snap.cascade_timing_zone_active = False

    snap.signal = "NEUTRAL"  # timing TF never generates trade signals


def _evaluate_trigger(
    snap: SignalSnapshot, state: MarketState, tf_state: TimeframeState
) -> None:
    """5m TRIGGER: generates actual trade signals only within 15m zone."""
    snap.cascade_allowed_direction = state.cascade_allowed_direction
    snap.cascade_context_confirmed = state.cascade_context_confirmed
    snap.signal = "NEUTRAL"
    snap.signal_blocked_reason = None

    # ── Check 15m timing zone ────────────────────────────────────
    tf_15m = state.tf_states.get("15m")
    zone_active = False
    zone_direction = None
    if tf_15m and tf_15m.active_zone_direction:
        age = time.time() - tf_15m.active_zone_timestamp
        if age <= config.CASCADE_TIMING_ZONE_TTL:
            zone_active = True
            zone_direction = tf_15m.active_zone_direction

    snap.cascade_timing_zone_active = zone_active
    snap.cascade_timing_zone_direction = zone_direction

    if not zone_active:
        snap.cascade_blocked_reason = "no_timing_zone"
        return

    # ── RuntimeConfig threshold override ─────────────────────────
    rt = state.runtime
    threshold = snap.active_threshold
    if rt:
        threshold = rt.scoring_threshold_5m
    snap.active_threshold = threshold

    # ── Threshold check ──────────────────────────────────────────
    if snap.total_score >= threshold:
        snap.signal = "LONG"
    elif snap.total_score <= -threshold:
        snap.signal = "SHORT"
    else:
        return

    # ── Direction must match zone direction ──────────────────────
    if snap.signal != zone_direction:
        snap.cascade_blocked_reason = "trigger_against_zone"
        snap.signal = "NEUTRAL"
        return

    # ── Direction must match 4h allowed direction ────────────────
    allowed = state.cascade_allowed_direction
    if allowed != "BOTH" and snap.signal != allowed:
        snap.cascade_blocked_reason = "trigger_against_bias"
        snap.signal = "NEUTRAL"
        return

    snap.cascade_blocked_reason = None

    # ── Funding filter ───────────────────────────────────────────
    if snap.funding_tier == "danger":
        against = (
            (state.funding_rate > 0 and snap.signal == "LONG")
            or (state.funding_rate < 0 and snap.signal == "SHORT")
        )
        if against:
            snap.signal_blocked_reason = "funding_danger"
            snap.signal = "NEUTRAL"
            return

    # ── Runtime pause check ──────────────────────────────────────
    if rt and rt.trading_paused:
        snap.signal_blocked_reason = "trading_paused"
        snap.signal = "NEUTRAL"
        return

    # ── Risk state checks ────────────────────────────────────────
    risk = state.risk
    if risk.daily_paused:
        snap.signal_blocked_reason = "daily_paused"
        snap.signal = "NEUTRAL"
    elif risk.full_stop_active:
        snap.signal_blocked_reason = "full_stop"
        snap.signal = "NEUTRAL"
