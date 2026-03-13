# CLAUDE.md — BayesMarket MVP Build Instructions

## Language Convention
- **Conversation & discussion:** Bahasa Indonesia
- **Code, comments, variable names, commit messages, logs, documentation:** English
- **This file:** English (it's a code instruction file)

---

## Project Overview

BayesMarket is an automated perpetual futures trading engine for Hyperliquid (BTC-PERP).
It runs in **shadow mode** — computing real-time signals from live mainnet data, simulating
trades, and logging everything to SQLite — without placing actual orders.

The core philosophy is **accuracy over speed**. The system is designed to be RIGHT when it
acts, not FAST. It rejects ~85% of signals through multi-layer conviction filters
(scoring threshold + MTF alignment + regime detection + funding filter). When it does act,
conviction is high.

---

## Source of Truth

There are exactly **three specification documents**. Read ALL in order before writing any code.
Later documents OVERRIDE earlier documents where they conflict.

1. **`BAYESMARKET_MVP_BLUEPRINT_v2.md`** — Complete system specification
   - Architecture, data models, all 9 indicator formulas, execution engine,
     risk management, feeds, database schema, dashboard layout
   - Sections 1-17

2. **`BAYESMARKET_BLUEPRINT_v2_ERRATA.md`** — Critical patches (7 fixes)
   - Patch #1: Synthetic klines from HL trades (replaces Binance Spot klines)
   - Patch #2: Wall detection with $10 price binning + 5s persistence
   - Patch #3: SL tighten logic — structural only, no wall chasing
   - Patch #4: Order type strategy (ALO entry, Stop Market SL, Limit GTC TP)
   - Patch #5: $1,000 capital, 5× leverage cap, position sizing formula
   - Patch #6: CLI report tool (`report.py`)
   - Patch #7: 3-phase improvement loop framework + CHANGELOG.md

3. **`BAYESMARKET_ERRATA_SUPPLEMENT.md`** — Config & Live Mode fixes
   - Supplement A: Capital auto-detection from HL account (not manual)
   - Supplement B: Final order type strategy (limit everywhere, market SL only)
   - Supplement C: .env.example final version
   - Supplement D: Live mode activation checklist

**Priority: Supplement > Errata > Blueprint. Latest document wins.**

---

## Project Structure

```
bayesmarket/
├── config.py              # All configurable constants and parameters
├── main.py                # Entry point — async orchestration
│
├── feeds/
│   ├── __init__.py
│   ├── hyperliquid.py     # HL WebSocket: l2Book, trades (shared for all TFs)
│   ├── binance.py         # Binance FUTURES WebSocket + REST (FALLBACK only)
│   └── synthetic.py       # Synthetic kline builder from HL trade stream
│
├── indicators/
│   ├── __init__.py
│   ├── order_flow.py      # CVD (Z-Score + tanh), OBI, Liquidity Depth
│   ├── structure.py       # VWAP, POC (Volume Profile), Heikin Ashi
│   ├── momentum.py        # RSI, MACD, EMA — all proportional scoring
│   ├── regime.py          # ATR(14), regime detection (trending/ranging)
│   └── scoring.py         # Composite bias score aggregation per TF
│
├── engine/
│   ├── __init__.py
│   ├── timeframe.py       # TimeframeEngine class — one instance per TF
│   ├── merge.py           # Smart merge logic for 5m+15m signal conflicts
│   ├── executor.py        # Entry/exit pipeline, SL/TP management
│   └── position.py        # Position state tracking, partial exits
│
├── risk/
│   ├── __init__.py
│   ├── sizing.py          # Position sizing (2% rule + leverage cap)
│   ├── limits.py          # Daily loss limit, cooldown, circuit breakers
│   └── funding.py         # Funding rate fetch + 3-tier filter
│
├── data/
│   ├── __init__.py
│   ├── state.py           # MarketState, TimeframeState, SignalSnapshot, etc.
│   ├── storage.py         # SQLite interface — create tables, insert, query
│   └── recorder.py        # Market snapshot recorder (every 10s)
│
├── dashboard/
│   ├── __init__.py
│   └── terminal.py        # Full Rich terminal — 4-panel split screen
│
├── report.py              # CLI performance report tool
├── requirements.txt
├── .env.example           # Template — credentials optional for shadow mode
└── CHANGELOG.md           # Improvement log (starts empty)

# EXTERNAL (lives outside project, not built by Claude Code):
# gate_checker.py          # Standalone validation tool — reads bayesmarket.db
```

---

## Build Order

Build files in this exact sequence. Each phase should be runnable/testable before
moving to the next.

### Phase 1: Foundation
Build these first. They have zero dependencies on other project files.

```
1. requirements.txt        — Copy from blueprint Section 3
2. .env.example             — Copy from errata Patch #1
3. config.py                — Merge blueprint Section 4 + all errata config overrides
4. data/state.py            — All dataclasses from blueprint Section 5 + errata Patch #2 (WallInfo with bins)
5. data/storage.py          — SQLite schema from blueprint Section 12, init/insert/query functions
```

**Test:** `python -c "from data.state import MarketState; print('OK')"`

### Phase 2: Data Feeds
These connect to live services. Build and test individually.

```
6. feeds/hyperliquid.py     — HL WebSocket: l2Book + trades subscriptions
                              Include wall tracker with price binning (errata Patch #2)
                              Blueprint Section 9.1 + 9.3
7. feeds/synthetic.py       — Synthetic kline builder from HL trades
                              Errata Patch #1 (NEW section)
8. feeds/binance.py         — Binance FUTURES klines (fallback)
                              Errata Patch #1 (overrides blueprint Section 9.2)
```

**Test:** Run each feed standalone, print incoming messages for 30 seconds.
Verify: l2Book updates arrive ~every 0.5s, trades stream continuously,
synthetic klines close at correct interval boundaries.

### Phase 3: Indicators
Pure computation — no I/O, no side effects. Easy to unit test.

```
9.  indicators/order_flow.py  — CVD (z-score + tanh), OBI, Liquidity Depth
                                Blueprint Section 6.1, 6.2, 6.3
10. indicators/structure.py   — VWAP, POC, Heikin Ashi
                                Blueprint Section 6.4, 6.5, 6.6
11. indicators/momentum.py    — RSI, MACD, EMA (all proportional)
                                Blueprint Section 6.7, 6.8, 6.9
12. indicators/regime.py      — ATR, regime detection
                                Blueprint Section 6.10
13. indicators/scoring.py     — Composite score, signal generation
                                Blueprint Section 6.11
```

**Test:** Create mock klines/orderbook data, verify each indicator returns
values within documented bounds. E.g., `assert -2.0 <= cvd_score <= 2.0`.

### Phase 4: Engine
Core decision logic. Depends on indicators and data feeds.

```
14. engine/timeframe.py     — TimeframeEngine: computes all signals for one TF
                              Orchestrates indicators, produces SignalSnapshot
15. engine/position.py      — Position tracking, partial exit handling
16. engine/merge.py         — Smart merge: 4 cases (neither, one, same, opposite)
                              Blueprint Section 7
17. engine/executor.py      — Entry pipeline, SL/TP determination, position monitoring
                              Blueprint Section 8 + errata Patch #3 (SL logic) + Patch #4 (order types)
```

**Test:** Feed recorded/mock data through TimeframeEngine, verify SignalSnapshot
fields populated correctly. Test merge logic with all 4 conflict cases.

### Phase 5: Risk
Safety layer. Must be bulletproof.

```
18. risk/sizing.py          — Position sizing with leverage cap
                              Errata Patch #5
19. risk/limits.py          — Daily loss, cooldown state machine, full stop
                              Blueprint Section 10.2
20. risk/funding.py         — Funding rate fetch + 3-tier evaluation
                              Blueprint Section 10.3
```

**Test:** Simulate sequences of wins/losses, verify cooldown triggers at 3 losses,
resets at 2 wins or 1 hour. Verify daily limit triggers at 7%. Verify leverage cap
prevents oversized merged positions.

### Phase 6: Dashboard & Reporting

```
21. dashboard/terminal.py   — Full Rich 4-panel split screen
                              Blueprint Section 11 (ASCII mockup is the spec)
22. report.py               — CLI performance report
                              Errata Patch #6
23. data/recorder.py        — Market snapshot recorder (every 10s to SQLite)
```

**Test:** Run dashboard with mock data, verify 4 panels render. Run report
against empty DB, verify no crash. Run report against seeded test data.

### Phase 7: Main Orchestration

```
24. main.py                 — Entry point, async task orchestration
                              Blueprint Section 13
25. CHANGELOG.md            — Empty file with header template
```

**Test:** Full integration test — run `python main.py`, verify:
- All WebSocket feeds connect
- Dashboard renders with live data
- Signals compute every 1s (exec TFs) / 3s (filter TFs)
- Shadow trades get logged to SQLite
- `python report.py` shows results

---

## Critical Implementation Rules

### Rule 1: No Binary Indicators
EVERY indicator outputs a proportional score with gradation. There is no indicator
that outputs only +MAX or -MAX. Check blueprint Section 6 for exact formulas.

Wrong: `vwap_score = 1.5 if price > vwap else -1.5`
Right: `vwap_score = clamp((price - vwap) / vwap * 150, -1.5, 1.5)`

### Rule 2: No Bare Except
Every exception handler must log the error with context. See blueprint Section 15.

Wrong: `except: pass`
Right: `except Exception as e: logger.error("l2book_parse_failed", error=str(e), raw=msg)`

### Rule 3: Errata Overrides Blueprint
When in doubt, errata wins. Key overrides:
- Kline source: synthetic from HL trades, NOT Binance Spot (Patch #1)
- Wall detection: price binning $10, persistence 5s (Patch #2)
- SL logic: structural tighten only, no wall chasing (Patch #3)
- WallInfo dataclass: uses bin_center/bin_low/bin_high, NOT exact price (Patch #2)

### Rule 4: Shadow Mode = No Credentials
The system must run fully functional without HL_PRIVATE_KEY and HL_ACCOUNT_ADDRESS.
All data feeds (l2Book, trades, metaAndAssetCtxs) are public endpoints.
If LIVE_MODE is False, no code path should attempt to use credentials.

### Rule 5: Structural SL, Not Reactive SL
SL after entry:
- NEVER tightens because a new wall appeared after entry
- ONLY tightens on confirmed structural swing low/high shift
- ONLY escalates fallback when the ORIGINAL basis wall decays
- Minimum distance: 0.3 × ATR from current price
See errata Patch #3 for complete rules.

### Rule 6: Smart Merge Specifics
- Same direction merge: size = sum of both (capped by leverage limit)
- Opposite direction: 15m wins (higher TF = higher conviction)
- Merged position: SL from tighter of two, TP from 15m (more structural)
- Max 1 position at any time
See blueprint Section 7.

### Rule 7: Position Sizing Always Respects Leverage Cap
```
final_size = min(risk_based_size, capital * MAX_LEVERAGE / entry_price)
```
This applies AFTER all modifiers (cooldown, funding, merge). See errata Patch #5.

### Rule 8: Synthetic Klines Are Primary
HL trade stream → SyntheticKlineBuilder → tf_state.klines (primary)
Binance Futures klines → fallback (only when HL trades stale >10s)
Bootstrap on startup uses Binance Futures REST, then synthetic takes over.
See errata Patch #1.

### Rule 9: All Logging to SQLite
Every signal computation → signals table (every cycle, every TF)
Every trade entry/exit → trades table
Every 10 seconds → market_snapshots table
Every system event → events table
Schema in blueprint Section 12.

### Rule 10: Dashboard Is the Spec
The ASCII mockup in blueprint Section 11 IS the dashboard specification.
4 panels: top-left = 5m, top-right = 15m, bottom-left = 1h, bottom-right = 4h.
Bottom bar: position, PnL, risk state, funding, regime, source TFs.
Filter TF panels (1h, 4h) are compact with "FILTER TF" label and prominent VWAP display.

### Rule 11: Capital Auto-Detection
In live mode, capital is fetched from Hyperliquid API (user_state → accountValue)
before EVERY new trade entry. In shadow mode, capital starts at SIMULATED_CAPITAL
and compounds with simulated PnL. Never hardcode capital in execution logic.
See Errata Supplement A.

### Rule 12: Order Types Are Fixed
Entry = Limit ALO (post-only, maker fee 0.015%)
SL = Stop Market (guaranteed fill, taker fee 0.045%)
TP1/TP2 = Limit GTC (passive, maker fee 0.015%)
No other combinations. No market orders for entry or TP. See Errata Supplement B.

### Rule 13: Entry Never Chases
If limit entry doesn't fill in 5s × 3 retries = 15s, ABORT.
Never fall back to market order for entry. If we can't get a good price,
we don't trade. This is an accuracy system, not a speed system.

---

## Key Formulas Quick Reference

These are the most critical. Full details in blueprint Section 6.

```
CVD:      z = (cvd_raw - mean) / std → score = 2.0 × tanh(z / 2.0)
OBI:      score = ((bid_vol - ask_vol) / total) × 2.0
Depth:    score = ((bid_depth - ask_depth) / total) × 2.0
VWAP:     score = clamp((price - vwap) / vwap × 150, -1.5, +1.5)
POC:      score = clamp((price - poc) / poc × 150, -1.5, +1.5)
HA:       score = (streak / 3) × 1.5  where streak ∈ [-3, +3]
RSI:      score mapped linearly: 30→+1, 50→0, 70→-1
MACD:     score = clamp(histogram / atr, -1.0, +1.0)
EMA:      score = clamp((ema5 - ema20) / ema20 × 200, -1.0, +1.0)

Total:    category_a + category_b + category_c
Threshold: ±7.0 (trending) or ±8.5/9.0 (ranging)
```

---

## Async Task Map (main.py)

13 concurrent tasks via asyncio.gather:

```
# Data feeds (always running)
1.  hl_book_feed(state)             # HL l2Book WebSocket → state.bids/asks + wall tracker
2.  hl_trade_feed(state)            # HL trades WebSocket → state.trades + synthetic kline builders
3.  binance_kline_feed(state)       # Binance Futures 4-stream multiplex (fallback)

# Signal computation (per-TF intervals)
4.  signal_loop_5m(state)           # Every 1s
5.  signal_loop_15m(state)          # Every 1s
6.  signal_loop_1h(state)           # Every 3s
7.  signal_loop_4h(state)           # Every 5s

# Execution
8.  merge_and_execute_loop(state)   # Every 1s: smart merge + entry evaluation
9.  position_monitor_loop(state)    # Every 1s: SL/TP/wall health monitoring

# Risk & data
10. funding_poller(state)           # Every 60s: fetch HL funding rate
11. daily_reset_loop(state)         # Every 60s: check 00:00 UTC reset
12. snapshot_recorder(state)        # Every 10s: log market state to SQLite

# UI
13. dashboard_loop(state)           # Every 3s: render Rich terminal
```

---

## Dependencies & Environment

```bash
# Python 3.11+ required
pip install -r requirements.txt

# No .env needed for shadow mode
# To run:
python main.py
```

---

## Validation After Build

Run the system for 10 minutes and verify:

```
□ Terminal shows 4-panel dashboard with live data
□ All 4 TF panels show updating scores
□ Synthetic klines incrementing (check candle timestamps)
□ Wall detection shows walls in dashboard (or "none" if none exist)
□ SQLite file created and growing (check file size)
□ At least some LONG/SHORT signals generated (may not trigger entry if below threshold)
□ If a shadow trade occurs: check trades table in SQLite
□ Run: python report.py — should output stats (may be empty if no trades yet)
□ No errors in terminal output
□ Memory stable (not growing unbounded)
```

---

## What NOT to Build

- No web dashboard (terminal only)
- No Telegram/Discord alerts
- No Polymarket integration (deferred to v2)
- No trailing stop (TP1 + TP2 only)
- No multi-asset (BTC only)
- No backtesting engine
- No actual order placement (shadow mode only)
