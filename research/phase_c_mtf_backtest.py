"""Phase C: Multi-Timeframe Cascade Backtest.

Downloads klines for all required intervals (1m, 5m, 15m, 1h), computes
indicators per timeframe, and simulates the full cascade strategy:
  4h (BIAS) -> 1h (CONTEXT) -> 15m (TIMING zone) -> 5m (TRIGGER)

Uses the reduced_flipped indicator set (CVD, VWAP, POC, RSI) from Phase B.
CVD/VWAP/POC are flipped (mean-reversion); RSI kept as-is.

All results exported to .txt files in research/results/.

Usage:
    set PYTHONIOENCODING=utf-8
    python research/phase_c_mtf_backtest.py
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

# Reuse Phase A indicator computations
sys.path.insert(0, str(Path(__file__).parent))
from phase_a_ic_test import (
    compute_atr,
    compute_cvd_score,
    compute_ha_score,
    compute_macd_score,
    compute_poc_score,
    compute_rsi_score,
    compute_vwap_score,
    compute_ema_score,
    classify_regime,
    spearman_ic,
)

# ===================================================================
# CONFIG
# ===================================================================

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"

BINANCE_DATA_URL = "https://data-api.binance.vision/api/v3/klines"
SYMBOL = "BTCUSDT"
LOOKBACK_DAYS = 730  # 2 years
FETCH_LIMIT = 1000

# Timeframe definitions matching bayesmarket/config.py
# Each analysis TF uses a specific kline interval to build its candles
TF_CONFIG = {
    "5m": {
        "role": "trigger",
        "kline_interval": "1m",
        "kline_interval_ms": 60_000,
        "analysis_candles": 5,      # 5 x 1m = 5m candle
        "cascade_parent": "15m",
    },
    "15m": {
        "role": "timing",
        "kline_interval": "5m",
        "kline_interval_ms": 300_000,
        "analysis_candles": 3,      # 3 x 5m = 15m candle
        "cascade_parent": "1h",
    },
    "1h": {
        "role": "context",
        "kline_interval": "15m",
        "kline_interval_ms": 900_000,
        "analysis_candles": 4,      # 4 x 15m = 1h candle
        "cascade_parent": "4h",
    },
    "4h": {
        "role": "bias",
        "kline_interval": "1h",
        "kline_interval_ms": 3_600_000,
        "analysis_candles": 4,      # 4 x 1h = 4h candle
        "cascade_parent": None,
    },
}

# Unique kline intervals to fetch
KLINE_INTERVALS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
}

# Reduced flipped indicator set (Phase B winner)
INDICATOR_SET = ["CVD", "VWAP", "POC", "RSI"]
FLIP_SET = ["CVD", "VWAP", "POC"]  # RSI kept as-is

# Forward return horizons per TF (in candles of that TF's kline_interval)
FORWARD_HORIZONS_BY_TF = {
    "5m":  {"5m": 5, "15m": 15, "1h": 60},    # in 1m candles
    "15m": {"15m": 3, "1h": 12, "4h": 48},     # in 5m candles
    "1h":  {"1h": 4, "4h": 16, "1d": 96},      # in 15m candles
    "4h":  {"4h": 4, "1d": 24, "3d": 72},      # in 1h candles
}

# Cascade thresholds (proposed from Phase B research, +-7.0 range)
CASCADE_THRESHOLDS = {
    "4h_bias": 1.5,
    "1h_context_same_sign": True,  # 1h must match 4h direction
    "15m_timing": 3.0,
    "15m_zone_ttl_candles": 3,     # 3 x 5m = 15 min TTL
    "5m_trigger_trending": 3.5,
    "5m_trigger_ranging": 4.0,
}

# Alternative threshold sets to test
THRESHOLD_VARIANTS = {
    "conservative": {
        "4h_bias": 2.0, "15m_timing": 3.5,
        "5m_trigger_trending": 4.0, "5m_trigger_ranging": 4.5,
    },
    "baseline": {
        "4h_bias": 1.5, "15m_timing": 3.0,
        "5m_trigger_trending": 3.5, "5m_trigger_ranging": 4.0,
    },
    "aggressive": {
        "4h_bias": 1.0, "15m_timing": 2.5,
        "5m_trigger_trending": 3.0, "5m_trigger_ranging": 3.5,
    },
}

# Fees
MAKER_FEE = 0.00015
TAKER_FEE = 0.00045
SLIPPAGE = 0.0001
TOTAL_FEE = MAKER_FEE + TAKER_FEE + 2 * SLIPPAGE

# Walk-forward windows
WF_WINDOWS = [
    ("2024-04-01", "2024-09-30", "2024-10-01", "2024-12-31"),
    ("2024-07-01", "2024-12-31", "2025-01-01", "2025-03-31"),
    ("2024-10-01", "2025-03-31", "2025-04-01", "2025-06-30"),
    ("2025-01-01", "2025-06-30", "2025-07-01", "2025-09-30"),
    ("2025-04-01", "2025-09-30", "2025-10-01", "2025-12-31"),
    ("2025-07-01", "2025-12-31", "2026-01-01", "2026-03-25"),
]


# ===================================================================
# DATA FETCHING
# ===================================================================

def fetch_klines(interval: str) -> pd.DataFrame:
    """Fetch 2 years of klines for a given interval, with parquet cache."""
    import requests

    cache_file = DATA_DIR / f"btcusdt_{interval}.parquet"
    if cache_file.exists():
        df = pd.read_parquet(cache_file)
        print(f"  [cache] {interval}: {len(df):,} candles ({df.index[0]} to {df.index[-1]})")
        return df

    print(f"  [fetch] {interval}: downloading {LOOKBACK_DAYS} days...")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (LOOKBACK_DAYS * 86400 * 1000)
    interval_ms = KLINE_INTERVALS[interval]

    all_candles: list[list] = []
    cursor = start_ms
    request_count = 0

    while cursor < now_ms:
        try:
            resp = requests.get(
                BINANCE_DATA_URL,
                params={
                    "symbol": SYMBOL,
                    "interval": interval,
                    "startTime": cursor,
                    "limit": FETCH_LIMIT,
                },
                timeout=15,
            )
            resp.raise_for_status()
            batch = resp.json()

            if not batch:
                break

            all_candles.extend(batch)
            cursor = batch[-1][0] + interval_ms
            request_count += 1

            if request_count % 50 == 0:
                pct = min(100, (cursor - start_ms) / (now_ms - start_ms) * 100)
                print(f"    [{pct:5.1f}%] {len(all_candles):,} candles...")

            time.sleep(0.12)

        except Exception as exc:
            print(f"    [error] {exc} -- retrying in 3s")
            time.sleep(3)

    print(f"  [fetch] {interval}: {len(all_candles):,} candles in {request_count} requests")

    df = pd.DataFrame(all_candles, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "num_trades", "taker_buy_vol",
        "taker_buy_quote_vol", "ignore",
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume", "taker_buy_vol"]:
        df[col] = df[col].astype(float)

    df = df.set_index("open_time").sort_index()
    df = df[~df.index.duplicated(keep="first")]

    df.to_parquet(cache_file)
    print(f"  [cache] Saved {cache_file}")

    return df


def fetch_all_klines() -> dict[str, pd.DataFrame]:
    """Fetch klines for all required intervals."""
    print("=" * 70)
    print("  FETCHING KLINE DATA (4 intervals, 2 years each)")
    print("=" * 70)

    data = {}
    for interval in KLINE_INTERVALS:
        data[interval] = fetch_klines(interval)

    print()
    for interval, df in data.items():
        print(f"  {interval:>4s}: {len(df):>10,} candles | "
              f"{df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')}")
    print()
    return data


# ===================================================================
# INDICATOR COMPUTATION
# ===================================================================

def compute_indicators(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Compute all 7 indicators + ATR/regime from a kline DataFrame."""
    c = df["close"].values
    o = df["open"].values
    h = df["high"].values
    lo = df["low"].values
    v = df["volume"].values

    atr = compute_atr(h, lo, c)
    regime = classify_regime(atr)

    return {
        "CVD": compute_cvd_score(c, o, h, lo, v),
        "VWAP": compute_vwap_score(c, h, lo, v),
        "POC": compute_poc_score(c, h, lo, v),
        "HA": compute_ha_score(o, h, lo, c),
        "RSI": compute_rsi_score(c),
        "MACD": compute_macd_score(c, atr),
        "EMA": compute_ema_score(c),
        "atr": atr,
        "regime": regime,
    }


def build_composite(
    scores: dict[str, np.ndarray],
    indicators: list[str],
    flip: list[str],
) -> np.ndarray:
    """Build composite score with flipping."""
    n = len(scores["CVD"])
    composite = np.zeros(n)
    for name in indicators:
        s = scores[name].copy()
        if name in flip:
            s = -s
        valid = np.isfinite(s)
        composite[valid] += s[valid]
    return composite


def compute_forward_returns(closes: np.ndarray, horizons: dict[str, int]) -> dict[str, np.ndarray]:
    """Compute forward returns at specified horizons."""
    returns = {}
    for name, periods in horizons.items():
        fwd = np.full(len(closes), np.nan)
        if periods < len(closes):
            fwd[:-periods] = (closes[periods:] - closes[:-periods]) / closes[:-periods]
        returns[name] = fwd
    return returns


# ===================================================================
# PHASE A: PER-TIMEFRAME IC TEST
# ===================================================================

def run_phase_a_per_tf(kline_data: dict[str, pd.DataFrame], out: "OutputWriter") -> dict:
    """Run IC test for each analysis timeframe using its kline interval."""
    out.section("PHASE A: Per-Timeframe IC Test (Reduced Flipped Set)")
    out.line(f"Indicators: {', '.join(INDICATOR_SET)}")
    out.line(f"Flipped: {', '.join(FLIP_SET)}")
    out.line("")

    all_tf_results = {}

    for tf_name, tf_cfg in TF_CONFIG.items():
        interval = tf_cfg["kline_interval"]
        df = kline_data[interval]
        closes = df["close"].values

        out.subsection(f"TF: {tf_name} (using {interval} klines, {len(df):,} candles)")

        # Compute indicators
        scores = compute_indicators(df)
        regime = scores["regime"]
        n_trending = (regime == "trending").sum()
        n_ranging = (regime == "ranging").sum()
        out.line(f"  Regime: {n_trending:,} trending ({n_trending/len(regime)*100:.1f}%), "
                 f"{n_ranging:,} ranging ({n_ranging/len(regime)*100:.1f}%)")

        # Composite (reduced_flipped)
        composite = build_composite(scores, INDICATOR_SET, FLIP_SET)
        out.line(f"  Composite range: [{composite.min():+.2f}, {composite.max():+.2f}]")

        # Forward returns
        horizons = FORWARD_HORIZONS_BY_TF[tf_name]
        fwd_returns = compute_forward_returns(closes, horizons)

        # IC test per indicator (flipped)
        out.line("")
        hz_names = list(horizons.keys())
        header = f"  {'Indicator':<10} |"
        for hz in hz_names:
            header += f"  IC_{hz:>4s} "
        header += f"| {'p_best':>8} | {'Keep?':>5}"
        out.line(header)
        out.line("  " + "-" * (len(header) - 2))

        for name in INDICATOR_SET:
            s = scores[name].copy()
            if name in FLIP_SET:
                s = -s

            ics = {}
            pvals = {}
            for hz_name, fwd in fwd_returns.items():
                ic, pv = spearman_ic(s, fwd)
                ics[hz_name] = ic
                pvals[hz_name] = pv

            valid_pvals = [p for p in pvals.values() if np.isfinite(p)]
            best_p = min(valid_pvals) if valid_pvals else np.nan
            valid_ics = [abs(ic) for ic in ics.values() if np.isfinite(ic)]
            best_abs_ic = max(valid_ics) if valid_ics else 0.0
            keep = "YES" if best_abs_ic >= 0.02 and np.isfinite(best_p) and best_p < 0.05 else "NO"

            line = f"  {name:<10} |"
            for hz in hz_names:
                ic = ics[hz]
                line += f"  {ic:+.4f} " if np.isfinite(ic) else "      N/A "
            line += f"| {best_p:>8.2e} | {keep:>5}" if np.isfinite(best_p) else f"| {'N/A':>8} | {keep:>5}"
            out.line(line)

        # Composite IC
        out.line("")
        for hz_name, fwd in fwd_returns.items():
            ic_c, pv_c = spearman_ic(composite, fwd)
            out.line(f"  Composite IC ({hz_name}): {ic_c:+.4f}  (p={pv_c:.2e})")

        # Threshold sweep
        out.line("")
        first_hz = hz_names[0]
        out.line(f"  {'Thresh':>6} | {'Signals':>8} | {'Tr/Day':>7} | "
                 f"{'WR':>6} | {'Avg_ret':>9} | {'Sharpe':>7} | {'PF':>5}")
        out.line("  " + "-" * 65)

        candles_per_day = 86400_000 / tf_cfg["kline_interval_ms"]
        for thr in [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]:
            long_m = composite >= thr
            short_m = composite <= -thr
            n_sig = long_m.sum() + short_m.sum()

            if n_sig < 10:
                out.line(f"  {thr:>6.1f} | {n_sig:>8} | {'---':>7} | "
                         f"{'---':>6} | {'---':>9} | {'---':>7} | {'---':>5}")
                continue

            total_days = len(composite) / candles_per_day
            trades_day = n_sig / total_days

            fwd = fwd_returns[first_hz]
            dir_ret = np.where(long_m, fwd, np.where(short_m, -fwd, np.nan))
            v = dir_ret[np.isfinite(dir_ret)]

            if len(v) < 10:
                out.line(f"  {thr:>6.1f} | {n_sig:>8,} | {trades_day:>7.1f} | "
                         f"{'---':>6} | {'---':>9} | {'---':>7} | {'---':>5}")
                continue

            v_net = v - TOTAL_FEE
            wr = (v_net > 0).mean()
            avg = v_net.mean()
            std = v_net.std()
            sharpe = (avg / std * np.sqrt(candles_per_day)) if std > 0 else 0
            wins = v_net[v_net > 0]
            losses = v_net[v_net < 0]
            pf = wins.sum() / abs(losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 0

            out.line(f"  {thr:>6.1f} | {n_sig:>8,} | {trades_day:>7.1f} | "
                     f"{wr*100:>5.1f}% | {avg*100:>+8.4f}% | {sharpe:>+7.2f} | {pf:>5.2f}")

        all_tf_results[tf_name] = {
            "scores": scores,
            "composite": composite,
            "regime": regime,
            "df": df,
        }

    return all_tf_results


# ===================================================================
# PHASE B: PER-TIMEFRAME WALK-FORWARD
# ===================================================================

def run_phase_b_per_tf(kline_data: dict[str, pd.DataFrame], out: "OutputWriter") -> dict:
    """Walk-forward validation per timeframe."""
    out.section("PHASE B: Per-Timeframe Walk-Forward Validation")

    tf_optimal = {}

    for tf_name, tf_cfg in TF_CONFIG.items():
        interval = tf_cfg["kline_interval"]
        df = kline_data[interval]
        closes = df["close"].values
        horizons = FORWARD_HORIZONS_BY_TF[tf_name]
        first_hz = list(horizons.keys())[0]
        first_hz_periods = horizons[first_hz]
        candles_per_day = 86400_000 / tf_cfg["kline_interval_ms"]

        out.subsection(f"TF: {tf_name} (kline: {interval}, eval horizon: {first_hz})")

        scores = compute_indicators(df)
        composite = build_composite(scores, INDICATOR_SET, FLIP_SET)
        fwd_rets = compute_forward_returns(closes, horizons)

        out.line(f"  {'Win':>3} | {'Train':>21} | {'Test':>21} | "
                 f"{'Best_Thr':>8} | {'IS_Sharpe':>9} | "
                 f"{'OOS_WR':>7} | {'OOS_Sharpe':>10} | {'OOS_PF':>6} | {'OOS_N':>6}")
        out.line("  " + "-" * 105)

        oos_sharpes = []
        best_thresholds = []

        for wi, (tr_start, tr_end, te_start, te_end) in enumerate(WF_WINDOWS):
            tr_mask = (df.index >= tr_start) & (df.index <= tr_end)
            te_mask = (df.index >= te_start) & (df.index <= te_end)

            if tr_mask.sum() < 100 or te_mask.sum() < 50:
                out.line(f"  {wi+1:>3} | {tr_start} to {tr_end} | {te_start} to {te_end} | "
                         f"{'SKIP':>60}")
                continue

            tr_idx = np.where(np.asarray(tr_mask))[0]
            te_idx = np.where(np.asarray(te_mask))[0]

            # In-sample: find best threshold
            best_thr = 1.0
            best_is_sharpe = -999

            thresholds = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]
            for thr in thresholds:
                long_m = np.zeros(len(composite), dtype=bool)
                short_m = np.zeros(len(composite), dtype=bool)
                long_m[tr_idx] = composite[tr_idx] >= thr
                short_m[tr_idx] = composite[tr_idx] <= -thr

                n_sig = long_m.sum() + short_m.sum()
                if n_sig < 20:
                    continue

                fwd = fwd_rets[first_hz]
                dir_ret = np.where(long_m, fwd, np.where(short_m, -fwd, np.nan))
                v = dir_ret[np.isfinite(dir_ret)]
                if len(v) < 20:
                    continue

                v_net = v - TOTAL_FEE
                sh = (v_net.mean() / v_net.std() * np.sqrt(candles_per_day)) if v_net.std() > 0 else 0
                if sh > best_is_sharpe:
                    best_is_sharpe = sh
                    best_thr = thr

            # OOS evaluation
            long_oos = np.zeros(len(composite), dtype=bool)
            short_oos = np.zeros(len(composite), dtype=bool)
            long_oos[te_idx] = composite[te_idx] >= best_thr
            short_oos[te_idx] = composite[te_idx] <= -best_thr

            fwd = fwd_rets[first_hz]
            dir_ret_oos = np.where(long_oos, fwd, np.where(short_oos, -fwd, np.nan))
            v_oos = dir_ret_oos[np.isfinite(dir_ret_oos)]

            if len(v_oos) < 10:
                out.line(f"  {wi+1:>3} | {tr_start} to {tr_end} | {te_start} to {te_end} | "
                         f"{best_thr:>8.1f} | {best_is_sharpe:>+9.2f} | "
                         f"{'<10':>7} | {'---':>10} | {'---':>6} | {len(v_oos):>6}")
                continue

            v_net = v_oos - TOTAL_FEE
            oos_wr = (v_net > 0).mean()
            oos_sharpe = (v_net.mean() / v_net.std() * np.sqrt(candles_per_day)) if v_net.std() > 0 else 0
            wins = v_net[v_net > 0]
            losses = v_net[v_net < 0]
            oos_pf = wins.sum() / abs(losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 0

            oos_sharpes.append(oos_sharpe)
            best_thresholds.append(best_thr)

            out.line(f"  {wi+1:>3} | {tr_start} to {tr_end} | {te_start} to {te_end} | "
                     f"{best_thr:>8.1f} | {best_is_sharpe:>+9.2f} | "
                     f"{oos_wr*100:>6.1f}% | {oos_sharpe:>+10.2f} | {oos_pf:>6.2f} | {len(v_oos):>6}")

        if oos_sharpes:
            avg_sharpe = np.mean(oos_sharpes)
            positive = sum(1 for s in oos_sharpes if s > 0)
            mode_thr = stats.mode(best_thresholds, keepdims=False).mode
            out.line(f"\n  Summary: Avg OOS Sharpe={avg_sharpe:+.2f}, "
                     f"Positive={positive}/{len(oos_sharpes)}, "
                     f"Threshold mode={mode_thr:.1f}")
            tf_optimal[tf_name] = {
                "threshold": float(mode_thr),
                "avg_sharpe": float(avg_sharpe),
                "positive_ratio": f"{positive}/{len(oos_sharpes)}",
            }
        else:
            tf_optimal[tf_name] = {"threshold": 3.5, "avg_sharpe": 0.0, "positive_ratio": "0/0"}

        out.line("")

    return tf_optimal


# ===================================================================
# PHASE C: FULL CASCADE SIMULATION
# ===================================================================

def run_cascade_simulation(
    kline_data: dict[str, pd.DataFrame],
    threshold_variant_name: str,
    thresholds: dict,
    out: "OutputWriter",
) -> list[dict]:
    """Simulate the 4h->1h->15m->5m cascade over 2 years.

    Logic:
      1. At each 5m candle boundary (= every 5 rows of 1m data):
         - Look up the latest 4h composite score -> sets BIAS direction
         - Look up the latest 1h composite score -> must match 4h sign (CONTEXT)
         - Look up the latest 15m composite score -> if > timing threshold & matches bias,
           TIMING ZONE activates (TTL = 15 min = 3 x 5m candles)
         - 5m composite score > trigger threshold & zone active & matches bias -> TRADE
      2. One position at a time. Hold for 1h (12 x 5m candles) then exit.
      3. Record each trade: entry time, direction, entry price, exit price, return.
    """
    bias_thr = thresholds.get("4h_bias", 1.5)
    timing_thr = thresholds.get("15m_timing", 3.0)
    trigger_thr_trend = thresholds.get("5m_trigger_trending", 3.5)
    trigger_thr_range = thresholds.get("5m_trigger_ranging", 4.0)
    zone_ttl = thresholds.get("15m_zone_ttl_candles", 3)

    out.subsection(f"Cascade Variant: {threshold_variant_name}")
    out.line(f"  4h bias >= {bias_thr}, 1h context = same sign, "
             f"15m timing >= {timing_thr}, 5m trigger >= {trigger_thr_trend}/{trigger_thr_range}")
    out.line(f"  Zone TTL: {zone_ttl} x 5m candles")
    out.line("")

    # Compute per-TF composites aligned to common timeline
    # Strategy: compute composite for each TF on its own kline data,
    # then align everything to 5m boundaries.

    # Step 1: Compute composites per TF
    tf_composites = {}
    tf_regimes = {}
    tf_closes = {}

    for tf_name, tf_cfg in TF_CONFIG.items():
        interval = tf_cfg["kline_interval"]
        df = kline_data[interval]
        scores = compute_indicators(df)
        composite = build_composite(scores, INDICATOR_SET, FLIP_SET)
        regime = scores["regime"]

        tf_composites[tf_name] = pd.Series(composite, index=df.index)
        tf_regimes[tf_name] = pd.Series(regime, index=df.index)
        tf_closes[tf_name] = pd.Series(df["close"].values, index=df.index)

    # Step 2: Build 5m candles from 1m data for entry/exit prices
    df_1m = kline_data["1m"]
    # Resample 1m -> 5m for price reference
    df_5m_price = df_1m["close"].resample("5min").last().dropna()

    # Step 3: Walk through 5m boundaries
    # The 5m composite is on 1m klines, so we look at every 5th 1m candle
    # But it's easier to just use the 5m composite timestamps directly

    # For cascade, we need to look up "latest known" composite for each TF
    # at each 5m timestamp. Since each TF's composite is indexed by its
    # kline_interval timestamps, we do asof-merge.

    timestamps_5m = tf_composites["5m"].index  # 1m timestamps
    # Actually 5m composite is computed on 1m klines, so it has 1m resolution.
    # We sample every 5th candle to get 5m decision points.
    decision_times = timestamps_5m[::5]  # every 5 minutes

    trades = []
    position = None  # {"direction": "LONG"/"SHORT", "entry_time": ts, "entry_price": float, "exit_idx": int}

    zone_active = False
    zone_direction = None
    zone_expiry_idx = 0

    for di, ts in enumerate(decision_times):
        # Check if we need to exit current position
        if position is not None:
            if di >= position["exit_idx"]:
                # Exit
                exit_price = _get_price_at(tf_closes["5m"], ts)
                if exit_price is not None and position["entry_price"] > 0:
                    if position["direction"] == "LONG":
                        ret = (exit_price - position["entry_price"]) / position["entry_price"]
                    else:
                        ret = (position["entry_price"] - exit_price) / position["entry_price"]

                    ret_net = ret - TOTAL_FEE
                    trades.append({
                        "entry_time": position["entry_time"],
                        "exit_time": ts,
                        "direction": position["direction"],
                        "entry_price": position["entry_price"],
                        "exit_price": exit_price,
                        "gross_return": ret,
                        "net_return": ret_net,
                        "hold_candles": di - position["entry_di"],
                    })
                position = None
            else:
                continue  # still in position, skip

        # Lookup latest composite scores
        score_4h = _latest_score(tf_composites["4h"], ts)
        score_1h = _latest_score(tf_composites["1h"], ts)
        score_15m = _latest_score(tf_composites["15m"], ts)
        score_5m = _latest_score(tf_composites["5m"], ts)
        regime_5m = _latest_regime(tf_regimes["5m"], ts)

        if any(x is None for x in [score_4h, score_1h, score_15m, score_5m]):
            continue

        # --- CASCADE LOGIC ---

        # 1. 4h BIAS
        if abs(score_4h) < bias_thr:
            zone_active = False
            continue

        bias_direction = "LONG" if score_4h > 0 else "SHORT"

        # 2. 1h CONTEXT: must match 4h sign
        if bias_direction == "LONG" and score_1h <= 0:
            continue
        if bias_direction == "SHORT" and score_1h >= 0:
            continue

        # 3. 15m TIMING ZONE
        if abs(score_15m) >= timing_thr:
            zone_15m_dir = "LONG" if score_15m > 0 else "SHORT"
            if zone_15m_dir == bias_direction:
                zone_active = True
                zone_direction = bias_direction
                zone_expiry_idx = di + zone_ttl

        # Check zone expiry
        if di >= zone_expiry_idx:
            zone_active = False

        if not zone_active:
            continue

        # 4. 5m TRIGGER
        trigger_thr = trigger_thr_trend if regime_5m == "trending" else trigger_thr_range

        if bias_direction == "LONG" and score_5m >= trigger_thr:
            pass  # trigger!
        elif bias_direction == "SHORT" and score_5m <= -trigger_thr:
            pass  # trigger!
        else:
            continue

        # ENTRY
        entry_price = _get_price_at(tf_closes["5m"], ts)
        if entry_price is None or entry_price <= 0:
            continue

        hold_candles = 12  # hold for 1h = 12 x 5m decision points
        position = {
            "direction": bias_direction,
            "entry_time": ts,
            "entry_price": entry_price,
            "exit_idx": di + hold_candles,
            "entry_di": di,
        }

    # Close any remaining position at end
    if position is not None and len(decision_times) > 0:
        last_ts = decision_times[-1]
        exit_price = _get_price_at(tf_closes["5m"], last_ts)
        if exit_price is not None and position["entry_price"] > 0:
            if position["direction"] == "LONG":
                ret = (exit_price - position["entry_price"]) / position["entry_price"]
            else:
                ret = (position["entry_price"] - exit_price) / position["entry_price"]
            trades.append({
                "entry_time": position["entry_time"],
                "exit_time": last_ts,
                "direction": position["direction"],
                "entry_price": position["entry_price"],
                "exit_price": exit_price,
                "gross_return": ret,
                "net_return": ret - TOTAL_FEE,
                "hold_candles": len(decision_times) - 1 - position["entry_di"],
            })

    # Print results
    _print_trade_summary(trades, out, threshold_variant_name)

    return trades


def _latest_score(series: pd.Series, ts: pd.Timestamp) -> float | None:
    """Get the latest score at or before timestamp ts."""
    mask = series.index <= ts
    if mask.sum() == 0:
        return None
    return float(series.loc[mask].iloc[-1])


def _latest_regime(series: pd.Series, ts: pd.Timestamp) -> str | None:
    """Get the latest regime at or before timestamp ts."""
    mask = series.index <= ts
    if mask.sum() == 0:
        return None
    return str(series.loc[mask].iloc[-1])


def _get_price_at(series: pd.Series, ts: pd.Timestamp) -> float | None:
    """Get price at or just before timestamp."""
    mask = series.index <= ts
    if mask.sum() == 0:
        return None
    return float(series.loc[mask].iloc[-1])


def _print_trade_summary(trades: list[dict], out: "OutputWriter", variant: str) -> None:
    """Print trade-by-trade results and summary statistics."""
    n = len(trades)
    out.line(f"  Total trades: {n}")

    if n == 0:
        out.line("  No trades generated.")
        out.line("")
        return

    # Trade list
    out.line("")
    out.line(f"  {'#':>4} | {'Entry Time':>20} | {'Dir':>5} | {'Entry':>10} | "
             f"{'Exit':>10} | {'Gross':>9} | {'Net':>9} | {'Correct':>7}")
    out.line("  " + "-" * 95)

    n_correct = 0
    total_net = 0.0
    monthly_returns: dict[str, list[float]] = {}

    for i, t in enumerate(trades):
        correct = t["net_return"] > 0
        if correct:
            n_correct += 1
        total_net += t["net_return"]

        month_key = t["entry_time"].strftime("%Y-%m") if hasattr(t["entry_time"], "strftime") else str(t["entry_time"])[:7]
        monthly_returns.setdefault(month_key, []).append(t["net_return"])

        entry_ts = t["entry_time"].strftime("%Y-%m-%d %H:%M") if hasattr(t["entry_time"], "strftime") else str(t["entry_time"])[:16]
        out.line(f"  {i+1:>4} | {entry_ts:>20} | {t['direction']:>5} | "
                 f"${t['entry_price']:>9,.0f} | ${t['exit_price']:>9,.0f} | "
                 f"{t['gross_return']*100:>+8.3f}% | {t['net_return']*100:>+8.3f}% | "
                 f"{'YES' if correct else 'NO':>7}")

    # Summary
    returns = np.array([t["net_return"] for t in trades])
    wins = returns[returns > 0]
    losses = returns[returns < 0]

    out.line("")
    out.line("  " + "=" * 50)
    out.line(f"  SUMMARY ({variant})")
    out.line("  " + "=" * 50)
    out.line(f"  Total trades:        {n}")
    out.line(f"  Win rate:            {n_correct/n*100:.1f}%")
    out.line(f"  Avg net return:      {returns.mean()*100:+.4f}%")
    out.line(f"  Total net return:    {returns.sum()*100:+.3f}%")
    out.line(f"  Std dev:             {returns.std()*100:.4f}%")

    if returns.std() > 0:
        # Annualized Sharpe (assume ~1 trade per few days)
        sharpe_per_trade = returns.mean() / returns.std()
        # Rough annualization: sqrt(trades_per_year)
        total_days = (trades[-1]["entry_time"] - trades[0]["entry_time"]).total_seconds() / 86400 if n > 1 else 365
        trades_per_year = n / total_days * 365 if total_days > 0 else n
        sharpe_annual = sharpe_per_trade * np.sqrt(trades_per_year)
        out.line(f"  Sharpe (annualized): {sharpe_annual:+.2f}  ({trades_per_year:.1f} trades/yr)")

    if len(wins) > 0:
        out.line(f"  Avg win:             {wins.mean()*100:+.4f}%")
    if len(losses) > 0:
        out.line(f"  Avg loss:            {losses.mean()*100:+.4f}%")
    if len(wins) > 0 and len(losses) > 0 and losses.sum() != 0:
        pf = wins.sum() / abs(losses.sum())
        out.line(f"  Profit factor:       {pf:.2f}")

    # Max drawdown
    cumulative = np.cumsum(returns)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = cumulative - running_max
    out.line(f"  Max drawdown:        {drawdown.min()*100:.3f}%")

    # Direction breakdown
    longs = [t for t in trades if t["direction"] == "LONG"]
    shorts = [t for t in trades if t["direction"] == "SHORT"]
    if longs:
        lr = np.array([t["net_return"] for t in longs])
        out.line(f"  LONG trades:         {len(longs)} (WR {(lr>0).mean()*100:.1f}%, avg {lr.mean()*100:+.4f}%)")
    if shorts:
        sr = np.array([t["net_return"] for t in shorts])
        out.line(f"  SHORT trades:        {len(shorts)} (WR {(sr>0).mean()*100:.1f}%, avg {sr.mean()*100:+.4f}%)")

    # Monthly breakdown
    if monthly_returns:
        out.line("")
        out.line(f"  {'Month':>7} | {'Trades':>6} | {'WR':>6} | {'Total':>9} | {'Avg':>9}")
        out.line("  " + "-" * 45)
        for month in sorted(monthly_returns.keys()):
            rets = np.array(monthly_returns[month])
            wr = (rets > 0).mean() * 100
            out.line(f"  {month:>7} | {len(rets):>6} | {wr:>5.1f}% | "
                     f"{rets.sum()*100:>+8.3f}% | {rets.mean()*100:>+8.4f}%")

    out.line("")


# ===================================================================
# OUTPUT WRITER
# ===================================================================

class OutputWriter:
    """Write results to .txt file instead of terminal."""

    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self._lines: list[str] = []

    def line(self, text: str = "") -> None:
        self._lines.append(text)

    def section(self, title: str) -> None:
        self._lines.append("")
        self._lines.append("=" * 78)
        self._lines.append(f"  {title}")
        self._lines.append("=" * 78)
        self._lines.append("")

    def subsection(self, title: str) -> None:
        self._lines.append("")
        self._lines.append(f"  --- {title} ---")
        self._lines.append("")

    def save(self) -> None:
        with open(self.filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(self._lines))
        print(f"  [saved] {self.filepath}")


# ===================================================================
# MAIN
# ===================================================================

def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("  PHASE C: Multi-Timeframe Cascade Backtest")
    print("  4h BIAS -> 1h CONTEXT -> 15m TIMING -> 5m TRIGGER")
    print("  Indicator set: reduced_flipped (CVD*, VWAP*, POC*, RSI)")
    print("  * = flipped for mean-reversion")
    print("=" * 70)
    print()

    # Step 1: Fetch all kline data
    kline_data = fetch_all_klines()

    # Step 2: Phase A — per-TF IC test
    print("[Phase A] Running per-TF IC test...")
    out_a = OutputWriter(RESULTS_DIR / "phase_a_mtf_ic_test.txt")
    out_a.line("Phase A: Multi-Timeframe IC Test Results")
    out_a.line(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    out_a.line(f"Data: BTCUSDT, {LOOKBACK_DAYS} days, Binance Spot")
    out_a.line(f"Indicator set: {', '.join(INDICATOR_SET)} (flipped: {', '.join(FLIP_SET)})")

    all_tf_results = run_phase_a_per_tf(kline_data, out_a)
    out_a.save()
    print()

    # Step 3: Phase B — per-TF walk-forward
    print("[Phase B] Running per-TF walk-forward validation...")
    out_b = OutputWriter(RESULTS_DIR / "phase_b_mtf_walkforward.txt")
    out_b.line("Phase B: Multi-Timeframe Walk-Forward Validation")
    out_b.line(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    out_b.line(f"Indicator set: {', '.join(INDICATOR_SET)} (flipped: {', '.join(FLIP_SET)})")

    tf_optimal = run_phase_b_per_tf(kline_data, out_b)

    out_b.section("Optimal Thresholds Summary")
    for tf, info in tf_optimal.items():
        out_b.line(f"  {tf:>4s}: threshold={info['threshold']:.1f}, "
                   f"avg_sharpe={info['avg_sharpe']:+.2f}, "
                   f"positive={info['positive_ratio']}")

    out_b.save()
    print()

    # Step 4: Phase C — Full cascade simulation (3 threshold variants)
    print("[Phase C] Running cascade simulation...")
    out_c = OutputWriter(RESULTS_DIR / "phase_c_cascade_backtest.txt")
    out_c.line("Phase C: Full Cascade Backtest Results")
    out_c.line(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    out_c.line(f"Strategy: 4h BIAS -> 1h CONTEXT -> 15m TIMING -> 5m TRIGGER")
    out_c.line(f"Indicator set: {', '.join(INDICATOR_SET)} (flipped: {', '.join(FLIP_SET)})")
    out_c.line(f"Hold period: 1h (12 x 5m candles)")
    out_c.line(f"Fees: maker {MAKER_FEE*100:.3f}% + taker {TAKER_FEE*100:.3f}% + slippage {SLIPPAGE*100:.3f}%")

    all_variant_trades = {}
    for variant_name, variant_thresholds in THRESHOLD_VARIANTS.items():
        out_c.section(f"Variant: {variant_name}")
        trades = run_cascade_simulation(kline_data, variant_name, variant_thresholds, out_c)
        all_variant_trades[variant_name] = trades
        print(f"  {variant_name}: {len(trades)} trades")

    # Comparison table
    out_c.section("VARIANT COMPARISON")
    out_c.line(f"  {'Variant':>14} | {'Trades':>6} | {'WR':>6} | {'Avg_Net':>9} | "
               f"{'Total':>9} | {'Sharpe':>7} | {'MaxDD':>7}")
    out_c.line("  " + "-" * 70)

    for vname, trades in all_variant_trades.items():
        if not trades:
            out_c.line(f"  {vname:>14} | {0:>6} | {'---':>6} | {'---':>9} | "
                       f"{'---':>9} | {'---':>7} | {'---':>7}")
            continue

        rets = np.array([t["net_return"] for t in trades])
        wr = (rets > 0).mean() * 100
        cum = np.cumsum(rets)
        dd = (cum - np.maximum.accumulate(cum)).min() * 100

        total_days = (trades[-1]["entry_time"] - trades[0]["entry_time"]).total_seconds() / 86400 if len(trades) > 1 else 365
        tpy = len(trades) / total_days * 365 if total_days > 0 else len(trades)
        sh = (rets.mean() / rets.std() * np.sqrt(tpy)) if rets.std() > 0 else 0

        out_c.line(f"  {vname:>14} | {len(trades):>6} | {wr:>5.1f}% | "
                   f"{rets.mean()*100:>+8.4f}% | {rets.sum()*100:>+8.3f}% | "
                   f"{sh:>+7.2f} | {dd:>+6.3f}%")

    out_c.save()
    print()

    # Step 5: Summary file
    out_summary = OutputWriter(RESULTS_DIR / "phase_c_summary.txt")
    out_summary.line("=" * 70)
    out_summary.line("  PHASE C: EXECUTIVE SUMMARY")
    out_summary.line("=" * 70)
    out_summary.line(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    out_summary.line("")
    out_summary.line("Strategy: BayesMarket Cascade MTF")
    out_summary.line("  4h sets BIAS direction (score > threshold)")
    out_summary.line("  1h confirms CONTEXT (same sign as 4h)")
    out_summary.line("  15m activates TIMING ZONE (score > threshold, matches bias, 15min TTL)")
    out_summary.line("  5m fires TRIGGER (score > threshold, zone active, matches bias)")
    out_summary.line("")
    out_summary.line("Indicators: CVD (flipped), VWAP (flipped), POC (flipped), RSI (kept)")
    out_summary.line("Score range: +/-7.0")
    out_summary.line("")

    out_summary.subsection("Per-TF Optimal Thresholds (Walk-Forward)")
    for tf, info in tf_optimal.items():
        out_summary.line(f"  {tf:>4s}: {info['threshold']:.1f} "
                         f"(Sharpe={info['avg_sharpe']:+.2f}, OOS+={info['positive_ratio']})")

    out_summary.subsection("Cascade Results by Variant")
    for vname, trades in all_variant_trades.items():
        n = len(trades)
        if n == 0:
            out_summary.line(f"  {vname}: 0 trades")
            continue
        rets = np.array([t["net_return"] for t in trades])
        wr = (rets > 0).mean() * 100
        out_summary.line(f"  {vname}: {n} trades, WR={wr:.1f}%, "
                         f"avg={rets.mean()*100:+.4f}%, total={rets.sum()*100:+.3f}%")

    out_summary.subsection("Recommendation")
    # Find best variant
    best_v = None
    best_total = -999
    for vname, trades in all_variant_trades.items():
        if trades:
            total = sum(t["net_return"] for t in trades)
            if total > best_total:
                best_total = total
                best_v = vname

    if best_v:
        out_summary.line(f"  Best variant: {best_v}")
        out_summary.line(f"  Thresholds: {THRESHOLD_VARIANTS[best_v]}")
    else:
        out_summary.line("  No trades generated by any variant. Thresholds may be too strict")
        out_summary.line("  or the cascade filter combination is too selective for 2yr backtest.")

    out_summary.line("")
    out_summary.line("Output files:")
    out_summary.line(f"  {RESULTS_DIR / 'phase_a_mtf_ic_test.txt'}")
    out_summary.line(f"  {RESULTS_DIR / 'phase_b_mtf_walkforward.txt'}")
    out_summary.line(f"  {RESULTS_DIR / 'phase_c_cascade_backtest.txt'}")
    out_summary.line(f"  {RESULTS_DIR / 'phase_c_summary.txt'}")

    out_summary.save()

    print("=" * 70)
    print("  DONE. All results saved to research/results/")
    print("=" * 70)
    print(f"  phase_a_mtf_ic_test.txt      — IC test per timeframe")
    print(f"  phase_b_mtf_walkforward.txt  — Walk-forward per timeframe")
    print(f"  phase_c_cascade_backtest.txt — Full cascade trade-by-trade")
    print(f"  phase_c_summary.txt          — Executive summary")


if __name__ == "__main__":
    main()
