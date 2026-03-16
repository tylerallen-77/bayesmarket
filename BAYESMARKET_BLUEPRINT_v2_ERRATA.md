# BAYESMARKET MVP — BLUEPRINT v2 ERRATA & PATCHES
## Critical Fixes, Additions, and Corrections

> **Dokumen ini WAJIB dibaca bersama BAYESMARKET_MVP_BLUEPRINT_v2.md.**
> Setiap section di sini MENGGANTIKAN (override) section yang sama di blueprint v2.
> Jika ada konflik antara blueprint v2 dan dokumen ini, dokumen ini yang benar.

---

## PATCH #1: Kline Source — Synthetic Primary, Binance Futures Fallback

### Apa yang berubah
Blueprint v2 Section 4 (`BINANCE_SYMBOL = "BTCUSDT"`) dan Section 9.2 menggunakan
**Binance Spot** klines. Ini SALAH. Harga Binance Spot dan Hyperliquid Perp bisa
diverge $10-30 saat volatilitas tinggi, menyebabkan VWAP score permanently skewed.

### Override: config.py
```python
# REPLACE these lines in config.py:

# OLD:
# BINANCE_SYMBOL = "BTCUSDT"
# BINANCE_WS_URL = "wss://stream.binance.com/stream"
# BINANCE_REST_URL = "https://api.binance.com/api/v3"

# NEW:
BINANCE_SYMBOL = "BTCUSDT"           # Used for FUTURES, not spot
BINANCE_FUTURES_WS_URL = "wss://fstream.binance.com/stream"
BINANCE_FUTURES_REST_URL = "https://fapi.binance.com/fapi/v1"

# Kline source priority
KLINE_SOURCE = "synthetic"           # "synthetic" = build from HL trades (primary)
                                     # "binance_futures" = fallback if HL trades interrupted
KLINE_FALLBACK_ENABLED = True        # Auto-switch to Binance Futures if synthetic fails
KLINE_FALLBACK_STALE_SECONDS = 10    # If no HL trade for 10s, switch to fallback
```

### Override: Section 9.2 — Binance Feeds (COMPLETE REPLACEMENT)

```
BINANCE FUTURES feeds (FALLBACK ONLY — not primary):

BOOTSTRAP (on startup, 4 REST calls to Binance FUTURES):
  GET https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=150
  GET https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=150
  GET https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=15m&limit=100
  GET https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=100

  These populate tf_states[*].klines as INITIAL data.
  Once synthetic kline builder has enough HL trades (after ~2-3 minutes),
  synthetic klines TAKE OVER as primary source.

WEBSOCKET (fallback, single multiplexed connection):
  URL: wss://fstream.binance.com/stream?streams=btcusdt@kline_1m/btcusdt@kline_5m/btcusdt@kline_15m/btcusdt@kline_1h

  This connection stays ALIVE but data is only used when:
  - Synthetic kline builder has no HL trades for > KLINE_FALLBACK_STALE_SECONDS (10s)
  - On that condition: switch to Binance Futures klines until HL trades resume
  - Log event: "[FALLBACK] Switched to Binance Futures klines — HL trades stale"
  - When HL trades resume: switch back to synthetic
  - Log event: "[RECOVERED] Switched back to synthetic klines from HL trades"
```

### NEW Section: Synthetic Kline Builder — `feeds/synthetic.py`

```
PURPOSE: Build OHLCV candles directly from Hyperliquid trade stream.
This ensures ZERO price divergence between kline data and order book data.

ARCHITECTURE:
  - One SyntheticKlineBuilder instance PER timeframe interval (1m, 5m, 15m, 1h)
  - Each receives the SAME trade stream from HL WebSocket
  - Each aggregates trades into candles of its interval

CLASS SyntheticKlineBuilder:
  INIT(interval_seconds):
    self.interval = interval_seconds  # 60, 300, 900, 3600
    self.current_candle = None
    self.candle_start_time = None

  ON_TRADE(trade: TradeEvent):
    # Determine which candle bucket this trade belongs to
    bucket_start = floor(trade.timestamp / self.interval) * self.interval

    IF self.current_candle is None OR bucket_start != self.candle_start_time:
      # New candle period started
      IF self.current_candle is not None:
        # Close previous candle
        self.current_candle.closed = True
        EMIT(self.current_candle)  # Send to tf_state.klines

      # Start new candle
      self.candle_start_time = bucket_start
      self.current_candle = Candle(
        timestamp=bucket_start,
        open=trade.price,
        high=trade.price,
        low=trade.price,
        close=trade.price,
        volume=trade.size,
        closed=False
      )
    ELSE:
      # Update current candle
      self.current_candle.high = max(self.current_candle.high, trade.price)
      self.current_candle.low = min(self.current_candle.low, trade.price)
      self.current_candle.close = trade.price
      self.current_candle.volume += trade.size

INSTANCES NEEDED (one per TF's kline_interval):
  builder_1m  = SyntheticKlineBuilder(60)    # for 5m TF
  builder_5m  = SyntheticKlineBuilder(300)   # for 15m TF
  builder_15m = SyntheticKlineBuilder(900)   # for 1h TF
  builder_1h  = SyntheticKlineBuilder(3600)  # for 4h TF

ROUTING:
  Every HL trade → feed to ALL 4 builders simultaneously
  Each builder emits closed candles → append to respective tf_state.klines

BOOTSTRAP PROBLEM:
  Synthetic klines start empty. On startup:
  1. Bootstrap from Binance Futures REST (provides immediate history)
  2. Start synthetic builders
  3. After builders have produced candles covering the full lookback window
     (e.g., 150 × 1m = 150 minutes for 5m TF), synthetic becomes authoritative
  4. Gradually, the oldest Binance-sourced klines age out of the deque
     and are replaced by synthetic klines

NOTE ON VOLUME:
  Synthetic kline volume is in BASE asset units (BTC), derived from HL trade sizes.
  This is actual Hyperliquid volume — NOT Binance volume.
  All volume-dependent indicators (VWAP, POC, CVD) now use native HL volume.
```

### Updated Project Structure
```
bayesmarket/
├── feeds/
│   ├── __init__.py
│   ├── hyperliquid.py     # HL WebSocket: l2Book, trades
│   ├── binance.py         # Binance FUTURES WebSocket + REST (FALLBACK)
│   └── synthetic.py       # NEW: Synthetic kline builder from HL trades
```

---

## PATCH #2: Wall Detection — Price Binning with $10 Range

### Apa yang berubah
Blueprint v2 tracks walls at EXACT price levels. In BTC markets, HFT market makers
requote every 100-500ms at slightly different prices. A genuine $5M buy wall might
shift between $84,000 and $84,005 continuously — the old logic would see this as
the wall "disappearing" and "reappearing" every 200ms, never reaching 15s persistence.

### Override: config.py
```python
# REPLACE wall-related config:

# Wall detection uses PRICE BINS, not exact prices
WALL_BIN_SIZE = 10.0                 # Group levels into $10 bins for wall detection
                                     # Example: $83,995 and $84,004 are in same bin ($84,000)
WALL_PERSISTENCE_SECONDS = 5.0       # Reduced from 15s to 5s (with binning, this is sufficient)
WALL_MIN_SIZE_MULTIPLIER = 3.0       # Unchanged: wall bin total >= 3× avg bin total
WALL_SL_OFFSET_PCT = 0.05           # Unchanged: SL placed 0.05% beyond wall bin edge
```

### Override: WallInfo dataclass in `data/state.py`
```python
@dataclass
class WallInfo:
    bin_center: float          # Center of price bin (e.g., $84,000 for bin $83,995-$84,005)
    bin_low: float             # Lower edge of bin
    bin_high: float            # Upper edge of bin
    total_size: float          # Sum of all level sizes within this bin
    side: str                  # "bid" or "ask"
    first_seen: float          # When this bin first exceeded wall threshold
    last_seen: float           # Last update time
    initial_size: float        # Size when first classified as wall
    peak_size: float           # Maximum size observed

    @property
    def age_seconds(self) -> float:
        return time.time() - self.first_seen

    @property
    def size_ratio(self) -> float:
        """Current vs initial. Below 0.5 = wall decaying significantly."""
        return self.total_size / self.initial_size if self.initial_size > 0 else 0

    @property
    def is_valid(self) -> bool:
        return self.age_seconds >= 5.0 and self.size_ratio >= 0.5
```

### Override: Wall Tracker Logic (Section 9.3)
```
On every l2Book update:
  1. Create price bins of WALL_BIN_SIZE ($10) for both sides:
     For bids: group all bid levels where price falls in same $10 range
       bin_center = floor(level.price / 10) * 10 + 5
       bin_low = floor(level.price / 10) * 10
       bin_high = bin_low + 10
       Accumulate: bin.total_size += level.size

     Same for asks.

  2. Compute average bin total:
     avg_bin_size = mean(all non-zero bin totals from both sides)
     threshold = avg_bin_size × WALL_MIN_SIZE_MULTIPLIER (3.0)

  3. For each bin:
     IF bin.total_size >= threshold:
       IF bin already tracked in state.tracked_walls:
         Update: last_seen = now, total_size = current total
         Update: peak_size = max(peak_size, current total)
       ELSE:
         Create new WallInfo with first_seen = now, initial_size = current total
     ELSE:
       IF bin was tracked: remove from tracked_walls

  4. Prune: remove walls not updated in last 3 seconds (missed 6+ l2Book pushes)

  5. Wall is "valid for SL" if:
     wall.age_seconds >= 5.0 AND wall.size_ratio >= 0.5

EXAMPLE:
  Bids at: $83,998 (0.5 BTC), $84,001 (1.2 BTC), $84,003 (0.8 BTC)
  All fall in bin $84,000 ($83,995 - $84,005, center $84,000)
  Bin total = 2.5 BTC
  If avg bin = 0.5 BTC, threshold = 1.5 BTC → 2.5 > 1.5 → WALL detected
  Even if MM requotes $84,001→$84,002 next update, bin total barely changes
```

---

## PATCH #3: SL Tighten Logic — Structural Only, No Wall Chase

### Apa yang berubah
Blueprint v2 had `SL_ONLY_TIGHTENS = True` which would auto-tighten SL when ANY new
wall appeared closer to current price. This creates a spoofing vulnerability: a fake
wall appears, SL tightens to it, wall vanishes, price dips slightly, SL triggered,
whipsawed out of a winning trade.

### Override: SL Management Rules (COMPLETE REPLACEMENT of Section 8 SL monitoring)

```
SL MANAGEMENT RULES (after entry):

RULE 1: SL NEVER moves based on NEW walls appearing after entry.
  - The initial SL is set at entry based on the wall/POC/ATR that existed AT ENTRY TIME.
  - New walls that appear AFTER entry are IGNORED for SL purposes.
  - Log: "New wall detected at $X — ignored for SL (post-entry)"

RULE 2: SL can tighten ONLY on structural price shift (swing low/high).
  For LONG positions:
    - A "structural higher low" is confirmed when:
      a. Price makes a local low (drops then rises again)
      b. That low is HIGHER than the previous swing low
      c. Price has moved at least 0.3% above that low (confirmation)
    - When confirmed: SL can move UP to just below this new higher low
    - But NEVER closer than 0.5 × ATR from current price (minimum breathing room)

  For SHORT positions:
    - Mirror logic: structural lower high confirmed → SL can move DOWN

RULE 3: SL tightens on BASIS WALL DECAY (the wall SL was originally based on).
  - If the ORIGINAL wall (the one SL was set from) decays:
    - Wall size drops below 50% of initial: WARNING state
    - Wall size drops below 25% of initial OR wall disappears entirely:
      → Execute fallback chain: search nearby bins for new wall → POC → ATR
      → New SL MUST be tighter or equal to current SL (never wider)
      → If all fallbacks yield wider SL than current: keep current SL

RULE 4: MINIMUM SL DISTANCE
  - SL can never be closer than 0.3 × ATR(14) from current price
  - This prevents whipsaw from micro-fluctuations
  - If a structural shift would place SL closer than this: cap at 0.3 × ATR
```

### Override: config.py additions
```python
# SL management
SL_ONLY_TIGHTENS = True                    # Unchanged in principle
SL_TIGHTEN_MODE = "structural"             # NEW: "structural" = only on swing low/high shifts
                                           # NOT on new walls appearing
SL_MIN_DISTANCE_ATR_MULT = 0.3            # NEW: SL never closer than 0.3 × ATR from price
SL_STRUCTURAL_CONFIRMATION_PCT = 0.003     # NEW: 0.3% move above swing low to confirm
```

---

## PATCH #4: Order Type Strategy — For Future LIVE_MODE

### NEW Section: Order Execution Specification

```
This section defines order types for when LIVE_MODE = True.
In SHADOW_MODE, all fills are simulated at mid_price.

ENTRY ORDERS:
  Type: Limit Order (Post-Only / ALO — Add Liquidity Only)
  Price: mid_price (or 1 tick better than best bid for LONG, best ask for SHORT)
  Behavior:
    - Post-only ensures maker fee (0.015%) not taker (0.045%)
    - If order would immediately match (cross the spread): cancel, don't fill
    - If not filled within 5 seconds: cancel and re-place at updated mid_price
    - Max re-attempts: 3
    - If still not filled after 3 attempts: abort entry, log "entry_failed_no_fill"
    - NEVER chase with market order for entry

STOP LOSS ORDERS:
  Type: Stop Market Order
  Trigger: mark price (not last trade price — HL uses mark for liquidations too)
  Rationale: SL MUST guarantee exit. Maker savings irrelevant when protecting capital.
  Fee: 0.045% taker — accepted cost of protection.
  Placed: Immediately after entry fill confirmed.

TAKE PROFIT ORDERS:
  Type: Limit Order (GTC — Good Til Canceled)
  TP1: Limit sell/buy at tp1_price, size = 60% of position
  TP2: Limit sell/buy at tp2_price, size = 40% of position
  Both placed immediately after entry fill.

PARTIAL FILL HANDLING:
  - TP limit orders may partially fill (e.g., only 40% of TP1 size filled)
  - If TP1 partially fills:
    - Filled portion: recorded as realized PnL
    - Remaining TP1 amount: stays as resting limit order
    - After 60 seconds with no additional fill: cancel remaining, replace with IOC
      (Immediate-or-Cancel — fill what you can, cancel rest)
    - After IOC: if still unfilled, accept as market order (last resort)
  - Log every partial fill event

ORDER TRACKING:
  - Every order gets a client_order_id (cloid) for tracking
  - Subscribe to HL WebSocket: {"type": "orderUpdates", "user": address}
  - Track states: placed → resting → partially_filled → filled → canceled
  - If HL API returns error on order placement: retry once, then abort + log

CANCEL-ON-DISCONNECT:
  - If HL WebSocket disconnects while position is open:
    - SL order is already on-chain (persists through disconnect) ✓
    - TP orders are already on-chain (persist) ✓
    - No action needed — orders live on L1 independent of our connection
    - On reconnect: verify all orders still active via REST query
```

### Override: config.py additions
```python
# Order execution (LIVE_MODE only)
ENTRY_ORDER_TYPE = "limit_post_only"       # ALO — maker fee
ENTRY_ORDER_TIMEOUT_SECONDS = 5            # Cancel + re-place if not filled
ENTRY_ORDER_MAX_RETRIES = 3                # Max re-attempts before aborting
SL_ORDER_TYPE = "stop_market"              # Guaranteed fill
TP_ORDER_TYPE = "limit_gtc"                # Passive, collect maker rebate
TP_PARTIAL_FILL_TIMEOUT = 60               # Seconds before converting partial to IOC
MAX_LEVERAGE = 5.0                         # Hard cap on leverage
```

---

## PATCH #5: Capital, Leverage, and Position Sizing

### Override: config.py
```python
# Capital and leverage
SHADOW_STARTING_CAPITAL = 1000.0           # $1,000 for shadow mode simulation
MAX_LEVERAGE = 5.0                         # Max 5× leverage
                                           # With $1,000: max notional = $5,000

# Position sizing interaction with leverage:
# risk_amount = $1,000 × 0.02 = $20 max loss per trade
# If ATR(14) = $300, SL = 1.5 × $300 = $450 from entry
# Position size = $20 / $450 = 0.044 BTC ≈ $3,700 notional at $84K
# Leverage required = $3,700 / $1,000 = 3.7× — within 5× limit ✓
#
# MERGED position (2× size): 0.088 BTC ≈ $7,400 notional
# Leverage required = $7,400 / $1,000 = 7.4× — EXCEEDS 5× limit ✗
# → Merged size gets CAPPED at $5,000 / $84,000 = 0.0595 BTC
#
# RULE: Final position notional = min(calculated_size × price, capital × MAX_LEVERAGE)
```

### Updated Position Sizing Formula
```
FUNCTION calculate_position_size(capital, entry, sl, state, is_merged):
  # Step 1: Risk-based sizing
  risk = capital × MAX_RISK_PER_TRADE                    # $20
  sl_distance = abs(entry - sl)                          # e.g., $450
  risk_based_size = risk / sl_distance                   # e.g., 0.044 BTC

  # Step 2: Apply modifiers
  IF cooldown_active: risk_based_size × 0.5
  IF funding_tier == "caution": risk_based_size × 0.75
  IF is_merged: risk_based_size × MERGE_MAX_SIZE_MULTIPLIER (2.0)

  # Step 3: Cap by leverage limit
  max_notional = capital × MAX_LEVERAGE                  # $1,000 × 5 = $5,000
  max_size_by_leverage = max_notional / entry            # $5,000 / $84,000 = 0.0595 BTC
  final_size = min(risk_based_size, max_size_by_leverage)

  # Step 4: Check minimum
  IF final_size × entry < MIN_ORDER_VALUE_USD:           # < $10
    SKIP TRADE

  RETURN final_size
```

---

## PATCH #6: PnL Monitoring and Reporting

### Dashboard (already in blueprint v2 Section 11)
Bottom bar already shows real-time PnL. No change needed for dashboard.

### NEW: CLI Report Tool — `report.py`

```
PURPOSE: Command-line tool to query SQLite and print performance stats.
Run anytime, even while main bot is running (SQLite supports concurrent reads).

USAGE:
  python -m bayesmarket.report                    # Today's summary
  python -m bayesmarket.report --period 7d        # Last 7 days
  python -m bayesmarket.report --period all       # All time
  python -m bayesmarket.report --detail           # Show every trade
  python -m bayesmarket.report --signals          # Signal distribution analysis

OUTPUT FORMAT (example):

  ══════════════════════════════════════════════════════
  BAYESMARKET PERFORMANCE REPORT — Last 7 Days
  ══════════════════════════════════════════════════════

  SUMMARY
    Period:              2026-03-07 to 2026-03-13
    Total trades:        47
    Win rate:            55.3% (26W / 21L)
    Profit factor:       1.42
    Net PnL:             +$82.30 (+8.23%)
    Avg PnL per trade:   +$1.75 (+0.18%)
    Max drawdown:        -$34.20 (-3.42%)
    Avg trade duration:  14m 22s

  BY SOURCE
    5m only:     22 trades | 50.0% WR | PF 1.15 | +$18.40
    15m only:    18 trades | 61.1% WR | PF 1.83 | +$52.90
    Merged:       7 trades | 57.1% WR | PF 1.61 | +$11.00

  BY EXIT REASON
    TP1 hit:     31 (66%)
    TP2 hit:     12 (26%)
    SL hit:       4 (8%)

  SL BASIS DISTRIBUTION
    Wall-based:  28 (60%)
    POC-based:   14 (30%)
    ATR-based:    5 (10%)

  REGIME PERFORMANCE
    Trending:    32 trades | 59.4% WR | +$71.20
    Ranging:     15 trades | 46.7% WR | +$11.10

  RISK EVENTS
    Cooldowns triggered:      2
    Daily limit hit:          0
    Full stops:               0
    MTF blocks:              34 (signals blocked by MTF filter)
    Funding blocks:           3

  SIGNAL STATISTICS (from signals table)
    Avg total score at entry: +8.4 / -8.1
    Score distribution:       [histogram of all signal scores]
    Signals generated:        312
    Signals executed:          47 (15.1% execution rate)
    Signals blocked:          265 (MTF: 34, Funding: 3, Risk: 8, Neutral: 220)

  ══════════════════════════════════════════════════════
```

### SQLite Queries backing the report
```sql
-- Win rate
SELECT
  COUNT(*) as total,
  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
  SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses
FROM trades
WHERE entry_time >= ?;

-- Profit factor
SELECT
  SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_profit,
  ABS(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END)) as gross_loss
FROM trades
WHERE entry_time >= ?;

-- By source
SELECT merge_type, COUNT(*), AVG(pnl), SUM(pnl)
FROM trades WHERE entry_time >= ?
GROUP BY merge_type;

-- Signal distribution
SELECT
  signal,
  COUNT(*) as count,
  AVG(total_score) as avg_score
FROM signals
WHERE timeframe IN ('5m', '15m') AND timestamp >= ?
GROUP BY signal;
```

---

## PATCH #7: Shadow Mode Improvement Loop Framework

### NEW Section: Continuous Improvement Protocol

```
PURPOSE:
Shadow mode is NOT just "watch and hope." It's a structured validation process
with specific checkpoints, metrics to evaluate, and actionable decisions at each gate.

═══════════════════════════════════════════════════════════════
PHASE 1: STABILITY (Day 1-3)
═══════════════════════════════════════════════════════════════

GOAL: Verify all system components work correctly.

DAILY CHECKLIST:
  □ All WebSocket feeds connected >95% of the time (check events table)
  □ Signal computation running at correct intervals (1s for exec TFs)
  □ Synthetic klines matching Binance Futures within ±$1 (cross-validate)
  □ Wall detection firing (are walls being found? check tracked_walls log)
  □ No memory leaks (RSS stable over 24h)
  □ SQLite growth rate acceptable

METRICS TO TRACK:
  - Feed uptime % per connection
  - Signal computation latency (time to compute all 9 indicators)
  - Synthetic kline vs Binance Futures divergence (should be < $2)
  - Number of walls detected and avg persistence

DECISION GATE:
  IF all checklists pass → proceed to Phase 2
  IF stability issues → fix bugs, restart Phase 1 counter

═══════════════════════════════════════════════════════════════
PHASE 2: SIGNAL QUALITY (Day 4-10)
═══════════════════════════════════════════════════════════════

GOAL: Evaluate whether signals have predictive value.

ANALYSIS (run daily via report tool):
  1. SCORE DISTRIBUTION: What's the histogram of total_score?
     Expected: roughly normal around 0, with tails at ±7+
     Red flag: if scores cluster near 0 (no conviction) or always max (overfitting)

  2. SIGNAL → OUTCOME CORRELATION:
     For every LONG signal (score >= threshold):
       What happened to price in the next 15 minutes?
       Calculate: hit_rate = % of times price moved at least 0.3% in signal direction
     Expected: >52% hit rate
     Red flag: <48% (worse than random)

  3. CATEGORY CONTRIBUTION:
     Which category (A/B/C) contributes most to winning vs losing trades?
     Expected: Category A (order flow) should have highest correlation with winners
     Red flag: Category C (lagging) dominates winners → signals are too late

  4. MTF FILTER EFFECTIVENESS:
     Compare: win rate of signals WITH MTF alignment vs signals that WOULD HAVE
     triggered but were blocked by MTF filter.
     Expected: blocked signals should have lower theoretical win rate
     Red flag: blocked signals would have been profitable → filter too aggressive

  5. REGIME DETECTION ACCURACY:
     Compare: win rate in "trending" vs "ranging" regimes
     Expected: trending > ranging (this is a trend-following system at core)
     Red flag: no difference → regime detection not adding value

ACTIONABLE ADJUSTMENTS (make ONE change at a time, observe for 2+ days):

  a. THRESHOLD TUNING:
     If too many losing trades: raise threshold by 0.5 (7.0 → 7.5)
     If too few trades (< 2/day): lower threshold by 0.5 (7.0 → 6.5)
     Never adjust by more than 1.0 at a time

  b. WEIGHT REBALANCING:
     If Category A consistently predicts winners better than B/C:
       Consider: increase Category A weights by 0.5, decrease C by 0.5
     Rule: total max score must stay at ±13.5 (rebalance, don't inflate)
     Never change weights during first 7 days — need sufficient sample

  c. MTF FILTER LOOSENING:
     If MTF blocks >40% of signals AND blocked signals would have been profitable:
       Option: change MTF from VWAP to "price above/below 50-period EMA on filter TF"
       Option: relax to "allow if filter TF score is neutral (not opposing)"

  d. WALL DETECTION TUNING:
     If wall-based SL never triggers (always falls back to POC/ATR):
       Increase bin size ($10 → $20) or decrease persistence (5s → 3s)
     If wall-based SL triggers but walls frequently decay after entry:
       Increase min size multiplier (3× → 5×) — only track mega walls

DECISION GATE:
  IF signal hit rate > 52% AND profitable in simulation → proceed to Phase 3
  IF signal hit rate 48-52% → continue Phase 2 for another week with adjustments
  IF signal hit rate < 48% → fundamental reassessment needed (see below)

═══════════════════════════════════════════════════════════════
PHASE 3: RISK VALIDATION (Day 11-21)
═══════════════════════════════════════════════════════════════

GOAL: Verify risk management protects capital as designed.

ANALYSIS:
  1. POSITION SIZING: Did any single trade exceed 2% loss?
     Expected: never (by design)
     Red flag: if yes → bug in sizing formula

  2. DAILY LIMIT: Was 7% daily limit ever approached?
     Expected: hit 0-1 times in 10 days (normal variance)
     Red flag: hit >3 times → system overtrades in losing conditions

  3. COOLDOWN: How many cooldown events? Did cooldown prevent further losses?
     Measure: PnL of first 3 trades after cooldown reset
     Expected: at least break-even (market regime shifted during cooldown)

  4. DRAWDOWN CURVE: Plot cumulative PnL over time
     Expected: some losing streaks but generally upward or flat
     Red flag: persistent downtrend → edge doesn't exist

  5. MERGED vs SINGLE: Do merged trades outperform?
     Expected: merged should have higher win rate (dual TF conviction)
     Red flag: merged worse than single → merge logic flawed

  6. SL ANALYSIS:
     - What % of SL exits later would have been winners? (regret analysis)
       Expected: <30% — SL correctly cut losers
       Red flag: >50% — SL too tight, cutting winners
     - Avg SL distance vs avg favorable move before reversal
       If SL distance is close to avg favorable move → SL too tight

ACTIONABLE ADJUSTMENTS:
  a. If SL too tight (>50% regret):
     Increase ATR multiplier: 1.5 → 2.0
     Or increase min SL distance: 0.3 × ATR → 0.5 × ATR

  b. If drawdown too large but win rate is OK:
     Reduce MAX_RISK_PER_TRADE: 2% → 1.5%
     Or reduce MAX_LEVERAGE: 5× → 3×

  c. If merged trades underperform:
     Disable merge temporarily — run as two independent single-TF bots
     Compare performance

DECISION GATE:
  IF 21-day simulation profitable AND max drawdown < 15% → ready for live consideration
  IF profitable but drawdown > 15% → reduce position sizing, extend Phase 3
  IF not profitable after 21 days with adjustments → return to design phase

═══════════════════════════════════════════════════════════════
IMPROVEMENT LOG FORMAT
═══════════════════════════════════════════════════════════════

Maintain a CHANGELOG.md file tracking every adjustment:

  ## [Day 8] - 2026-03-21
  ### Observation
  MTF filter blocking 45% of 5m signals. Analysis shows 60% of blocked signals
  would have been profitable.

  ### Change
  Relaxed MTF filter for 5m: from "price > 1h VWAP" to "1h total score > -3"
  (allow 5m LONG even if slightly below 1h VWAP, as long as 1h isn't strongly bearish)

  ### Config Changed
  MTF_FILTER_MODE = "score_threshold" (was "vwap_direction")
  MTF_SCORE_BLOCK_THRESHOLD = -3.0

  ### Result (observed over next 3 days)
  5m signal execution rate: 15% → 22%
  5m win rate: 53% → 51% (slight decrease — more signals, slightly lower quality)
  5m net PnL: +$12 → +$18 (net positive — more trades compensate lower WR)

  ### Decision
  KEEP this change.
```

---

## UPDATED PROJECT STRUCTURE (with all patches)

```
bayesmarket/
├── config.py              # All parameters (updated with patches 1-5)
├── main.py                # Entry point
│
├── feeds/
│   ├── __init__.py
│   ├── hyperliquid.py     # HL WebSocket: l2Book, trades
│   ├── binance.py         # Binance FUTURES WebSocket + REST (FALLBACK)
│   └── synthetic.py       # NEW: Synthetic kline builder from HL trades
│
├── indicators/
│   ├── __init__.py
│   ├── order_flow.py      # CVD (Z-Score), OBI, Liquidity Depth
│   ├── structure.py       # VWAP, POC, Heikin Ashi
│   ├── momentum.py        # RSI, MACD, EMA
│   ├── regime.py          # ATR, regime detection
│   └── scoring.py         # Composite scoring
│
├── engine/
│   ├── __init__.py
│   ├── timeframe.py       # TimeframeEngine (one per TF)
│   ├── merge.py           # Smart merge
│   ├── executor.py        # Entry/exit pipeline, SL/TP management
│   └── position.py        # Position tracking
│
├── risk/
│   ├── __init__.py
│   ├── sizing.py          # Position sizing (with leverage cap)
│   ├── limits.py          # Daily loss, cooldown, circuit breakers
│   └── funding.py         # Funding rate filter
│
├── data/
│   ├── __init__.py
│   ├── state.py           # All dataclasses (updated WallInfo with bins)
│   ├── storage.py         # SQLite
│   └── recorder.py        # Market snapshot recorder
│
├── dashboard/
│   ├── __init__.py
│   └── terminal.py        # Full Rich 4-panel dashboard
│
├── report.py              # NEW: CLI performance report tool
├── requirements.txt
├── .env.example
└── CHANGELOG.md           # NEW: Improvement log during shadow mode
```

---

## SUMMARY OF ALL PATCHES

| # | Issue | Fix | Severity |
|---|-------|-----|----------|
| 1 | Binance Spot klines vs HL Perp prices | Synthetic klines from HL trades (primary) + Binance Futures (fallback) | FATAL → FIXED |
| 2 | Wall persistence 15s too long for BTC HFT | Price binning $10 range, persistence 5s | HIGH → FIXED |
| 3 | SL auto-tighten to new walls = spoofing trap | SL tightens only on structural swing shift, never on new post-entry walls | HIGH → FIXED |
| 4 | No order type specification | Limit ALO entry, Stop Market SL, Limit GTC TP, partial fill handling | MEDIUM → FIXED |
| 5 | No leverage/capital spec | $1,000 capital, 5× max leverage, leverage caps merged positions | MEDIUM → FIXED |
| 6 | No easy PnL monitoring | CLI report tool with comprehensive stats from SQLite | LOW → FIXED |
| 7 | No improvement framework | 3-phase validation protocol with specific metrics and decision gates | HIGH → FIXED |
