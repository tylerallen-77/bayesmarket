"""Simple backtest framework (MOD-2).

Replays signals from the SQLite database and computes theoretical PnL.
Uses the same scoring thresholds and risk parameters as live engine.

Usage:
    python -m bayesmarket.backtest [--db bayesmarket.db] [--threshold 7.0]
"""

import argparse
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from bayesmarket import config

logger = structlog.get_logger()


@dataclass
class BacktestTrade:
    """A simulated trade from backtest replay."""
    entry_time: float
    exit_time: float = 0.0
    side: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    sl_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    pnl: float = 0.0
    exit_reason: str = ""
    entry_score: float = 0.0


@dataclass
class BacktestResult:
    """Aggregate backtest statistics."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    trades: list = field(default_factory=list)


def run_backtest(
    db_path: Path,
    threshold: float = 7.0,
    capital: float = 1000.0,
    risk_per_trade: float = 0.02,
    max_leverage: float = 5.0,
) -> BacktestResult:
    """Replay signals from DB and simulate trades.

    Strategy: enter on 5m signal when score crosses threshold,
    exit on SL/TP using ATR-based levels from recorded data.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Fetch 5m signals ordered by time
    rows = conn.execute(
        """SELECT timestamp, mid_price, total_score, atr_value, regime,
                  active_threshold, signal, blocked_reason,
                  cvd_score, obi_score, depth_score,
                  vwap_score, poc_score, ha_score,
                  rsi_score, macd_score, ema_score
           FROM signals
           WHERE timeframe = '5m' AND mid_price > 0
           ORDER BY timestamp ASC"""
    ).fetchall()
    conn.close()

    if not rows:
        logger.warning("backtest_no_data")
        return BacktestResult()

    result = BacktestResult()
    current_capital = capital
    peak_capital = capital
    position = None  # None or BacktestTrade

    for row in rows:
        ts = row["timestamp"]
        mid = row["mid_price"]
        score = row["total_score"]
        atr = row["atr_value"] if row["atr_value"] else mid * 0.005

        # If we have an open position, check SL/TP
        if position is not None:
            hit_sl = False
            hit_tp1 = False

            if position.side == "LONG":
                if mid <= position.sl_price:
                    hit_sl = True
                elif mid >= position.tp1_price:
                    hit_tp1 = True
            else:
                if mid >= position.sl_price:
                    hit_sl = True
                elif mid <= position.tp1_price:
                    hit_tp1 = True

            if hit_sl:
                position.exit_time = ts
                position.exit_price = position.sl_price
                position.exit_reason = "sl_hit"
                _finalize_trade(position, result, current_capital)
                current_capital += position.pnl
                peak_capital = max(peak_capital, current_capital)
                dd = (peak_capital - current_capital) / peak_capital * 100
                result.max_drawdown = max(result.max_drawdown, dd)
                position = None
                continue

            if hit_tp1:
                position.exit_time = ts
                position.exit_price = position.tp1_price
                position.exit_reason = "tp1_hit"
                _finalize_trade(position, result, current_capital)
                current_capital += position.pnl
                peak_capital = max(peak_capital, current_capital)
                position = None
                continue

            # Time exit: 30 minutes
            if ts - position.entry_time > config.TIME_EXIT_MINUTES_5M * 60:
                position.exit_time = ts
                position.exit_price = mid
                position.exit_reason = "time_exit"
                _finalize_trade(position, result, current_capital)
                current_capital += position.pnl
                peak_capital = max(peak_capital, current_capital)
                dd = (peak_capital - current_capital) / peak_capital * 100
                result.max_drawdown = max(result.max_drawdown, dd)
                position = None
                continue

            continue  # still in position, skip entry logic

        # No position — check for entry signal
        if score >= threshold:
            direction = "LONG"
        elif score <= -threshold:
            direction = "SHORT"
        else:
            continue

        # Compute SL/TP from ATR
        sl_dist = config.ATR_SL_MULTIPLIER * atr
        tp1_dist = config.TP1_FALLBACK_ATR_MULT * atr

        if direction == "LONG":
            sl = mid - sl_dist
            tp1 = mid + tp1_dist
        else:
            sl = mid + sl_dist
            tp1 = mid - tp1_dist

        # Position sizing
        risk_amount = current_capital * risk_per_trade
        if sl_dist <= 0:
            continue
        size = risk_amount / sl_dist
        max_size = current_capital * max_leverage / mid
        size = min(size, max_size)

        if size * mid < config.MIN_ORDER_VALUE_USD:
            continue

        position = BacktestTrade(
            entry_time=ts,
            side=direction,
            entry_price=mid,
            sl_price=sl,
            tp1_price=tp1,
            entry_score=score,
        )

    # Close any remaining position at last price
    if position is not None and rows:
        last_mid = rows[-1]["mid_price"]
        position.exit_time = rows[-1]["timestamp"]
        position.exit_price = last_mid
        position.exit_reason = "end_of_data"
        _finalize_trade(position, result, current_capital)
        current_capital += position.pnl

    # Compute aggregate stats
    result.total_pnl = current_capital - capital
    if result.total_trades > 0:
        result.win_rate = result.winning_trades / result.total_trades * 100

    wins = [t.pnl for t in result.trades if t.pnl > 0]
    losses = [t.pnl for t in result.trades if t.pnl < 0]
    result.avg_win = sum(wins) / len(wins) if wins else 0.0
    result.avg_loss = sum(losses) / len(losses) if losses else 0.0
    total_wins = sum(wins)
    total_losses = abs(sum(losses))
    result.profit_factor = total_wins / total_losses if total_losses > 0 else float("inf")

    return result


def _finalize_trade(
    trade: BacktestTrade,
    result: BacktestResult,
    capital: float,
) -> None:
    """Compute PnL and update result counters."""
    if trade.side == "LONG":
        trade.pnl = (trade.exit_price - trade.entry_price) / trade.entry_price * capital * 0.02
    else:
        trade.pnl = (trade.entry_price - trade.exit_price) / trade.entry_price * capital * 0.02

    result.total_trades += 1
    if trade.pnl > 0:
        result.winning_trades += 1
    else:
        result.losing_trades += 1
    result.trades.append(trade)


def print_report(result: BacktestResult) -> None:
    """Print backtest results to stdout."""
    print("\n" + "=" * 60)
    print("  BAYESMARKET BACKTEST REPORT")
    print("=" * 60)
    print(f"  Total Trades:    {result.total_trades}")
    print(f"  Winning:         {result.winning_trades}")
    print(f"  Losing:          {result.losing_trades}")
    print(f"  Win Rate:        {result.win_rate:.1f}%")
    print(f"  Total PnL:       ${result.total_pnl:.2f}")
    print(f"  Avg Win:         ${result.avg_win:.2f}")
    print(f"  Avg Loss:        ${result.avg_loss:.2f}")
    print(f"  Profit Factor:   {result.profit_factor:.2f}")
    print(f"  Max Drawdown:    {result.max_drawdown:.1f}%")
    print("=" * 60)

    if result.trades:
        print("\n  TRADE LOG (last 20):")
        print(f"  {'#':>3}  {'Side':<6} {'Entry':>10} {'Exit':>10} {'PnL':>8} {'Reason':<12}")
        print("  " + "-" * 56)
        for i, t in enumerate(result.trades[-20:], 1):
            print(
                f"  {i:>3}  {t.side:<6} {t.entry_price:>10.1f} "
                f"{t.exit_price:>10.1f} {t.pnl:>+8.2f} {t.exit_reason:<12}"
            )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="BayesMarket Backtest")
    parser.add_argument("--db", default="bayesmarket.db", help="Path to SQLite database")
    parser.add_argument("--threshold", type=float, default=7.0, help="Score threshold")
    parser.add_argument("--capital", type=float, default=1000.0, help="Starting capital")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return

    result = run_backtest(db_path, threshold=args.threshold, capital=args.capital)
    print_report(result)


if __name__ == "__main__":
    main()
