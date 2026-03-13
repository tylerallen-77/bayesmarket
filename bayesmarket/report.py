"""CLI performance report tool (errata Patch #6).

Usage:
    python -m bayesmarket.report                 # Today's summary
    python -m bayesmarket.report --period 7d     # Last 7 days
    python -m bayesmarket.report --period all    # All time
    python -m bayesmarket.report --detail        # Show every trade
"""

import argparse
import sqlite3
import time
from pathlib import Path

from bayesmarket import config


def _get_period_start(period: str) -> float:
    """Convert period string to Unix timestamp."""
    if period == "all":
        return 0.0
    elif period.endswith("d"):
        days = int(period[:-1])
        return time.time() - days * 86400
    elif period.endswith("h"):
        hours = int(period[:-1])
        return time.time() - hours * 3600
    else:
        return time.time() - 86400  # Default: 1 day


def run_report(db_path: Path, period: str = "1d", detail: bool = False, signals: bool = False) -> None:
    """Generate and print performance report."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    start_ts = _get_period_start(period)

    print()
    print("=" * 56)
    print(f"  BAYESMARKET PERFORMANCE REPORT — {period.upper()}")
    print("=" * 56)
    print()

    # Summary
    row = conn.execute(
        """SELECT
            COUNT(*) as total,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
            SUM(pnl) as net_pnl,
            AVG(pnl) as avg_pnl,
            AVG(pnl_pct) as avg_pnl_pct,
            MIN(pnl) as worst_trade,
            MAX(pnl) as best_trade
        FROM trades WHERE entry_time >= ?""",
        (start_ts,),
    ).fetchone()

    total = row["total"] or 0
    wins = row["wins"] or 0
    losses = row["losses"] or 0

    print("  SUMMARY")
    print(f"    Total trades:        {total}")

    if total == 0:
        print("    No trades in this period.")
        print()
        _print_signal_stats(conn, start_ts, signals)
        conn.close()
        return

    win_rate = wins / total * 100 if total > 0 else 0
    net_pnl = row["net_pnl"] or 0
    avg_pnl = row["avg_pnl"] or 0

    print(f"    Win rate:            {win_rate:.1f}% ({wins}W / {losses}L)")

    # Profit factor
    pf_row = conn.execute(
        """SELECT
            SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_profit,
            ABS(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END)) as gross_loss
        FROM trades WHERE entry_time >= ?""",
        (start_ts,),
    ).fetchone()

    gross_profit = pf_row["gross_profit"] or 0
    gross_loss = pf_row["gross_loss"] or 0.001
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    print(f"    Profit factor:       {profit_factor:.2f}")
    print(f"    Net PnL:             ${net_pnl:+.2f}")
    print(f"    Avg PnL per trade:   ${avg_pnl:+.2f}")
    print(f"    Best trade:          ${row['best_trade']:+.2f}")
    print(f"    Worst trade:         ${row['worst_trade']:+.2f}")

    # Average duration
    dur_row = conn.execute(
        "SELECT AVG(exit_time - entry_time) as avg_dur FROM trades WHERE entry_time >= ? AND exit_time IS NOT NULL",
        (start_ts,),
    ).fetchone()
    avg_dur = dur_row["avg_dur"] or 0
    mins = int(avg_dur // 60)
    secs = int(avg_dur % 60)
    print(f"    Avg duration:        {mins}m {secs}s")
    print()

    # By source
    print("  BY SOURCE")
    for row in conn.execute(
        "SELECT merge_type, COUNT(*) as cnt, AVG(pnl) as avg, SUM(pnl) as total_pnl "
        "FROM trades WHERE entry_time >= ? GROUP BY merge_type",
        (start_ts,),
    ):
        src_wins = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE entry_time >= ? AND merge_type = ? AND pnl > 0",
            (start_ts, row["merge_type"]),
        ).fetchone()[0]
        src_wr = src_wins / row["cnt"] * 100 if row["cnt"] > 0 else 0
        print(f"    {row['merge_type']:15s} {row['cnt']:3d} trades | {src_wr:.0f}% WR | ${row['total_pnl']:+.2f}")
    print()

    # By exit reason
    print("  BY EXIT REASON")
    for row in conn.execute(
        "SELECT exit_reason, COUNT(*) as cnt FROM trades WHERE entry_time >= ? GROUP BY exit_reason",
        (start_ts,),
    ):
        pct = row["cnt"] / total * 100 if total > 0 else 0
        print(f"    {row['exit_reason']:15s} {row['cnt']:3d} ({pct:.0f}%)")
    print()

    # By regime
    print("  REGIME PERFORMANCE")
    for row in conn.execute(
        "SELECT regime, COUNT(*) as cnt, SUM(pnl) as total_pnl FROM trades WHERE entry_time >= ? GROUP BY regime",
        (start_ts,),
    ):
        r_wins = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE entry_time >= ? AND regime = ? AND pnl > 0",
            (start_ts, row["regime"]),
        ).fetchone()[0]
        r_wr = r_wins / row["cnt"] * 100 if row["cnt"] > 0 else 0
        print(f"    {row['regime']:12s} {row['cnt']:3d} trades | {r_wr:.0f}% WR | ${row['total_pnl']:+.2f}")
    print()

    # Detail mode
    if detail:
        print("  TRADE DETAIL")
        print(f"  {'#':>3} {'Side':>5} {'Entry':>10} {'Exit':>10} {'PnL':>10} {'Reason':>10} {'Source':>12}")
        print("  " + "-" * 65)
        for i, row in enumerate(conn.execute(
            "SELECT * FROM trades WHERE entry_time >= ? ORDER BY entry_time", (start_ts,)
        )):
            print(
                f"  {i+1:3d} {row['side']:>5} "
                f"${row['entry_price']:>9,.1f} ${row['exit_price']:>9,.1f} "
                f"${row['pnl']:>+9.2f} {row['exit_reason']:>10} {row['merge_type']:>12}"
            )
        print()

    _print_signal_stats(conn, start_ts, signals)

    conn.close()
    print("=" * 56)


def _print_signal_stats(conn: sqlite3.Connection, start_ts: float, show_detail: bool) -> None:
    """Print signal statistics."""
    row = conn.execute(
        """SELECT
            COUNT(*) as total,
            SUM(CASE WHEN signal = 'LONG' THEN 1 ELSE 0 END) as longs,
            SUM(CASE WHEN signal = 'SHORT' THEN 1 ELSE 0 END) as shorts,
            SUM(CASE WHEN signal = 'NEUTRAL' THEN 1 ELSE 0 END) as neutrals,
            AVG(CASE WHEN signal != 'NEUTRAL' THEN total_score END) as avg_score
        FROM signals WHERE timeframe IN ('5m', '15m') AND timestamp >= ?""",
        (start_ts,),
    ).fetchone()

    total_signals = row["total"] or 0
    if total_signals == 0:
        return

    print("  SIGNAL STATISTICS")
    print(f"    Total computations:  {total_signals}")
    print(f"    LONG signals:        {row['longs'] or 0}")
    print(f"    SHORT signals:       {row['shorts'] or 0}")
    print(f"    NEUTRAL:             {row['neutrals'] or 0}")
    if row["avg_score"]:
        print(f"    Avg score at signal: {row['avg_score']:+.1f}")

    if show_detail:
        print()
        print("  BLOCKED REASONS")
        for brow in conn.execute(
            "SELECT blocked_reason, COUNT(*) as cnt FROM signals "
            "WHERE timestamp >= ? AND blocked_reason IS NOT NULL GROUP BY blocked_reason",
            (start_ts,),
        ):
            print(f"    {brow['blocked_reason']:20s} {brow['cnt']}")

    print()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="BayesMarket Performance Report")
    parser.add_argument("--period", default="1d", help="Period: 1d, 7d, 30d, all (default: 1d)")
    parser.add_argument("--detail", action="store_true", help="Show every trade")
    parser.add_argument("--signals", action="store_true", help="Show signal distribution")
    parser.add_argument("--db", default=str(config.DB_PATH), help="Database path")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        print("Run the bot first to generate data.")
        return

    run_report(db_path, args.period, args.detail, args.signals)


if __name__ == "__main__":
    main()
