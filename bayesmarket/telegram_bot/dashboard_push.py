"""Telegram push dashboard — live ASCII ticker yang auto-update.

Dua mode:
  - Pull: user ketik /dashboard → snapshot sekarang
  - Push: satu pesan di-edit tiap PUSH_INTERVAL detik (live ticker)

Format mengikuti style monospace Telegram seperti screenshot referensi.
"""

import asyncio
import time
from typing import Optional, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from telegram.ext import Application
    from bayesmarket.data.state import MarketState
    from bayesmarket.runtime import RuntimeConfig

logger = structlog.get_logger()

PUSH_INTERVAL = 30      # detik antar update
PUSH_AUTO = True        # bisa di-toggle via /dashboard auto on/off

# State: message_id dari pesan dashboard yang sedang aktif
_dashboard_message_id: Optional[int] = None
_app: Optional["Application"] = None
_chat_id: Optional[str] = None


def init_push_dashboard(app: "Application", chat_id: str) -> None:
    global _app, _chat_id
    _app = app
    _chat_id = chat_id


def _score_bar(score: float, width: int = 6) -> str:
    """Compact bar untuk Telegram monospace."""
    ratio = min(abs(score) / 13.5, 1.0)
    filled = int(ratio * width)
    empty = width - filled
    return "▓" * filled + "░" * empty


def _signal_short(signal: str, blocked: str | None) -> str:
    if blocked:
        return f"○ {signal[:7]:7s}"
    if signal == "LONG":
        return "▲ LONG  "
    if signal == "SHORT":
        return "▼ SHORT "
    return "─ NEUTRAL"


def build_dashboard_text(state: "MarketState", rt: "RuntimeConfig") -> str:
    """Build full ASCII dashboard text untuk Telegram."""
    now_str = time.strftime("%m/%d %H:%M")
    pos = state.position
    risk = state.risk

    network = rt.network_label if rt else "🟡 SHADOW"

    # ── Header ─────────────────────────────────────────────────────────────────
    lines = [
        f"⚡ *BAYESMARKET — {now_str}*",
        f"Token : `{state.__class__.__name__ and 'BTC-PERP'}`  |  {network}",
        "",
        "```",
        f"{'METRIC':<16}| {'VALUE':<12}| STATUS",
        f"{'-'*16}|{'-'*13}|{'-'*10}",
    ]

    # ── Price & Capital ─────────────────────────────────────────────────────────
    price = f"${state.mid_price:,.1f}" if state.mid_price > 0 else "---"
    pnl_str = f"{risk.daily_pnl:+.2f}"
    pnl_pct = risk.daily_pnl / state.capital * 100 if state.capital > 0 else 0
    lines += [
        f"{'PRICE':<16}| {price:<12}| –",
        f"{'CAPITAL':<16}| ${state.capital:,.2f}  | –",
        f"{'DAILY PNL':<16}| {pnl_str:<12}| {pnl_pct:+.2f}%",
        f"{'-'*16}|{'-'*13}|{'-'*10}",
    ]

    # ── Scores ─────────────────────────────────────────────────────────────────
    for tf in ["5m", "15m", "1h", "4h"]:
        tfs = state.tf_states.get(tf)
        snap = tfs.signal if tfs else None
        if snap:
            bar = _score_bar(snap.total_score)
            sig = _signal_short(snap.signal, snap.signal_blocked_reason)
            lines.append(
                f"{'SCORE ' + tf:<16}| {snap.total_score:+.1f} {bar}  | {sig}"
            )
        else:
            lines.append(f"{'SCORE ' + tf:<16}| warming... | –")

    # ── Cascade status ─────────────────────────────────────────────────────────
    tf_15m = state.tf_states.get("15m")
    zone = tf_15m.active_zone_direction if (tf_15m and tf_15m.active_zone_direction) else "NONE"
    ctx_ok = "Y" if state.cascade_context_confirmed else "N"
    lines.append(
        f"{'CASCADE':<16}| {state.cascade_allowed_direction:<12}| "
        f"CTX:{ctx_ok} Z:{zone}"
    )
    lines.append(f"{'-'*16}|{'-'*13}|{'-'*10}")

    # ── Categories (5m) ────────────────────────────────────────────────────────
    snap5 = state.tf_states.get("5m")
    snap5 = snap5.signal if snap5 else None
    if snap5:
        lines += [
            f"{'CAT A (Flow)':<16}| {snap5.category_a:+.2f}       | {'BULL' if snap5.category_a > 0 else 'BEAR'}",
            f"{'CAT B (Struct)':<16}| {snap5.category_b:+.2f}       | {'BULL' if snap5.category_b > 0 else 'BEAR'}",
            f"{'CAT C (Mom)':<16}| {snap5.category_c:+.2f}       | {'BULL' if snap5.category_c > 0 else 'BEAR'}",
            f"{'-'*16}|{'-'*13}|{'-'*10}",
            f"{'OBI':<16}| {snap5.obi_score:+.2f}       | {'BUY' if snap5.obi_score > 0 else 'SELL'}",
            f"{'CVD Z-SCORE':<16}| {snap5.cvd_zscore_raw:+.1f}σ      | {'BULL' if snap5.cvd_zscore_raw > 0 else 'BEAR'}",
            f"{'VWAP':<16}| ${snap5.vwap_value:,.0f}   | {'ABOVE' if state.mid_price > snap5.vwap_value else 'BELOW'}",
            f"{'RSI (14)':<16}| {(snap5.rsi_value or 0):.1f}        | {'OVERSOLD' if (snap5.rsi_value or 50) < 35 else ('OVERBOUGHT' if (snap5.rsi_value or 50) > 65 else 'NEUTRAL')}",
            f"{'-'*16}|{'-'*13}|{'-'*10}",
        ]

    # ── Position ───────────────────────────────────────────────────────────────
    if pos:
        from bayesmarket.engine.position import calculate_unrealized_pnl
        unrealized = calculate_unrealized_pnl(pos, state.mid_price)
        pnl_pct_pos = unrealized / state.capital * 100 if state.capital > 0 else 0
        hold_min = (time.time() - pos.entry_time) / 60
        hold_str = f"{int(hold_min)}m" if hold_min < 60 else f"{hold_min/60:.1f}h"
        hold_warn = " ⚠️" if hold_min > 60 else ""
        lines += [
            f"{'POSITION':<16}| {pos.side.upper():<12}| {'OPEN'}",
            f"{'ENTRY':<16}| ${pos.entry_price:,.1f}  | –",
            f"{'SL':<16}| ${pos.sl_price:,.1f}  | [{pos.sl_basis}]",
            f"{'TP1':<16}| ${pos.tp1_price:,.1f}  | [60%]{'✓' if pos.tp1_hit else ''}",
            f"{'UNREALIZED':<16}| ${unrealized:+.2f}     | {pnl_pct_pos:+.2f}%",
            f"{'HOLD TIME':<16}| {hold_str:<12}| {hold_warn.strip() or '–'}",
            f"{'-'*16}|{'-'*13}|{'-'*10}",
        ]
    else:
        lines.append(f"{'POSITION':<16}| {'NONE':<12}| –")
        lines.append(f"{'-'*16}|{'-'*13}|{'-'*10}")

    # ── System ─────────────────────────────────────────────────────────────────
    regime = "–"
    if snap5:
        regime = snap5.regime.upper()
    risk_label = "NORMAL"
    if risk.full_stop_active:
        risk_label = "FULL STOP"
    elif risk.daily_paused:
        risk_label = "PAUSED"
    elif risk.cooldown_active:
        risk_label = "COOLDOWN"

    fund_str = f"{state.funding_rate*100:.4f}%/h"
    lines += [
        f"{'REGIME':<16}| {regime:<12}| –",
        f"{'FUNDING':<16}| {fund_str:<12}| {state.funding_tier.upper()}",
        f"{'RISK STATE':<16}| {risk_label:<12}| W:{risk.consecutive_wins} L:{risk.consecutive_losses}",
        f"{'TRADES TODAY':<16}| {risk.trades_today:<12}| –",
        "```",
    ]

    # ── Footer signal explanation ───────────────────────────────────────────────
    if snap5 and snap5.signal != "NEUTRAL":
        direction = "naik" if snap5.signal == "LONG" else "turun"
        lines += [
            "",
            f"🧠 *Signal ({snap5.signal} — 5m TRIGGER)*",
            f"Score: `{snap5.total_score:+.1f}` | Threshold: `{snap5.active_threshold}`",
            f"Cascade: 4h={snap5.cascade_allowed_direction} → 1h={'✓' if snap5.cascade_context_confirmed else '✗'} → 15m={'Active' if snap5.cascade_timing_zone_active else 'None'}",
        ]
        if snap5.cascade_blocked_reason:
            lines.append(f"⚠️ Cascade: `{snap5.cascade_blocked_reason}`")
        if snap5.signal_blocked_reason:
            lines.append(f"⚠️ Blocked: `{snap5.signal_blocked_reason}`")

    return "\n".join(lines)


async def send_dashboard_once(
    state: "MarketState",
    rt: "RuntimeConfig",
    chat_id: str,
    app: "Application",
) -> Optional[int]:
    """Send new dashboard message. Returns message_id."""
    try:
        text = build_dashboard_text(state, rt)
        msg = await app.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
        )
        return msg.message_id
    except Exception as exc:
        logger.error("dashboard_send_failed", error=str(exc))
        return None


async def edit_dashboard(
    state: "MarketState",
    rt: "RuntimeConfig",
    chat_id: str,
    app: "Application",
    message_id: int,
) -> bool:
    """Edit existing dashboard message. Returns False if message no longer exists."""
    try:
        text = build_dashboard_text(state, rt)
        await app.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="Markdown",
        )
        return True
    except Exception as exc:
        err = str(exc)
        if "message is not modified" in err:
            return True  # no change, still valid
        if "message to edit not found" in err or "MESSAGE_ID_INVALID" in err:
            return False  # message deleted, need to resend
        logger.error("dashboard_edit_failed", error=err)
        return True


async def dashboard_push_loop(
    state: "MarketState",
    rt: "RuntimeConfig",
    app: "Application",
    chat_id: str,
) -> None:
    """Push dashboard loop — edits one Telegram message every PUSH_INTERVAL seconds.

    Creates a new message if none exists or previous was deleted.
    """
    global _dashboard_message_id, PUSH_AUTO

    logger.info("dashboard_push_loop_started", interval=PUSH_INTERVAL)

    # Wait for bot and state to warm up
    await asyncio.sleep(15)

    while True:
        try:
            if not PUSH_AUTO:
                await asyncio.sleep(5)
                continue

            if state.mid_price <= 0:
                await asyncio.sleep(5)
                continue

            if _dashboard_message_id is None:
                # First run or message was deleted — send new
                _dashboard_message_id = await send_dashboard_once(state, rt, chat_id, app)
                logger.info("dashboard_push_created", msg_id=_dashboard_message_id)
            else:
                still_valid = await edit_dashboard(state, rt, chat_id, app, _dashboard_message_id)
                if not still_valid:
                    # Message was deleted by user — send fresh one
                    _dashboard_message_id = await send_dashboard_once(state, rt, chat_id, app)
                    logger.info("dashboard_push_recreated", msg_id=_dashboard_message_id)

        except Exception as exc:
            logger.error("dashboard_push_loop_error", error=str(exc))

        await asyncio.sleep(PUSH_INTERVAL)


def toggle_push(enable: bool) -> str:
    global PUSH_AUTO
    PUSH_AUTO = enable
    status = "ON ✅" if enable else "OFF ⏸️"
    return f"Dashboard auto-push: *{status}*"


def reset_dashboard_message() -> None:
    """Force next cycle to send a new message instead of editing."""
    global _dashboard_message_id
    _dashboard_message_id = None
