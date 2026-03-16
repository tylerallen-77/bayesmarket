"""Loss trade auto-classifier dan post-mortem recorder.

Dipanggil dari executor.py saat SL hit atau time_exit dengan pnl < 0.
Mengklasifikasi penyebab loss dan menyimpan diagnosis ke DB.
"""

import time
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from bayesmarket.data.state import MarketState, Position

logger = structlog.get_logger()


# ── Loss category constants ────────────────────────────────────────────────────

CATEGORY_STALE_POC_SL      = "stale_poc_sl"       # SL based on stale POC > 1% away
CATEGORY_POOR_RR           = "poor_rr_entry"       # RR ratio < 0.5 at entry
CATEGORY_TREND_REVERSAL    = "trend_reversal"       # Score flipped before SL hit
CATEGORY_TIME_OVERHELD     = "time_overheld"        # Held > 2x TIME_EXIT limit
CATEGORY_CHOPPY_MARKET     = "choppy_market"        # Score oscillated, no clear bias
CATEGORY_MTF_MISALIGNED    = "mtf_misaligned_entry" # Filter TF was opposing at entry
CATEGORY_NORMAL_SL         = "normal_sl"            # Clean SL, no special pattern


@dataclass
class LossDiagnosis:
    """Post-mortem analysis for a losing trade."""
    trade_id: Optional[int]
    category: str
    severity: str                # "critical" | "moderate" | "minor"
    sl_distance_pct: float
    tp1_distance_pct: float
    rr_ratio: float
    hold_minutes: float
    score_at_entry: float
    score_at_exit: float
    score_flipped: bool          # True if score changed direction during hold
    sl_basis: str
    exit_reason: str
    diagnosis_text: str          # Human-readable explanation
    recommendation: str          # Actionable suggestion


def classify_loss(
    pos: "Position",
    state: "MarketState",
    exit_price: float,
    exit_reason: str,
    exit_score: float,
) -> LossDiagnosis:
    """Classify why a trade lost money.

    Called from executor._close_position() when pnl < 0.
    Returns structured diagnosis for alert and DB storage.
    """
    from bayesmarket import config

    # ── Basic metrics ──────────────────────────────────────────────────────────
    sl_dist = abs(pos.entry_price - pos.sl_price)
    tp1_dist = abs(pos.entry_price - pos.tp1_price)
    sl_dist_pct = sl_dist / pos.entry_price * 100 if pos.entry_price > 0 else 0
    tp1_dist_pct = tp1_dist / pos.entry_price * 100 if pos.entry_price > 0 else 0
    rr_ratio = tp1_dist / sl_dist if sl_dist > 0 else 0
    hold_minutes = (time.time() - pos.entry_time) / 60

    entry_score = pos.entry_score_5m or pos.entry_score_15m or 0.0
    score_flipped = (
        (pos.side == "short" and exit_score > 0) or
        (pos.side == "long"  and exit_score < 0)
    )

    # ── Category detection (priority order) ───────────────────────────────────
    category = CATEGORY_NORMAL_SL
    severity = "minor"
    diagnosis_parts = []
    recommendation_parts = []

    # 1. Stale POC SL (most common critical failure from data)
    if pos.sl_basis == "poc" and sl_dist_pct > 1.0:
        category = CATEGORY_STALE_POC_SL
        severity = "critical"
        diagnosis_parts.append(
            f"SL basis adalah POC stale (${pos.sl_price:,.0f}) "
            f"dengan jarak {sl_dist_pct:.2f}% — terlalu jauh"
        )
        recommendation_parts.append(
            "MAX_SL_TP_RATIO perlu aktif untuk cap SL ini"
        )

    # 2. Poor RR from start
    if rr_ratio < 0.5:
        if category == CATEGORY_NORMAL_SL:
            category = CATEGORY_POOR_RR
        severity = "critical"
        diagnosis_parts.append(
            f"RR ratio saat entry: 1:{rr_ratio:.2f} — terbalik "
            f"(SL ${sl_dist:.0f} vs TP1 ${tp1_dist:.0f})"
        )
        recommendation_parts.append(
            "Breakeven WR yang dibutuhkan terlalu tinggi (>66%)"
        )

    # 3. Score flipped — trend reversal ignored
    if score_flipped and category == CATEGORY_NORMAL_SL:
        category = CATEGORY_TREND_REVERSAL
        severity = "moderate"
        direction = "bullish" if pos.side == "short" else "bearish"
        diagnosis_parts.append(
            f"Score saat exit: {exit_score:+.1f} sudah {direction} "
            f"sementara posisi masih {pos.side.upper()}"
        )
        recommendation_parts.append(
            "Early exit trigger jika score berbalik kuat selama hold"
        )
    elif score_flipped:
        diagnosis_parts.append(
            f"Score saat exit {exit_score:+.1f} sudah berlawanan arah posisi"
        )

    # 4. Time overheld (>2x time exit limit)
    time_limit = (
        getattr(config, "TIME_EXIT_MINUTES_5M", 30)
        if "5m" in pos.source_tfs
        else getattr(config, "TIME_EXIT_MINUTES_15M", 90)
    )
    if hold_minutes > time_limit * 2:
        if category == CATEGORY_NORMAL_SL:
            category = CATEGORY_TIME_OVERHELD
        severity = "critical" if severity != "critical" else severity
        diagnosis_parts.append(
            f"Posisi terbuka {hold_minutes:.0f} menit "
            f"(limit: {time_limit}m, actual: {hold_minutes:.0f}m)"
        )
        recommendation_parts.append(
            f"TIME_EXIT_ENABLED harus True dan TIME_EXIT_MINUTES_5M={time_limit}"
        )

    # 5. Score oscillated — choppy market
    # (heuristic: if entry score was borderline < threshold + 2)
    entry_threshold = getattr(config.TIMEFRAMES.get("5m", {}), "scoring_threshold", 7.0)
    if isinstance(entry_threshold, dict):
        entry_threshold = 7.0
    if abs(entry_score) < entry_threshold + 1.5 and category == CATEGORY_NORMAL_SL:
        category = CATEGORY_CHOPPY_MARKET
        severity = "moderate"
        diagnosis_parts.append(
            f"Entry score borderline ({entry_score:+.1f}) — "
            f"pasar kemungkinan ranging saat entry"
        )
        recommendation_parts.append(
            "Threshold lebih ketat di ranging market (scoring_threshold_ranging)"
        )

    # ── Build final text ───────────────────────────────────────────────────────
    if not diagnosis_parts:
        diagnosis_parts.append(
            f"SL hit normal. "
            f"Entry score {entry_score:+.1f}, exit score {exit_score:+.1f}"
        )
        recommendation_parts.append("Tidak ada anomali — review market condition")

    diagnosis_text = " | ".join(diagnosis_parts)
    recommendation = " | ".join(recommendation_parts)

    return LossDiagnosis(
        trade_id=None,  # filled after DB insert
        category=category,
        severity=severity,
        sl_distance_pct=round(sl_dist_pct, 3),
        tp1_distance_pct=round(tp1_dist_pct, 3),
        rr_ratio=round(rr_ratio, 3),
        hold_minutes=round(hold_minutes, 1),
        score_at_entry=round(entry_score, 2),
        score_at_exit=round(exit_score, 2),
        score_flipped=score_flipped,
        sl_basis=pos.sl_basis,
        exit_reason=exit_reason,
        diagnosis_text=diagnosis_text,
        recommendation=recommendation,
    )


def format_loss_alert(
    diagnosis: LossDiagnosis,
    pos: "Position",
    exit_price: float,
    pnl: float,
    mode: str,
) -> str:
    """Format loss trade alert for Telegram — rich diagnostic format."""
    severity_emoji = {
        "critical": "🚨",
        "moderate": "⚠️",
        "minor": "📋",
    }.get(diagnosis.severity, "📋")

    category_label = {
        CATEGORY_STALE_POC_SL:      "POC SL Stale",
        CATEGORY_POOR_RR:           "RR Ratio Buruk",
        CATEGORY_TREND_REVERSAL:    "Trend Reversal",
        CATEGORY_TIME_OVERHELD:     "Time Overheld",
        CATEGORY_CHOPPY_MARKET:     "Choppy Market",
        CATEGORY_MTF_MISALIGNED:    "MTF Misaligned",
        CATEGORY_NORMAL_SL:         "Normal SL",
    }.get(diagnosis.category, diagnosis.category)

    lines = [
        f"{severity_emoji} *{mode} LOSS — {pos.side.upper()}*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Entry:    `${pos.entry_price:,.1f}`",
        f"Exit:     `${exit_price:,.1f}` via `{diagnosis.exit_reason}`",
        f"PnL:      `${pnl:+.2f}`",
        f"Hold:     `{diagnosis.hold_minutes:.0f} menit`",
        "",
        "```",
        f"METRIC      | VALUE",
        f"------------|------------------",
        f"SL basis    | {diagnosis.sl_basis.upper()}",
        f"SL dist     | {diagnosis.sl_distance_pct:.2f}%  (${abs(pos.entry_price - pos.sl_price):,.0f})",
        f"TP1 dist    | {diagnosis.tp1_distance_pct:.2f}%  (${abs(pos.entry_price - pos.tp1_price):,.0f})",
        f"RR ratio    | 1:{diagnosis.rr_ratio:.2f}",
        f"Score entry | {diagnosis.score_at_entry:+.1f}",
        f"Score exit  | {diagnosis.score_at_exit:+.1f}",
        f"Flip        | {'YES ⚠️' if diagnosis.score_flipped else 'NO'}",
        "```",
        "",
        f"📋 *Diagnosis: {category_label}*",
        f"`{diagnosis.diagnosis_text}`",
        "",
        f"💡 *Saran:*",
        f"`{diagnosis.recommendation}`",
    ]

    return "\n".join(lines)
