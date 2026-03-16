"""Composite bias score aggregation per TF — signal generation.

Changes from original:
  1. Thresholds pulled from RuntimeConfig if available (hot-reload via Telegram /set)
  2. MTF filter is a soft penalty/bonus, not a hard veto
     - Aligned:   score += MTF_ALIGNMENT_BONUS (default +1.5)
     - Misaligned: score *= MTF_MISALIGN_PENALTY (default 0.7), still can signal
     - Exception: if misalignment is strong (filter TF score strongly opposing),
       block is reinstated to prevent counter-trend trap
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

# MTF hierarchy constants
MTF_ALIGNMENT_BONUS = 1.5      # added to score when MTF agrees
MTF_MISALIGN_PENALTY = 0.7     # multiplier when MTF disagrees (softer than hard block)
MTF_STRONG_OPPOSE_THRESHOLD = 3.5  # lowered from 5.0 — blocks entry when filter TF score opposes at ±3.5+


def compute_signal(state: MarketState, tf_name: str) -> SignalSnapshot:
    """Compute all indicators and generate a signal for one timeframe."""
    tf_state = state.tf_states[tf_name]
    tf_cfg = config.TIMEFRAMES[tf_name]
    mid = state.mid_price

    snap = SignalSnapshot(timestamp=time.time(), timeframe=tf_name)

    # ── Category A: Order Flow ─────────────────────────────────────
    snap.cvd_zscore_raw, snap.cvd_score = compute_cvd_score(state, tf_state)
    snap.obi_raw, snap.obi_score = compute_obi_score(state, tf_cfg["obi_band_pct"])
    snap.depth_ratio, snap.depth_score = compute_depth_score(state)

    # ── Category B: Structure ──────────────────────────────────────
    snap.vwap_value, snap.vwap_score = compute_vwap(tf_state.klines, mid)
    snap.poc_value, snap.poc_score = compute_poc(tf_state.klines, mid)
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

    # Filter TFs: compute only, no signal
    if tf_cfg["role"] != "execution":
        tf_state.signal = snap
        return snap

    # ── Threshold — use RuntimeConfig if available (hot-reload) ───
    rt = state.runtime
    if rt:
        if tf_name == "5m":
            threshold = rt.scoring_threshold_5m
        elif tf_name == "15m":
            threshold = rt.scoring_threshold_15m
    snap.active_threshold = threshold

    # ── Signal decision ───────────────────────────────────────────
    snap.signal = "NEUTRAL"
    snap.signal_blocked_reason = None

    if snap.total_score >= threshold:
        snap.signal = "LONG"
    elif snap.total_score <= -threshold:
        snap.signal = "SHORT"
    else:
        tf_state.signal = snap
        return snap

    # ── MTF hierarchy filter (soft penalty, not hard veto) ────────
    mtf_tf_name = tf_cfg["mtf_filter_tf"]
    if mtf_tf_name:
        mtf_state = state.tf_states.get(mtf_tf_name)
        if mtf_state and mtf_state.signal:
            mtf_snap = mtf_state.signal
            snap.mtf_vwap = mtf_snap.vwap_value

            if snap.mtf_vwap and snap.mtf_vwap > 0:
                snap.mtf_aligned_long = mid > snap.mtf_vwap
                snap.mtf_aligned_short = mid < snap.mtf_vwap

                if snap.signal == "LONG":
                    if snap.mtf_aligned_long:
                        # MTF agrees → bonus
                        snap.total_score += MTF_ALIGNMENT_BONUS
                    else:
                        # MTF disagrees → check strength
                        if mtf_snap.total_score <= -MTF_STRONG_OPPOSE_THRESHOLD:
                            # Filter TF strongly bearish, hard block LONG
                            snap.signal_blocked_reason = "mtf_strong_oppose"
                            snap.signal = "NEUTRAL"
                        else:
                            # Soft penalty
                            snap.total_score *= MTF_MISALIGN_PENALTY
                            snap.signal_blocked_reason = "mtf_soft_penalty"
                            # Re-check threshold after penalty
                            if snap.total_score < threshold:
                                snap.signal = "NEUTRAL"
                            else:
                                snap.signal_blocked_reason = None

                elif snap.signal == "SHORT":
                    if snap.mtf_aligned_short:
                        snap.total_score -= MTF_ALIGNMENT_BONUS  # more negative = stronger SHORT
                    else:
                        if mtf_snap.total_score >= MTF_STRONG_OPPOSE_THRESHOLD:
                            snap.signal_blocked_reason = "mtf_strong_oppose"
                            snap.signal = "NEUTRAL"
                        else:
                            snap.total_score *= MTF_MISALIGN_PENALTY
                            snap.signal_blocked_reason = "mtf_soft_penalty"
                            if snap.total_score > -threshold:
                                snap.signal = "NEUTRAL"
                            else:
                                snap.signal_blocked_reason = None

    if snap.signal == "NEUTRAL":
        tf_state.signal = snap
        return snap

    # ── Funding filter ────────────────────────────────────────────
    if snap.funding_tier == "danger":
        against = (
            (state.funding_rate > 0 and snap.signal == "LONG")
            or (state.funding_rate < 0 and snap.signal == "SHORT")
        )
        if against:
            snap.signal_blocked_reason = "funding_danger"
            snap.signal = "NEUTRAL"

    # ── Runtime pause check ───────────────────────────────────────
    if snap.signal != "NEUTRAL" and rt and rt.trading_paused:
        snap.signal_blocked_reason = "trading_paused"
        snap.signal = "NEUTRAL"

    # ── Risk state checks ─────────────────────────────────────────
    if snap.signal != "NEUTRAL":
        risk = state.risk
        if risk.daily_paused:
            snap.signal_blocked_reason = "daily_paused"
            snap.signal = "NEUTRAL"
        elif risk.full_stop_active:
            snap.signal_blocked_reason = "full_stop"
            snap.signal = "NEUTRAL"

    tf_state.signal = snap
    return snap
