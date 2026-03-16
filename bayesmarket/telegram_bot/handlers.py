"""Telegram bot command handlers — control panel utama BayesMarket."""

import sqlite3
import time
from typing import TYPE_CHECKING

import structlog
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bayesmarket import config
from bayesmarket.telegram_bot.keyboards import (
    close_position_keyboard,
    config_menu_keyboard,
    live_confirm_keyboard,
    main_menu_keyboard,
    mode_menu_keyboard,
    report_period_keyboard,
)

if TYPE_CHECKING:
    from bayesmarket.data.state import MarketState
    from bayesmarket.runtime import RuntimeConfig

logger = structlog.get_logger()

# ─── helpers ──────────────────────────────────────────────────────────────────

def _format_status(state: "MarketState", rt: "RuntimeConfig") -> str:
    """Build status message string."""
    pos = state.position
    risk = state.risk

    # Scores
    lines = []
    from bayesmarket import config as _cfg
    network_tag = " | 🟠 TESTNET" if (rt.live_mode and _cfg.IS_TESTNET) else ""
    lines.append(f"{rt.network_label}  {rt.status_label}{network_tag}")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💰 Capital:  `${state.capital:,.2f}`")
    lines.append(f"📊 Daily PnL: `${risk.daily_pnl:+.2f}`")
    lines.append(f"🔄 Funding:  `{state.funding_rate*100:.4f}%/h` ({state.funding_tier})")
    lines.append(f"")

    if pos:
        from bayesmarket.engine.position import calculate_unrealized_pnl
        unrealized = calculate_unrealized_pnl(pos, state.mid_price)
        pnl_pct = unrealized / state.capital * 100
        side_e = "🟢" if pos.side == "long" else "🔴"
        lines.append(f"{side_e} *POSITION: {pos.side.upper()}*")
        lines.append(f"  Entry:  `${pos.entry_price:,.1f}`")
        lines.append(f"  Mid:    `${state.mid_price:,.1f}`")
        lines.append(f"  PnL:    `${unrealized:+.2f}` ({pnl_pct:+.2f}%)")
        lines.append(f"  SL:     `${pos.sl_price:,.1f}` [{pos.sl_basis}]")
        lines.append(f"  TP1:    `${pos.tp1_price:,.1f}` {'✅' if pos.tp1_hit else '⏳'}")
        lines.append(f"  TP2:    `${pos.tp2_price:,.1f}` ⏳")
        dur = time.time() - pos.entry_time
        lines.append(f"  Time:   `{int(dur//60)}m {int(dur%60)}s`")
    else:
        lines.append("📭 *No open position*")

    lines.append(f"")
    lines.append(f"📈 Trades today: `{risk.trades_today}` (W:{risk.consecutive_wins} L:{risk.consecutive_losses})")

    risk_label = "🟢 NORMAL"
    if risk.full_stop_active:
        risk_label = "🚨 FULL STOP"
    elif risk.daily_paused:
        risk_label = "⛔ DAILY PAUSED"
    elif risk.cooldown_active:
        risk_label = "⚠️ COOLDOWN"
    lines.append(f"🛡️ Risk:  {risk_label}")
    lines.append(f"📡 Klines: `{state.kline_source}`")

    return "\n".join(lines)


def _format_scores(state: "MarketState") -> str:
    role_labels = {"bias": "BIAS", "context": "CTX", "timing": "ZONE", "trigger": "TRIG"}
    lines = ["*📊 CASCADE SCORES*", "━━━━━━━━━━━━━━━━━━━━"]
    lines.append(f"Mid: `${state.mid_price:,.1f}`")
    lines.append(f"Cascade: `{state.cascade_allowed_direction}` | 1h: `{'✓' if state.cascade_context_confirmed else '✗'}`")
    lines.append("")

    for tf_name in ["4h", "1h", "15m", "5m"]:
        tf_cfg = config.TIMEFRAMES.get(tf_name, {})
        role_label = role_labels.get(tf_cfg.get("role", ""), "?")
        tf_state = state.tf_states.get(tf_name)
        snap = tf_state.signal if tf_state else None
        if snap:
            bar_len = int(min(abs(snap.total_score) / 13.5 * 8, 8))
            bar = "█" * bar_len + "░" * (8 - bar_len)
            sig = snap.signal
            if snap.cascade_blocked_reason:
                sig += f" [{snap.cascade_blocked_reason}]"
            elif snap.signal_blocked_reason:
                sig += f" [{snap.signal_blocked_reason}]"
            cascade_info = ""
            if tf_name == "4h":
                cascade_info = f"  Dir: `{snap.cascade_allowed_direction}`"
            elif tf_name == "1h":
                cascade_info = f"  Confirmed: `{'YES' if snap.cascade_context_confirmed else 'NO'}`"
            elif tf_name == "15m":
                zone = tf_state.active_zone_direction or "NONE"
                cascade_info = f"  Zone: `{zone}`"
            lines.append(
                f"*{tf_name}* [{role_label}] `{snap.total_score:+.1f}` {bar} `{sig}`{cascade_info}\n"
                f"  A:`{snap.category_a:+.1f}` B:`{snap.category_b:+.1f}` C:`{snap.category_c:+.1f}`\n"
            )
        else:
            lines.append(f"*{tf_name}* [{role_label}] warming up...")
    return "\n".join(lines)


def _format_report(db_path, period: str) -> str:
    """Generate report string dari SQLite."""
    period_map = {"1d": 86400, "7d": 604800, "30d": 2592000, "all": 0}
    secs = period_map.get(period, 86400)
    start_ts = time.time() - secs if secs > 0 else 0

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        row = conn.execute(
            """SELECT COUNT(*) as total,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
               SUM(pnl) as net_pnl,
               AVG(pnl) as avg_pnl,
               MIN(pnl) as worst,
               MAX(pnl) as best
            FROM trades WHERE entry_time >= ?""", (start_ts,)
        ).fetchone()

        total = row["total"] or 0
        if total == 0:
            conn.close()
            return f"📋 *Report {period.upper()}*\nBelum ada trade dalam periode ini."

        wins = row["wins"] or 0
        losses = total - wins
        wr = wins / total * 100
        net = row["net_pnl"] or 0

        pf_row = conn.execute(
            """SELECT SUM(CASE WHEN pnl>0 THEN pnl ELSE 0 END) as gp,
               ABS(SUM(CASE WHEN pnl<0 THEN pnl ELSE 0 END)) as gl
               FROM trades WHERE entry_time >= ?""", (start_ts,)
        ).fetchone()
        gp = pf_row["gp"] or 0
        gl = pf_row["gl"] or 0.001
        pf = gp / gl if gl > 0 else 999

        # By source
        sources = list(conn.execute(
            "SELECT merge_type, COUNT(*) as n, SUM(pnl) as total "
            "FROM trades WHERE entry_time >= ? GROUP BY merge_type", (start_ts,)
        ))

        dur_row = conn.execute(
            "SELECT AVG(exit_time - entry_time) as d FROM trades WHERE entry_time >= ?", (start_ts,)
        ).fetchone()
        avg_dur = dur_row["d"] or 0

        conn.close()

        lines = [
            f"📋 *REPORT — {period.upper()}*",
            f"━━━━━━━━━━━━━━━━━━━━",
            f"📊 Trades:     `{total}` ({wins}W / {losses}L)",
            f"🎯 Win Rate:   `{wr:.1f}%`",
            f"⚖️ Prof Factor: `{pf:.2f}`",
            f"💵 Net PnL:    `${net:+.2f}`",
            f"📈 Best:       `${row['best']:+.2f}`",
            f"📉 Worst:      `${row['worst']:+.2f}`",
            f"⏱️  Avg Duration: `{int(avg_dur//60)}m {int(avg_dur%60)}s`",
            f"",
            f"*By Source:*",
        ]
        for s in sources:
            lines.append(f"  `{s['merge_type']}`: {s['n']} trades | ${s['total']:+.2f}")

        return "\n".join(lines)
    except Exception as e:
        return f"❌ Error generating report: {e}"


def _format_config(rt: "RuntimeConfig") -> str:
    lines = [
        f"⚙️ *CONFIG AKTIF*",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"Mode:          `{rt.mode_label}`",
        f"Status:        `{rt.status_label}`",
        f"",
        f"*Scoring Thresholds:*",
        f"  5m (trigger):  `{rt.scoring_threshold_5m}`",
        f"",
        f"*Sensitivities:*",
        f"  VWAP: `{rt.vwap_sensitivity}`",
        f"  POC:  `{rt.poc_sensitivity}`",
        f"",
        f"*Alerts:*",
        f"  Entry:  `{'✅' if rt.alert_on_entry else '❌'}`",
        f"  Exit:   `{'✅' if rt.alert_on_exit else '❌'}`",
        f"  SL Hit: `{'✅' if rt.alert_on_sl_hit else '❌'}`",
        f"  TP:     `{'✅' if rt.alert_on_tp else '❌'}`",
        f"",
        f"Gunakan `/set <param> <value>` untuk ubah.",
        f"Contoh: `/set threshold_5m 6.5`",
    ]
    return "\n".join(lines)


# ─── command handlers ─────────────────────────────────────────────────────────

def build_handlers(state: "MarketState", rt: "RuntimeConfig") -> list:
    """Build list of handlers dengan closure ke state dan rt."""

    async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = (
            "🤖 *BayesMarket Control Panel*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Bot trading BTC otomatis di Hyperliquid.\n\n"
            f"Mode saat ini: {rt.mode_label}\n"
            f"Status: {rt.status_label}\n\n"
            "Gunakan menu di bawah untuk kontrol:"
        )
        await update.message.reply_text(
            msg, parse_mode="Markdown", reply_markup=main_menu_keyboard()
        )

    async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = _format_status(state, rt)
        kb = main_menu_keyboard()
        if update.message:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
        else:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown", reply_markup=kb)

    async def cmd_scores(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = _format_scores(state)
        if update.message:
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup_back())

    async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        period = ctx.args[0] if ctx.args else "1d"
        msg = _format_report(config.DB_PATH, period)
        if update.message:
            await update.message.reply_text(msg, parse_mode="Markdown",
                reply_markup=report_period_keyboard())

    async def cmd_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = (
            f"*MODE CONTROL*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Mode aktif: {rt.mode_label}\n"
            f"Switched: {time.strftime('%Y-%m-%d %H:%M', time.localtime(rt.mode_switched_at))}\n"
            f"By: `{rt.mode_switched_by}`\n"
            f"Total switches: `{rt.total_mode_switches}`"
        )
        if update.message:
            await update.message.reply_text(
                msg, parse_mode="Markdown", reply_markup=mode_menu_keyboard(rt.live_mode)
            )

    async def cmd_shadow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        result = rt.switch_to_shadow(by="telegram")
        await update.message.reply_text(result, parse_mode="Markdown")

    async def cmd_live(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = (
            "⚠️ *KONFIRMASI SWITCH KE LIVE MODE*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Order NYATA akan dieksekusi di Hyperliquid.\n"
            "Modal sungguhan akan digunakan.\n\n"
            "Pastikan:\n"
            "✅ HL_PRIVATE_KEY sudah diisi di .env\n"
            "✅ HL_ACCOUNT_ADDRESS sudah diisi di .env\n"
            "✅ Shadow mode sudah divalidasi\n\n"
            "Lanjutkan?"
        )
        await update.message.reply_text(
            msg, parse_mode="Markdown", reply_markup=live_confirm_keyboard()
        )

    async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        reason = " ".join(ctx.args) if ctx.args else "manual via Telegram"
        result = rt.pause_trading(reason=reason)
        await update.message.reply_text(result)

    async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        result = rt.resume_trading()
        await update.message.reply_text(result)

    async def cmd_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if state.position is None:
            await update.message.reply_text("📭 Tidak ada posisi aktif untuk ditutup.")
            return
        pos = state.position
        from bayesmarket.engine.position import calculate_unrealized_pnl
        unrealized = calculate_unrealized_pnl(pos, state.mid_price)
        msg = (
            f"⚠️ *FORCE CLOSE POSITION?*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Side:  `{pos.side.upper()}`\n"
            f"Entry: `${pos.entry_price:,.1f}`\n"
            f"Mid:   `${state.mid_price:,.1f}`\n"
            f"PnL:   `${unrealized:+.2f}`\n\n"
            f"Yakin ingin menutup posisi sekarang?"
        )
        await update.message.reply_text(
            msg, parse_mode="Markdown", reply_markup=close_position_keyboard()
        )

    async def cmd_config(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = _format_config(rt)
        if update.message:
            await update.message.reply_text(
                msg, parse_mode="Markdown", reply_markup=config_menu_keyboard()
            )

    async def cmd_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """
        Usage: /set <param> <value>
        Params: threshold_5m, bias_threshold, vwap_sensitivity, poc_sensitivity
        """
        if not ctx.args or len(ctx.args) < 2:
            await update.message.reply_text(
                "Usage: `/set <param> <value>`\n\n"
                "Params:\n"
                "  `threshold_5m` — trigger scoring threshold (default 7.0)\n"
                "  `bias_threshold` — 4h cascade bias threshold (default 3.0)\n"
                "  `vwap_sensitivity` — VWAP sensitivity (default 20.0)\n"
                "  `poc_sensitivity` — POC sensitivity (default 20.0)",
                parse_mode="Markdown"
            )
            return

        param = ctx.args[0].lower()
        try:
            val = float(ctx.args[1])
        except ValueError:
            await update.message.reply_text(f"❌ Value harus numerik: `{ctx.args[1]}`", parse_mode="Markdown")
            return

        param_map = {
            "threshold_5m": ("scoring_threshold_5m", 1.0, 15.0),
            "bias_threshold": ("bias_threshold", 1.0, 10.0),
            "vwap_sensitivity": ("vwap_sensitivity", 1.0, 500.0),
            "poc_sensitivity": ("poc_sensitivity", 1.0, 500.0),
        }

        if param not in param_map:
            await update.message.reply_text(
                f"❌ Parameter `{param}` tidak dikenal.", parse_mode="Markdown"
            )
            return

        attr, min_v, max_v = param_map[param]
        if not (min_v <= val <= max_v):
            await update.message.reply_text(
                f"❌ Value harus antara {min_v} dan {max_v}.", parse_mode="Markdown"
            )
            return

        old_val = getattr(rt, attr)
        setattr(rt, attr, val)
        logger.info("config_changed_via_telegram", param=attr, old=old_val, new=val)
        await update.message.reply_text(
            f"✅ `{param}` diubah: `{old_val}` → `{val}`", parse_mode="Markdown"
        )

    async def cmd_dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """/dashboard — pull mode: send current snapshot immediately."""
        from bayesmarket.telegram_bot.dashboard_push import (
            build_dashboard_text, toggle_push,
            reset_dashboard_message, PUSH_AUTO,
        )

        if ctx.args:
            arg = ctx.args[0].lower()
            if arg in ("on", "auto"):
                result = toggle_push(True)
                reset_dashboard_message()
                await update.message.reply_text(result, parse_mode="Markdown")
                return
            elif arg in ("off", "stop"):
                result = toggle_push(False)
                await update.message.reply_text(result, parse_mode="Markdown")
                return

        # Pull: send snapshot now
        text = build_dashboard_text(state, rt)
        from bayesmarket.telegram_bot.dashboard_push import PUSH_AUTO as _pa
        status = f"\n\n_Auto-push: {'ON ✅' if _pa else 'OFF ⏸️'}_"
        await update.message.reply_text(
            text + status,
            parse_mode="Markdown"
        )

    async def cmd_analysis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """/analysis — loss pattern summary from DB."""
        period = ctx.args[0] if ctx.args else "7d"
        period_map = {"1d": 86400, "7d": 604800, "30d": 2592000, "all": 0}
        secs = period_map.get(period, 604800)
        start_ts = time.time() - secs if secs > 0 else 0

        try:
            conn = sqlite3.connect(str(config.DB_PATH))
            conn.row_factory = sqlite3.Row

            losses = list(conn.execute(
                "SELECT * FROM trades WHERE pnl < 0 AND entry_time >= ? "
                "ORDER BY entry_time DESC",
                (start_ts,)
            ))

            if not losses:
                await update.message.reply_text(
                    f"📊 *Loss Analysis — {period.upper()}*\n"
                    "Tidak ada loss trade dalam periode ini. 🎉",
                    parse_mode="Markdown"
                )
                conn.close()
                return

            from collections import Counter
            cats = Counter(r["loss_category"] or "unknown" for r in losses)
            total_loss = sum(r["pnl"] for r in losses)
            avg_hold = sum((r["hold_minutes"] or 0) for r in losses) / len(losses)
            avg_rr = sum((r["rr_actual"] or 0) for r in losses) / len(losses)

            cat_labels = {
                "stale_poc_sl":       "POC SL Stale",
                "poor_rr_entry":      "RR Ratio Buruk",
                "trend_reversal":     "Trend Reversal",
                "time_overheld":      "Time Overheld",
                "choppy_market":      "Choppy Market",
                "mtf_misaligned_entry": "MTF Misaligned",
                "normal_sl":          "Normal SL",
                "unknown":            "Belum Diklasifikasi",
            }

            lines = [
                f"📊 *LOSS ANALYSIS — {period.upper()}*",
                "━━━━━━━━━━━━━━━━━━━━",
                f"Total loss trades: `{len(losses)}`",
                f"Total loss PnL:    `${total_loss:+.2f}`",
                f"Avg hold time:     `{avg_hold:.0f} menit`",
                f"Avg RR ratio:      `1:{avg_rr:.2f}`",
                "",
                "```",
                f"{'KATEGORI':<22}| N  | LOSS",
                f"{'-'*22}|-----|------",
            ]

            for cat, count in cats.most_common():
                cat_loss = sum(r["pnl"] for r in losses if r["loss_category"] == cat)
                label = cat_labels.get(cat, cat)[:22]
                lines.append(f"{label:<22}| {count:<3} | ${cat_loss:+.2f}")

            lines += [
                "```",
                "",
            ]

            latest = losses[0]
            if latest["loss_category"]:
                lines += [
                    f"*Loss terbaru ({latest['loss_category']}):*",
                    f"`{latest['loss_diagnosis'] or 'No diagnosis'}`",
                    "",
                    f"💡 `{latest['loss_recommendation'] or '–'}`",
                ]

            conn.close()
            await update.message.reply_text(
                "\n".join(lines), parse_mode="Markdown"
            )

        except Exception as exc:
            await update.message.reply_text(f"❌ Error: {exc}")

    async def cmd_setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Interactive setup wizard via Telegram (for Railway deployment)."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        mode_c = {"shadow": "🟡", "testnet": "🟠", "live": "🔴"}
        current_mode = "live" if rt.live_mode else "shadow"
        if rt.live_mode and config.IS_TESTNET:
            current_mode = "testnet"

        msg = (
            "⚙️ *BAYESMARKET SETUP WIZARD*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Current mode: {mode_c.get(current_mode, '⚪')} `{current_mode.upper()}`\n\n"
            "*Current Configuration:*\n"
            f"  Capital: `${state.capital:,.2f}`\n"
            f"  Trigger threshold (5m): `{rt.scoring_threshold_5m}`\n"
            f"  Bias threshold (4h): `{rt.bias_threshold}`\n"
            f"  VWAP sensitivity: `{rt.vwap_sensitivity}`\n"
            f"  POC sensitivity: `{rt.poc_sensitivity}`\n"
            f"  Max leverage: `{config.MAX_LEVERAGE}x`\n"
            f"  Risk/trade: `{config.MAX_RISK_PER_TRADE*100:.1f}%`\n"
            f"  Daily loss limit: `{config.DAILY_LOSS_LIMIT*100:.1f}%`\n"
            f"  Database: `{config.DB_PATH}`\n"
            f"  Telegram: `{'✅ Connected' if config.TELEGRAM_BOT_TOKEN else '❌ Not set'}`\n"
            f"  HL Wallet: `{'✅ Set' if config.HL_PRIVATE_KEY else '❌ Not set'}`\n\n"
            "Use the buttons below to adjust, or use `/set` for individual parameters.\n"
            "Changes via `/set` take effect immediately (no restart needed)."
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🟡 Shadow", callback_data="setup_shadow"),
                InlineKeyboardButton("🟠 Testnet", callback_data="setup_testnet"),
                InlineKeyboardButton("🔴 Live", callback_data="setup_live"),
            ],
            [
                InlineKeyboardButton("📊 Threshold ▲", callback_data="threshold_up"),
                InlineKeyboardButton("📊 Threshold ▼", callback_data="threshold_down"),
            ],
            [
                InlineKeyboardButton("📊 Bias ▲", callback_data="setup_bias_up"),
                InlineKeyboardButton("📊 Bias ▼", callback_data="setup_bias_down"),
            ],
            [
                InlineKeyboardButton("◀️ Main Menu", callback_data="main_menu"),
            ],
        ])
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)

    async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = (
            "📖 *BAYESMARKET COMMANDS*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "*/start* — Main menu\n"
            "*/status* — Status lengkap (posisi, PnL, risk)\n"
            "*/scores* — Score semua TF saat ini\n"
            "*/report [1d|7d|30d|all]* — Performance report\n"
            "*/setup* — Interactive setup wizard\n"
            "*/mode* — Lihat dan switch mode\n"
            "*/shadow* — Switch ke shadow mode\n"
            "*/live* — Switch ke live mode (butuh konfirmasi)\n"
            "*/pause [reason]* — Pause entry baru\n"
            "*/resume* — Resume trading\n"
            "*/close* — Force close posisi aktif\n"
            "*/dashboard* — Lihat live dashboard sekarang (pull)\n"
            "*/dashboard auto* — Aktifkan auto-push (update tiap 30s)\n"
            "*/dashboard off* — Matikan auto-push\n"
            "*/analysis [1d|7d|30d|all]* — Loss pattern analysis\n"
            "*/config* — Lihat config aktif\n"
            "*/set <param> <value>* — Ubah parameter\n"
            "*/help* — Daftar commands ini\n"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    # ─── Callback query handler ────────────────────────────────────────────────

    async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "status":
            msg = _format_status(state, rt)
            await query.edit_message_text(msg, parse_mode="Markdown",
                reply_markup=main_menu_keyboard())

        elif data == "scores":
            msg = _format_scores(state)
            await query.edit_message_text(msg, parse_mode="Markdown",
                reply_markup=_back_keyboard())

        elif data == "main_menu":
            msg = f"🤖 *BayesMarket Control Panel*\n{rt.mode_label} | {rt.status_label}"
            await query.edit_message_text(msg, parse_mode="Markdown",
                reply_markup=main_menu_keyboard())

        elif data == "mode_menu":
            msg = f"*MODE CONTROL*\nAktif: {rt.mode_label}"
            await query.edit_message_text(msg, parse_mode="Markdown",
                reply_markup=mode_menu_keyboard(rt.live_mode))

        elif data == "switch_shadow":
            result = rt.switch_to_shadow(by="telegram_button")
            await query.edit_message_text(result, parse_mode="Markdown",
                reply_markup=main_menu_keyboard())

        elif data == "switch_live_confirm":
            msg = (
                "⚠️ *KONFIRMASI LIVE MODE*\n"
                "Order NYATA di Hyperliquid akan aktif.\n"
                "Yakin?"
            )
            await query.edit_message_text(msg, parse_mode="Markdown",
                reply_markup=live_confirm_keyboard())

        elif data == "switch_live_execute":
            result = rt.switch_to_live(
                hl_key=config.HL_PRIVATE_KEY,
                hl_address=config.HL_ACCOUNT_ADDRESS,
                by="telegram_button",
            )
            await query.edit_message_text(result, parse_mode="Markdown",
                reply_markup=main_menu_keyboard())

        elif data.startswith("report_"):
            period = data.split("_")[1]
            msg = _format_report(config.DB_PATH, period)
            await query.edit_message_text(msg, parse_mode="Markdown",
                reply_markup=report_period_keyboard())

        elif data == "config":
            await query.edit_message_text(_format_config(rt), parse_mode="Markdown",
                reply_markup=config_menu_keyboard())

        elif data == "config_view":
            await query.edit_message_text(_format_config(rt), parse_mode="Markdown",
                reply_markup=config_menu_keyboard())

        elif data == "threshold_up":
            rt.scoring_threshold_5m = min(rt.scoring_threshold_5m + 0.5, 13.5)
            await query.edit_message_text(_format_config(rt), parse_mode="Markdown",
                reply_markup=config_menu_keyboard())

        elif data == "threshold_down":
            rt.scoring_threshold_5m = max(rt.scoring_threshold_5m - 0.5, 1.0)
            await query.edit_message_text(_format_config(rt), parse_mode="Markdown",
                reply_markup=config_menu_keyboard())

        elif data == "toggle_alerts":
            rt.alert_on_entry = not rt.alert_on_entry
            rt.alert_on_exit = not rt.alert_on_exit
            rt.alert_on_sl_hit = not rt.alert_on_sl_hit
            rt.alert_on_tp = not rt.alert_on_tp
            status = "ON" if rt.alert_on_entry else "OFF"
            await query.edit_message_text(
                f"🔔 Alerts toggled: `{status}`", parse_mode="Markdown",
                reply_markup=config_menu_keyboard()
            )

        elif data == "pause":
            result = rt.pause_trading(reason="manual via Telegram button")
            await query.edit_message_text(result, reply_markup=main_menu_keyboard())

        elif data == "resume":
            result = rt.resume_trading()
            await query.edit_message_text(result, reply_markup=main_menu_keyboard())

        elif data == "dashboard_pull":
            from bayesmarket.telegram_bot.dashboard_push import build_dashboard_text
            text = build_dashboard_text(state, rt)
            await query.edit_message_text(text, parse_mode="Markdown",
                reply_markup=_back_keyboard())

        elif data == "setup_shadow":
            result = rt.switch_to_shadow(by="telegram_setup")
            await query.edit_message_text(
                f"✅ Switched to SHADOW mode.\n\n{result}",
                parse_mode="Markdown", reply_markup=main_menu_keyboard()
            )

        elif data == "setup_testnet":
            if not config.HL_PRIVATE_KEY or not config.HL_ACCOUNT_ADDRESS:
                await query.edit_message_text(
                    "❌ *Cannot switch to testnet*\n\n"
                    "Set these env vars first:\n"
                    "```\n"
                    "HL_REST_URL=https://api.hyperliquid-testnet.xyz\n"
                    "HL_WS_URL=wss://api.hyperliquid-testnet.xyz/ws\n"
                    "HL_PRIVATE_KEY=<testnet API wallet key>\n"
                    "HL_ACCOUNT_ADDRESS=<testnet main wallet address>\n"
                    "```\n"
                    "Then restart the bot.",
                    parse_mode="Markdown", reply_markup=main_menu_keyboard()
                )
            elif not config.IS_TESTNET:
                await query.edit_message_text(
                    "⚠️ *HL_REST_URL points to mainnet*\n\n"
                    "Set `HL_REST_URL=https://api.hyperliquid-testnet.xyz` "
                    "and restart to use testnet.",
                    parse_mode="Markdown", reply_markup=main_menu_keyboard()
                )
            else:
                result = rt.switch_to_live(
                    hl_key=config.HL_PRIVATE_KEY,
                    hl_address=config.HL_ACCOUNT_ADDRESS,
                    by="telegram_setup",
                )
                await query.edit_message_text(
                    f"🟠 *TESTNET MODE ACTIVE*\n\n{result}",
                    parse_mode="Markdown", reply_markup=main_menu_keyboard()
                )

        elif data == "setup_live":
            # Same as existing live confirm flow
            msg = (
                "⚠️ *KONFIRMASI LIVE MODE*\n"
                "Order NYATA di Hyperliquid akan aktif.\n"
                "Yakin?"
            )
            await query.edit_message_text(msg, parse_mode="Markdown",
                reply_markup=live_confirm_keyboard())

        elif data == "setup_bias_up":
            rt.bias_threshold = min(rt.bias_threshold + 0.5, 10.0)
            await query.edit_message_text(
                f"📊 Bias threshold: `{rt.bias_threshold}`",
                parse_mode="Markdown", reply_markup=config_menu_keyboard()
            )

        elif data == "setup_bias_down":
            rt.bias_threshold = max(rt.bias_threshold - 0.5, 1.0)
            await query.edit_message_text(
                f"📊 Bias threshold: `{rt.bias_threshold}`",
                parse_mode="Markdown", reply_markup=config_menu_keyboard()
            )

        elif data == "force_close_execute":
            if state.position is not None:
                from bayesmarket.engine.position import calculate_pnl
                pos = state.position
                pnl = calculate_pnl(pos.side, pos.entry_price, state.mid_price, pos.remaining_size)
                pnl_pct = pnl / state.capital * 100
                # Mark for close — executor will handle on next cycle
                state.position._force_close = True
                await query.edit_message_text(
                    f"✅ Force close dieksekusi.\nPnL estimasi: `${pnl:+.2f}` ({pnl_pct:+.2f}%)",
                    parse_mode="Markdown", reply_markup=main_menu_keyboard()
                )
            else:
                await query.edit_message_text(
                    "📭 Tidak ada posisi.", reply_markup=main_menu_keyboard()
                )

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    def _back_keyboard():
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Kembali", callback_data="main_menu")
        ]])

    def InlineKeyboardMarkup_back():
        return _back_keyboard()

    return [
        CommandHandler("start", cmd_start),
        CommandHandler("status", cmd_status),
        CommandHandler("scores", cmd_scores),
        CommandHandler("report", cmd_report),
        CommandHandler("mode", cmd_mode),
        CommandHandler("shadow", cmd_shadow),
        CommandHandler("live", cmd_live),
        CommandHandler("pause", cmd_pause),
        CommandHandler("resume", cmd_resume),
        CommandHandler("close", cmd_close),
        CommandHandler("config", cmd_config),
        CommandHandler("set", cmd_set),
        CommandHandler("setup", cmd_setup),
        CommandHandler("dashboard", cmd_dashboard),
        CommandHandler("analysis", cmd_analysis),
        CommandHandler("help", cmd_help),
        CallbackQueryHandler(on_callback),
    ]
