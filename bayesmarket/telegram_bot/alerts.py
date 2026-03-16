"""Outbound Telegram alerts — dipanggil dari executor dan engine."""

import time
from typing import Optional, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from telegram.ext import Application

logger = structlog.get_logger()

# Global reference ke bot app dan chat_id — diset saat startup
_app: Optional["Application"] = None
_chat_id: Optional[str] = None


def init_alerts(app: "Application", chat_id: str) -> None:
    """Inisialisasi module dengan bot app dan target chat_id."""
    global _app, _chat_id
    _app = app
    _chat_id = chat_id
    logger.info("telegram_alerts_initialized", chat_id=chat_id)


async def send_alert(message: str, parse_mode: str = "Markdown") -> None:
    """Kirim alert ke Telegram. Silent fail jika bot belum diinit."""
    if _app is None or _chat_id is None:
        return
    try:
        await _app.bot.send_message(
            chat_id=_chat_id,
            text=message,
            parse_mode=parse_mode,
        )
    except Exception as exc:
        logger.error("telegram_alert_failed", error=str(exc))


async def alert_entry(
    side: str,
    entry_price: float,
    size: float,
    sl_price: float,
    sl_basis: str,
    tp1_price: float,
    tp2_price: float,
    source_tfs: list,
    score: float,
    mode: str,
    capital: float,
) -> None:
    sl_dist_pct = abs(entry_price - sl_price) / entry_price * 100
    tp1_dist_pct = abs(tp1_price - entry_price) / entry_price * 100
    risk_usd = capital * 0.02

    side_emoji = "🟢" if side.upper() == "LONG" else "🔴"
    mode_emoji = "🔴" if mode == "LIVE" else "🟡"

    msg = (
        f"{side_emoji} *{mode_emoji} {mode} ENTRY — {side.upper()}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Entry:  `${entry_price:,.1f}`\n"
        f"📦 Size:   `{size:.5f} BTC` (${size * entry_price:,.0f})\n"
        f"🛑 SL:     `${sl_price:,.1f}` ({sl_dist_pct:.2f}%) `[{sl_basis}]`\n"
        f"🎯 TP1:    `${tp1_price:,.1f}` ({tp1_dist_pct:.2f}%) `[60%]`\n"
        f"🎯 TP2:    `${tp2_price:,.1f}` `[40%]`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Score:  `{score:+.2f}`\n"
        f"⏱️  Source: `{'+'.join(source_tfs)}`\n"
        f"💵 Risk:   `${risk_usd:.2f}` (2% capital)"
    )
    await send_alert(msg)


async def alert_tp1(
    side: str,
    tp1_price: float,
    pnl: float,
    remaining_size: float,
    mode: str,
) -> None:
    msg = (
        f"🎯 *{mode} TP1 HIT — {side.upper()}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Exit 60%:  `${tp1_price:,.1f}`\n"
        f"💵 PnL:       `${pnl:+.2f}`\n"
        f"📦 Remaining: `{remaining_size:.5f} BTC` (40%)\n"
        f"📌 SL moved to: breakeven tracking aktif"
    )
    await send_alert(msg)


async def alert_exit(
    side: str,
    entry_price: float,
    exit_price: float,
    pnl: float,
    pnl_pct: float,
    exit_reason: str,
    duration_seconds: float,
    tp1_hit: bool,
    mode: str,
    diagnosis=None,
) -> None:
    # If loss with diagnosis, use rich format from loss_analyzer
    if diagnosis and pnl < 0:
        try:
            from bayesmarket.engine.loss_analyzer import format_loss_alert
            # Build minimal pos-like object for formatter
            class _PosMock:
                pass
            pos_mock = _PosMock()
            pos_mock.side = side
            pos_mock.entry_price = entry_price
            pos_mock.sl_price = entry_price * (0.99 if side.lower() == "long" else 1.01)
            pos_mock.tp1_price = entry_price * (1.002 if side.lower() == "long" else 0.998)
            pos_mock.sl_basis = diagnosis.sl_basis
            pos_mock.source_tfs = ["5m"]
            pos_mock.entry_score_5m = diagnosis.score_at_entry
            pos_mock.entry_score_15m = diagnosis.score_at_entry
            pos_mock.entry_time = time.time() - diagnosis.hold_minutes * 60
            msg = format_loss_alert(diagnosis, pos_mock, exit_price, pnl, mode)
            await send_alert(msg)
            return
        except Exception as exc:
            logger.error("loss_alert_format_failed", error=str(exc))

    duration_min = int(duration_seconds // 60)
    duration_sec = int(duration_seconds % 60)

    reason_map = {
        "tp2_hit": "🎯 TP2 Hit",
        "sl_hit": "🛑 SL Hit",
        "time_exit": "⏱️ Time Exit",
        "force_close": "👤 Manual Close",
    }
    reason_label = reason_map.get(exit_reason, exit_reason)
    pnl_emoji = "✅" if pnl >= 0 else "❌"

    msg = (
        f"{pnl_emoji} *{mode} TRADE CLOSED — {side.upper()}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Reason:  {reason_label}\n"
        f"💰 Entry:   `${entry_price:,.1f}`\n"
        f"💰 Exit:    `${exit_price:,.1f}`\n"
        f"💵 PnL:     `${pnl:+.2f}` ({pnl_pct:+.2f}%)\n"
        f"⏱️  Duration: `{duration_min}m {duration_sec}s`\n"
        f"🎯 TP1:     {'✅ Hit' if tp1_hit else '❌ Missed'}"
    )
    await send_alert(msg)


async def alert_daily_report(
    trades_today: int,
    wins: int,
    losses: int,
    daily_pnl: float,
    capital: float,
) -> None:
    win_rate = wins / trades_today * 100 if trades_today > 0 else 0
    pnl_pct = daily_pnl / capital * 100 if capital > 0 else 0
    pnl_emoji = "📈" if daily_pnl >= 0 else "📉"

    msg = (
        f"{pnl_emoji} *DAILY REPORT — {time.strftime('%Y-%m-%d')}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Trades:   `{trades_today}` ({wins}W / {losses}L)\n"
        f"🎯 Win Rate: `{win_rate:.1f}%`\n"
        f"💵 Daily PnL: `${daily_pnl:+.2f}` ({pnl_pct:+.2f}%)\n"
        f"💰 Capital:  `${capital:,.2f}`"
    )
    await send_alert(msg)


async def alert_risk_event(event: str, details: str) -> None:
    event_map = {
        "cooldown": "⚠️ COOLDOWN AKTIF",
        "full_stop": "🚨 FULL STOP AKTIF",
        "daily_pause": "⛔ DAILY LOSS LIMIT",
        "cooldown_reset": "✅ Cooldown Reset",
        "full_stop_reset": "✅ Full Stop Reset",
    }
    label = event_map.get(event, f"⚠️ {event.upper()}")
    msg = f"{label}\n`{details}`"
    await send_alert(msg)
