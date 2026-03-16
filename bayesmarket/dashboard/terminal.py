"""Full Rich terminal dashboard — uniform 4-panel layout.

Semua 4 TF menampilkan informasi yang sama:
  - Score bar + Signal
  - ORDER BOOK: OBI, Depth, Walls
  - FLOW: CVD, POC, VWAP
  - TECHNICAL: RSI, MACD, EMA, HA
  - REGIME + MTF

Bottom bar: position, PnL, risk state, funding, source TFs.
"""

import asyncio
import time

from rich.box import SIMPLE, MINIMAL, ROUNDED
from rich.columns import Columns
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


# ── Visual helpers ─────────────────────────────────────────────────────────────

def _score_bar(score: float, width: int = 12) -> str:
    """Bilateral score bar from -13.5 to +13.5."""
    max_val = 13.5
    ratio = min(abs(score) / max_val, 1.0)
    filled = int(ratio * width)
    empty = width - filled
    bar = "█" * filled + "░" * empty
    prefix = "+" if score >= 0 else ""
    return f"{prefix}{score:5.1f} │{bar}│"


def _score_color(score: float) -> str:
    if score >= 9.0:
        return "bold bright_green"
    elif score >= 6.0:
        return "bold green"
    elif score >= 3.0:
        return "green"
    elif score <= -9.0:
        return "bold bright_red"
    elif score <= -6.0:
        return "bold red"
    elif score <= -3.0:
        return "red"
    return "white"


def _signal_style(signal: str, blocked: bool) -> tuple[str, str]:
    """Returns (label, color)."""
    if blocked:
        return f"◌ {signal}", "dim yellow"
    if signal == "LONG":
        return "▲ LONG", "bold bright_green"
    if signal == "SHORT":
        return "▼ SHORT", "bold bright_red"
    return "─ NEUTRAL", "dim white"


def _mini_ha_bar(klines, count: int = 6) -> str:
    """Compact HA candle string."""
    if not klines or len(klines) < 3:
        return "---"
    candles = list(klines)
    ha_candles = []
    for i, k in enumerate(candles):
        hc = (k.open + k.high + k.low + k.close) / 4
        ho = (k.open + k.close) / 2 if i == 0 else (ha_candles[-1][0] + ha_candles[-1][1]) / 2
        ha_candles.append((ho, hc))
    return " ".join(
        "▲" if hc >= ho else "▼"
        for ho, hc in ha_candles[-count:]
    )


def _wall_str(state: MarketState, side: str) -> str:
    walls = [w for w in state.tracked_walls if w.side == side and w.is_valid]
    if not walls:
        return "none"
    best = max(walls, key=lambda w: w.total_size)
    age = int(best.age_seconds)
    decay = f" {best.size_ratio*100:.0f}%"
    return f"${best.bin_center:,.0f} ({best.total_size:.2f}){decay} {age}s"


# ── Uniform TF panel builder ───────────────────────────────────────────────────

def _build_tf_panel(state: MarketState, tf_name: str) -> Panel:
    """Build uniform panel — same layout for all 4 TFs."""
    tf_state = state.tf_states.get(tf_name)
    snap: SignalSnapshot = tf_state.signal if tf_state else None
    tf_cfg = config.TIMEFRAMES.get(tf_name, {})
    role = tf_cfg.get("role", "execution")
    mtf_tf = tf_cfg.get("mtf_filter_tf")

    table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    table.add_column("label", style="dim", width=13)
    table.add_column("value", no_wrap=False)

    price_str = f"${state.mid_price:,.1f}" if state.mid_price > 0 else "─"

    if not snap:
        table.add_row("", "[dim]warming up...[/dim]")
        border = "dim"
        title = f"[dim]BTC {tf_name}[/dim]"
        return Panel(table, title=title, border_style=border)

    # ── SCORE ──────────────────────────────────────────────────────────────────
    score_text = Text(_score_bar(snap.total_score))
    score_text.stylize(_score_color(snap.total_score))
    table.add_row("[bold]Score[/bold]", score_text)

    # ── SIGNAL ────────────────────────────────────────────────────────────────
    blocked = bool(snap.signal_blocked_reason)
    sig_label, sig_color = _signal_style(snap.signal, blocked)
    sig_text = Text(sig_label)
    sig_text.stylize(sig_color)
    if blocked:
        sig_text.append(f"  [{snap.signal_blocked_reason}]", style="dim yellow")
    table.add_row("[bold]Signal[/bold]", sig_text)

    # ── REGIME ────────────────────────────────────────────────────────────────
    regime_color = "yellow" if snap.regime == "ranging" else "cyan"
    regime_str = f"[{regime_color}]{snap.regime.upper()}[/{regime_color}]  ATR%: {snap.atr_percentile:.0f}"
    table.add_row("Regime", regime_str)

    # ── MTF ───────────────────────────────────────────────────────────────────
    if mtf_tf:
        mtf_state = state.tf_states.get(mtf_tf)
        mtf_snap = mtf_state.signal if mtf_state else None
        if mtf_snap and mtf_snap.vwap_value:
            price_vs = state.mid_price
            aligned_long = price_vs > mtf_snap.vwap_value
            aligned_short = price_vs < mtf_snap.vwap_value
            align_label = (
                "[green]▲ LONG OK[/green]" if aligned_long
                else "[red]▼ SHORT OK[/red]"
            )
            table.add_row(f"MTF({mtf_tf})", f"${mtf_snap.vwap_value:,.0f}  {align_label}")
        else:
            table.add_row(f"MTF({mtf_tf})", "[dim]loading...[/dim]")
    else:
        table.add_row(f"Role", f"[cyan]FILTER[/cyan]  (no MTF gate)")

    # ── CATEGORY BREAKDOWN ────────────────────────────────────────────────────
    cat_str = (
        f"A:[bold]{snap.category_a:+.1f}[/bold]"
        f"  B:[bold]{snap.category_b:+.1f}[/bold]"
        f"  C:[bold]{snap.category_c:+.1f}[/bold]"
    )
    table.add_row("A/B/C", cat_str)

    table.add_row("", "")

    # ── ORDER BOOK ────────────────────────────────────────────────────────────
    table.add_row("[bold]ORDER BOOK[/bold]", "")

    obi_color = "green" if snap.obi_raw > 0.05 else ("red" if snap.obi_raw < -0.05 else "white")
    table.add_row(
        "OBI",
        f"[{obi_color}]{snap.obi_raw*100:+.1f}%[/{obi_color}]  ({snap.obi_score:+.2f})"
    )
    depth_color = "green" if snap.depth_score > 0 else "red"
    table.add_row(
        "Depth",
        f"[{depth_color}]{snap.depth_score:+.2f}[/{depth_color}]"
    )

    bid_wall = _wall_str(state, "bid")
    ask_wall = _wall_str(state, "ask")
    wall_bid_color = "green" if bid_wall != "none" else "dim"
    wall_ask_color = "red" if ask_wall != "none" else "dim"
    table.add_row(
        "Bid Wall",
        f"[{wall_bid_color}]{bid_wall}[/{wall_bid_color}]"
    )
    table.add_row(
        "Ask Wall",
        f"[{wall_ask_color}]{ask_wall}[/{wall_ask_color}]"
    )

    table.add_row("", "")

    # ── FLOW ──────────────────────────────────────────────────────────────────
    table.add_row("[bold]FLOW[/bold]", "")

    cvd_color = "green" if snap.cvd_zscore_raw > 0 else "red"
    table.add_row(
        "CVD Z",
        f"[{cvd_color}]{snap.cvd_zscore_raw:+.1f}σ[/{cvd_color}]  ({snap.cvd_score:+.2f})"
    )
    table.add_row(
        "VWAP",
        f"${snap.vwap_value:,.0f}  ({snap.vwap_score:+.2f})" if snap.vwap_value else "---"
    )
    table.add_row(
        "POC",
        f"${snap.poc_value:,.0f}  ({snap.poc_score:+.2f})" if snap.poc_value else "---"
    )

    table.add_row("", "")

    # ── TECHNICAL ─────────────────────────────────────────────────────────────
    table.add_row("[bold]TECHNICAL[/bold]", "")

    rsi_val = snap.rsi_value or 0
    rsi_color = (
        "green" if rsi_val <= 35 else
        "red" if rsi_val >= 65 else "white"
    )
    rsi_str = f"[{rsi_color}]{rsi_val:.1f}[/{rsi_color}]  ({snap.rsi_score:+.2f})" if snap.rsi_value else "---"
    table.add_row("RSI(14)", rsi_str)
    table.add_row("MACD", f"{snap.macd_score:+.2f}")

    ema_str = "---"
    if snap.ema_short and snap.ema_long:
        rel = ">" if snap.ema_short > snap.ema_long else "<"
        ema_color = "green" if snap.ema_short > snap.ema_long else "red"
        ema_str = f"[{ema_color}]5{rel}20[/{ema_color}]  ({snap.ema_score:+.2f})"
    table.add_row("EMA", ema_str)

    ha_str = _mini_ha_bar(tf_state.klines if tf_state else [])
    table.add_row("HA", ha_str)

    # ── PANEL TITLE & BORDER ──────────────────────────────────────────────────
    title_color = _score_color(snap.total_score)
    role_tag = "[dim][EXEC][/dim]" if role == "execution" else "[dim][FILT][/dim]"
    title = f"[{title_color}]BTC {tf_name}[/{title_color}] {role_tag} {price_str}"

    using_fb = tf_state.using_fallback if tf_state else False
    border = "dim red" if using_fb else title_color

    return Panel(table, title=title, border_style=border)


# ── Bottom status bar ─────────────────────────────────────────────────────────

def _build_status_bar(state: MarketState) -> Panel:
    table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    table.add_column("col1", ratio=2)
    table.add_column("col2", ratio=2)
    table.add_column("col3", ratio=1)

    pos = state.position

    # Col 1: Position
    if pos:
        unrealized = calculate_unrealized_pnl(pos, state.mid_price)
        pnl_pct = unrealized / state.capital * 100 if state.capital > 0 else 0
        dur = time.time() - pos.entry_time
        pnl_color = "green" if unrealized >= 0 else "red"
        side_e = "▲" if pos.side == "long" else "▼"
        pos_str = (
            f"[bold]{side_e} {pos.side.upper()} {pos.remaining_size:.4f}BTC[/bold]\n"
            f"Entry [bold]${pos.entry_price:,.1f}[/bold] → Mid [bold]${state.mid_price:,.1f}[/bold]\n"
            f"SL [bold]${pos.sl_price:,.1f}[/bold] [{pos.sl_basis}]  "
            f"TP1 [bold]${pos.tp1_price:,.1f}[/bold] {'✓' if pos.tp1_hit else '○'}  "
            f"TP2 [bold]${pos.tp2_price:,.1f}[/bold] ○\n"
            f"PnL [{pnl_color}][bold]${unrealized:+.2f}[/bold] ({pnl_pct:+.2f}%)[/{pnl_color}]  "
            f"[dim]{int(dur//60)}m {int(dur%60)}s open[/dim]"
        )
        src_str = f"src: [bold]{'+'.join(pos.source_tfs)}[/bold]"
    else:
        pos_str = "[dim]── No open position ──[/dim]"
        src_str = ""

    # Col 2: System
    risk = state.risk
    risk_label = "[green]● NORMAL[/green]"
    if risk.full_stop_active:
        risk_label = "[bold red]● FULL STOP[/bold red]"
    elif risk.daily_paused:
        risk_label = "[red]● DAILY PAUSED[/red]"
    elif risk.cooldown_active:
        risk_label = "[yellow]● COOLDOWN[/yellow]"

    fund_color = "green" if state.funding_tier == "safe" else ("yellow" if state.funding_tier == "caution" else "red")
    regime_str = "---"
    tf_5m = state.tf_states.get("5m")
    if tf_5m and tf_5m.signal:
        regime_str = tf_5m.signal.regime.upper()

    sys_str = (
        f"Risk: {risk_label}\n"
        f"Daily PnL: [bold]${risk.daily_pnl:+.2f}[/bold]  Capital: [bold]${state.capital:,.2f}[/bold]\n"
        f"Funding: [{fund_color}]{state.funding_rate*100:.4f}%/h[/{fund_color}] ({state.funding_tier})\n"
        f"Regime: [cyan]{regime_str}[/cyan]  Trades: {risk.trades_today}  "
        f"W:[green]{risk.consecutive_wins}[/green] L:[red]{risk.consecutive_losses}[/red]"
    )

    # Col 3: Mode
    from bayesmarket import config as _cfg
    try:
        rt = state.runtime
        if not rt or not rt.live_mode:
            mode_color, mode_label = "yellow", "SHADOW"
        elif _cfg.IS_TESTNET:
            mode_color, mode_label = "dark_orange", "TESTNET"
        else:
            mode_color, mode_label = "red", "LIVE"
        paused_label = "\n[yellow]⏸ PAUSED[/yellow]" if (rt and rt.trading_paused) else ""
    except AttributeError:
        mode_color = "yellow"
        mode_label = "SHADOW"
        paused_label = ""

    kline_color = "yellow" if "binance" in state.kline_source else "green"
    mode_str = (
        f"[{mode_color}][bold]● {mode_label}[/bold][/{mode_color}]{paused_label}\n"
        f"Klines: [{kline_color}]{state.kline_source}[/{kline_color}]\n"
        f"{src_str}\n"
        f"[dim]{time.strftime('%H:%M:%S')}[/dim]"
    )

    table.add_row(pos_str, sys_str, mode_str)
    return Panel(table, title="[bold]STATUS[/bold]", border_style="blue")


# ── Layout builder ────────────────────────────────────────────────────────────

def build_layout(state: MarketState) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="panels", ratio=5),
        Layout(name="status", ratio=2),
    )
    layout["panels"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="right", ratio=1),
    )
    layout["left"].split_column(
        Layout(_build_tf_panel(state, "5m"), name="5m"),
        Layout(_build_tf_panel(state, "1h"), name="1h"),
    )
    layout["right"].split_column(
        Layout(_build_tf_panel(state, "15m"), name="15m"),
        Layout(_build_tf_panel(state, "4h"), name="4h"),
    )
    layout["status"].update(_build_status_bar(state))
    return layout


# ── Main dashboard loop ───────────────────────────────────────────────────────

async def dashboard_loop(state: MarketState) -> None:
    logger.info("dashboard_started")
    with Live(
        build_layout(state),
        console=console,
        refresh_per_second=1,
        screen=True,
    ) as live:
        while True:
            try:
                live.update(build_layout(state))
            except Exception as exc:
                logger.error("dashboard_render_error", error=str(exc))
            await asyncio.sleep(config.DASHBOARD_REFRESH_SECONDS)
