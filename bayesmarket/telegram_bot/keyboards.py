"""Inline keyboards untuk Telegram bot."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Status", callback_data="status"),
            InlineKeyboardButton("📈 Scores", callback_data="scores"),
        ],
        [
            InlineKeyboardButton("📋 Report 1D", callback_data="report_1d"),
            InlineKeyboardButton("📋 Report 7D", callback_data="report_7d"),
        ],
        [
            InlineKeyboardButton("⚙️ Config", callback_data="config"),
            InlineKeyboardButton("🔄 Mode Switch", callback_data="mode_menu"),
        ],
        [
            InlineKeyboardButton("⏸️ Pause Trading", callback_data="pause"),
            InlineKeyboardButton("▶️ Resume", callback_data="resume"),
        ],
        [
            InlineKeyboardButton("📊 Dashboard", callback_data="dashboard_pull"),
        ],
    ])


def mode_menu_keyboard(live_mode: bool) -> InlineKeyboardMarkup:
    if live_mode:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🟡 Switch ke SHADOW MODE", callback_data="switch_shadow")],
            [InlineKeyboardButton("◀️ Kembali", callback_data="main_menu")],
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔴 Switch ke LIVE MODE", callback_data="switch_live_confirm")],
            [InlineKeyboardButton("◀️ Kembali", callback_data="main_menu")],
        ])


def live_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ YA, Switch ke LIVE", callback_data="switch_live_execute"),
            InlineKeyboardButton("❌ Batal", callback_data="main_menu"),
        ],
    ])


def report_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1 Hari", callback_data="report_1d"),
            InlineKeyboardButton("7 Hari", callback_data="report_7d"),
            InlineKeyboardButton("30 Hari", callback_data="report_30d"),
        ],
        [
            InlineKeyboardButton("Semua", callback_data="report_all"),
            InlineKeyboardButton("◀️ Kembali", callback_data="main_menu"),
        ],
    ])


def config_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Lihat Config Aktif", callback_data="config_view")],
        [
            InlineKeyboardButton("⬆️ Threshold +0.5", callback_data="threshold_up"),
            InlineKeyboardButton("⬇️ Threshold -0.5", callback_data="threshold_down"),
        ],
        [
            InlineKeyboardButton("⚡ Leverage ▲", callback_data="setup_leverage_up"),
            InlineKeyboardButton("⚡ Leverage ▼", callback_data="setup_leverage_down"),
        ],
        [
            InlineKeyboardButton("🎯 Risk ▲", callback_data="setup_risk_up"),
            InlineKeyboardButton("🎯 Risk ▼", callback_data="setup_risk_down"),
        ],
        [
            InlineKeyboardButton("📈 TP1% ▲", callback_data="setup_tp1_up"),
            InlineKeyboardButton("📈 TP1% ▼", callback_data="setup_tp1_down"),
        ],
        [
            InlineKeyboardButton("🔀 Trailing", callback_data="setup_toggle_trailing"),
            InlineKeyboardButton("🔀 Adaptive TP", callback_data="setup_toggle_adaptive"),
        ],
        [
            InlineKeyboardButton("🔔 Toggle Alerts", callback_data="toggle_alerts"),
        ],
        [InlineKeyboardButton("◀️ Kembali", callback_data="main_menu")],
    ])


def close_position_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ YA, Close Sekarang", callback_data="force_close_execute"),
            InlineKeyboardButton("❌ Batal", callback_data="status"),
        ],
    ])
