"""Full Rich terminal dashboard — 4-panel split screen.

Blueprint Section 11: top-left=5m, top-right=15m, bottom-left=1h, bottom-right=4h.
Bottom bar: position, PnL, risk state, funding, regime, source TFs.
"""

import asyncio
import time

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import structlog

from bayesmarket import config
from bayesmarket.data.state import MarketState, SignalSnapshot
from bayesmarket.engine.position import calculate_unrealized_pnl

logger = structlog.get_logger()

console = Console()


def _score_bar(score: float, max_val: float = 13.5, width: int = 10) -> str:
    """Create a visual score bar."""
    ratio = min(abs(score) / max_val, 1.0)
    filled = int(ratio * width)
    empty = width - filled
    prefix = "+" if score >= 0 else ""
    bar = "\u2588" * filled + "\u2591" * empty
    return f"{prefix}{score:5.1f} {bar}"


def _color_score(score: float) -> str:
    """Return color name based on score value."""
    if score >= 7.0:
        return "bold green"
    elif score >= 3.0:
        return "green"
    elif score <= -7.0:
        return "bold red"
    elif score <= -3.0:
        return "red"
    return "white"


def _build_exec_panel(state: MarketState, tf_name: str) -> Panel:
    """Build panel for an execution TF (5m, 15m)."""
    tf_state = state.tf_states.get(tf_name)
    snap = tf_state.signal if tf_state else None

    table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    table.add_column("label", width=16)
    table.add_column("value")

    price_str = f"${state.mid_price:,.1f}" if state.mid_price > 0 else "---"

    if snap:
        score_str = _score_bar(snap.total_score)
        signal_str = snap.signal
        if snap.signal_blocked_reason:
            signal_str += f" (blocked: {snap.signal_blocked_reason})"

        # MTF info
        mtf_tf = config.TIMEFRAMES[tf_name]["mtf_filter_tf"]
        mtf_str = "---"
        if snap.mtf_vwap and snap.mtf_vwap > 0:
            aligned = snap.mtf_aligned_long or snap.mtf_aligned_short
            mtf_str = f"${snap.mtf_vwap:,.0f} {'ALIGNED' if aligned else 'BLOCKED'}"

        table.add_row("Score:", score_str)
        table.add_row("Signal:", signal_str)
        table.add_row(f"MTF ({mtf_tf}):", mtf_str)
        table.add_row("", "")

        # Order Book section
        table.add_row("[bold]ORDER BOOK[/]", "")
        table.add_row("OBI:", f"{snap.obi_raw*100:+.1f}% ({snap.obi_score:+.2f})")
        table.add_row("Depth:", f"{snap.depth_score:+.2f}")

        # Walls
        bid_walls = [w for w in state.tracked_walls if w.side == "bid" and w.is_valid]
        ask_walls = [w for w in state.tracked_walls if w.side == "ask" and w.is_valid]
        wall_str = ""
        if bid_walls:
            best = max(bid_walls, key=lambda w: w.total_size)
            wall_str += f"Buy: ${best.bin_center:,.0f} ({best.total_size:.1f})"
        if ask_walls:
            best = max(ask_walls, key=lambda w: w.total_size)
            if wall_str:
                wall_str += " | "
            wall_str += f"Sell: ${best.bin_center:,.0f} ({best.total_size:.1f})"
        table.add_row("Walls:", wall_str or "none")
        table.add_row("", "")

        # Flow section
        table.add_row("[bold]FLOW[/]", "")
        table.add_row("CVD Z:", f"{snap.cvd_zscore_raw:+.1f}\u03c3 ({snap.cvd_score:+.2f})")
        table.add_row("POC:", f"${snap.poc_value:,.0f}" if snap.poc_value else "---")
        table.add_row("", "")

        # Technical section
        table.add_row("[bold]TECHNICAL[/]", "")
        rsi_str = f"{snap.rsi_value:.1f} ({snap.rsi_score:+.2f})" if snap.rsi_value else "---"
        table.add_row("RSI(14):", rsi_str)
        table.add_row("MACD:", f"{snap.macd_score:+.2f}")
        ema_str = ""
        if snap.ema_short and snap.ema_long:
            rel = ">" if snap.ema_short > snap.ema_long else "<"
            ema_str = f"5{rel}20 ({snap.ema_score:+.2f})"
        table.add_row("EMA:", ema_str or "---")
        table.add_row("VWAP:", f"${snap.vwap_value:,.0f} ({snap.vwap_score:+.2f})" if snap.vwap_value else "---")

        # HA candles
        ha_chars = []
        for k in list(tf_state.klines)[-config.HA_DISPLAY_COUNT:]:
            ha_chars.append("\u25b2" if k.close >= k.open else "\u25bc")
        table.add_row("HA:", " ".join(ha_chars) if ha_chars else "---")
    else:
        table.add_row("Score:", "warming up...")
        table.add_row("Signal:", "---")

    title_color = _color_score(snap.total_score if snap else 0)
    title = f"BTC {tf_name} | {price_str}"

    return Panel(table, title=title, border_style=title_color)


def _build_filter_panel(state: MarketState, tf_name: str) -> Panel:
    """Build compact panel for a filter TF (1h, 4h)."""
    tf_state = state.tf_states.get(tf_name)
    snap = tf_state.signal if tf_state else None

    table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    table.add_column("label", width=16)
    table.add_column("value")

    if snap:
        table.add_row("Score:", _score_bar(snap.total_score))
        table.add_row("[bold]VWAP:[/]", f"${snap.vwap_value:,.0f}" if snap.vwap_value else "---")
        table.add_row("Regime:", f"{snap.regime.upper()} (ATR pct: {snap.atr_percentile:.0f}%)")
        table.add_row("Cat A/B/C:", f"{snap.category_a:+.1f} / {snap.category_b:+.1f} / {snap.category_c:+.1f}")
        table.add_row("RSI:", f"{snap.rsi_value:.1f}" if snap.rsi_value else "---")
    else:
        table.add_row("Score:", "warming up...")
        table.add_row("VWAP:", "---")

    return Panel(table, title=f"BTC {tf_name} | FILTER TF", border_style="dim")


def _build_bottom_bar(state: MarketState) -> Panel:
    """Build the bottom status bar with position, PnL, risk, funding."""
    lines: list[str] = []

    # Position info
    pos = state.position
    if pos:
        unrealized = calculate_unrealized_pnl(pos, state.mid_price)
        unrealized_pct = unrealized / state.capital * 100 if state.capital > 0 else 0
        pos_str = (
            f"POSITION: {pos.side.upper()} {pos.remaining_size:.4f} BTC "
            f"@ ${pos.entry_price:,.1f} | "
            f"SL: ${pos.sl_price:,.1f} ({pos.sl_basis})"
        )
        lines.append(pos_str)

        tp_str = (
            f"TP1: ${pos.tp1_price:,.1f} "
            f"[{'HIT' if pos.tp1_hit else '60%'}]  "
            f"TP2: ${pos.tp2_price:,.1f} [40%]"
        )
        lines.append(tp_str)

        pnl_str = (
            f"PnL: {'+' if unrealized >= 0 else ''}"
            f"${unrealized:.2f} ({unrealized_pct:+.2f}%) | "
            f"Daily: ${state.risk.daily_pnl:+.2f}"
        )
        lines.append(pnl_str)
    else:
        lines.append("POSITION: None")
        lines.append(f"Daily PnL: ${state.risk.daily_pnl:+.2f} | Capital: ${state.capital:,.2f}")

    # Risk state
    risk = state.risk
    risk_label = "NORMAL"
    if risk.full_stop_active:
        risk_label = "FULL STOP"
    elif risk.daily_paused:
        risk_label = "DAILY PAUSED"
    elif risk.cooldown_active:
        risk_label = "COOLDOWN"

    # Regime from 5m
    regime = "---"
    tf_5m = state.tf_states.get("5m")
    if tf_5m and tf_5m.signal:
        regime = tf_5m.signal.regime.upper()

    source = "+".join(pos.source_tfs) if pos else "---"
    status_line = (
        f"Risk: {risk_label} | "
        f"Funding: {state.funding_rate*100:.4f}%/h ({state.funding_tier}) | "
        f"Regime: {regime} | "
        f"Source: {source} | "
        f"Trades: {risk.trades_today} | "
        f"W:{risk.consecutive_wins} L:{risk.consecutive_losses}"
    )
    lines.append(status_line)

    # Kline source
    lines.append(f"Klines: {state.kline_source} | Mode: {'LIVE' if config.LIVE_MODE else 'SHADOW'}")

    return Panel("\n".join(lines), title="STATUS", border_style="blue")


def build_layout(state: MarketState) -> Layout:
    """Build the complete 4-panel dashboard layout."""
    layout = Layout()

    layout.split_column(
        Layout(name="top", ratio=3),
        Layout(name="bottom_panels", ratio=2),
        Layout(name="status", ratio=2),
    )

    layout["top"].split_row(
        Layout(_build_exec_panel(state, "5m"), name="5m"),
        Layout(_build_exec_panel(state, "15m"), name="15m"),
    )

    layout["bottom_panels"].split_row(
        Layout(_build_filter_panel(state, "1h"), name="1h"),
        Layout(_build_filter_panel(state, "4h"), name="4h"),
    )

    layout["status"].update(_build_bottom_bar(state))

    return layout


async def dashboard_loop(state: MarketState) -> None:
    """Render the dashboard every 3 seconds using Rich Live."""
    logger.info("dashboard_started")

    with Live(build_layout(state), console=console, refresh_per_second=1, screen=True) as live:
        while True:
            try:
                live.update(build_layout(state))
            except Exception as exc:
                logger.error("dashboard_render_error", error=str(exc))

            await asyncio.sleep(config.DASHBOARD_REFRESH_SECONDS)
