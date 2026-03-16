# BAYESMARKET BLUEPRINT — ERRATA SUPPLEMENT (Config & Live Mode)

> **Dokumen ini MELENGKAPI `BAYESMARKET_BLUEPRINT_v2_ERRATA.md`.**
> Membaca urutan: Blueprint v2 → Errata → **Supplement ini.**
> Semua override di sini menggantikan konfigurasi di dokumen sebelumnya.

---

## SUPPLEMENT A: Capital Configuration — Auto-Detect, Not Manual

### Problem
Blueprint dan errata menggunakan `SHADOW_STARTING_CAPITAL = 1000.0` yang harus
diketik manual. Ketika switch ke live mode, user harus ingat mengganti field ini
ke jumlah deposit aktual. Nama field juga membingungkan saat live.

### Fix: Unified Capital Config

**REPLACE all capital-related config in `config.py` with:**

```python
# ══════════════════════════════════════════════════════════════════
# CAPITAL & MODE
# ══════════════════════════════════════════════════════════════════
LIVE_MODE = False                          # False = shadow, True = live

# Capital source depends on mode:
# - Shadow mode: uses SIMULATED_CAPITAL below (no API call)
# - Live mode: auto-fetches actual USDC balance from Hyperliquid account
#   Falls back to SIMULATED_CAPITAL if fetch fails (with warning log)

SIMULATED_CAPITAL = 1000.0                 # Used in shadow mode simulation
                                           # Also used as fallback if live balance fetch fails
```

### Implementation: Capital Resolution Logic

```python
# In engine/executor.py or main.py initialization:

import structlog
logger = structlog.get_logger()

async def resolve_capital(config, hl_client=None) -> float:
    """
    Determine trading capital based on mode.

    Shadow mode: return SIMULATED_CAPITAL (no API needed)
    Live mode: fetch actual USDC perp balance from Hyperliquid
    """
    if not config.LIVE_MODE:
        logger.info("capital_resolved",
                     mode="shadow",
                     capital=config.SIMULATED_CAPITAL)
        return config.SIMULATED_CAPITAL

    # Live mode: auto-detect from Hyperliquid account
    try:
        # Using hyperliquid-python-sdk:
        # user_state = info.user_state(config.HL_ACCOUNT_ADDRESS)
        # The response includes:
        # {
        #   "marginSummary": {
        #     "accountValue": "1234.56",    ← total account value
        #     "totalMarginUsed": "100.00",  ← margin locked in positions
        #     "totalNtlPos": "500.00",
        #     ...
        #   },
        #   "crossMarginSummary": { ... },
        #   "withdrawable": "1134.56"       ← available capital
        # }

        user_state = hl_client.user_state(config.HL_ACCOUNT_ADDRESS)
        account_value = float(user_state["marginSummary"]["accountValue"])

        if account_value <= 0:
            logger.warning("capital_zero",
                           msg="Hyperliquid account value is $0. Is the account funded?",
                           fallback=config.SIMULATED_CAPITAL)
            return config.SIMULATED_CAPITAL

        logger.info("capital_resolved",
                     mode="live",
                     account_value=account_value,
                     source="hyperliquid_api")
        return account_value

    except Exception as e:
        logger.error("capital_fetch_failed",
                      error=str(e),
                      fallback=config.SIMULATED_CAPITAL,
                      msg="Using SIMULATED_CAPITAL as fallback")
        return config.SIMULATED_CAPITAL
```

### How Capital Updates During Live Trading

```
Capital is NOT static during a live session. It changes with every trade.

RULE: Re-resolve capital from Hyperliquid account before EVERY new trade entry.
  - Before position sizing calculation:
    1. Fetch fresh user_state from HL API
    2. Use "accountValue" as current capital
    3. Position size = current_capital × 2% / sl_distance
  - This ensures:
    - After a win: capital grows, next position naturally larger (compounding)
    - After a loss: capital shrinks, next position naturally smaller (protection)
    - No manual adjustment ever needed

SHADOW MODE: Capital is tracked in-memory as state.capital.
  - Starts at SIMULATED_CAPITAL ($1,000)
  - Updated after each simulated trade: state.capital += pnl
  - This simulates the same compounding/protection behavior
```

---

## SUPPLEMENT B: Order Type Strategy — Final Specification

### Override: Errata Patch #4 (COMPLETE REPLACEMENT)

The previous errata Patch #4 specified Stop Market for SL. User has confirmed:
**ALL orders use Limit EXCEPT Stop Loss which uses Market.**

```python
# ══════════════════════════════════════════════════════════════════
# ORDER TYPES (for LIVE_MODE)
# ══════════════════════════════════════════════════════════════════

# ENTRY: Limit Post-Only (Add Liquidity Only)
# - Ensures maker fee (0.015% on Hyperliquid) not taker (0.045%)
# - If order would immediately cross spread: auto-canceled by exchange (ALO behavior)
# - Bot retries at updated price
ENTRY_ORDER_TYPE = "limit_post_only"       # Hyperliquid: {"limit": {"tif": "Alo"}}
ENTRY_ORDER_TIMEOUT_SECONDS = 5            # Cancel + re-place if not filled in 5s
ENTRY_ORDER_MAX_RETRIES = 3                # After 3 failed attempts: abort entry
ENTRY_ORDER_PRICE_OFFSET_TICKS = 1         # Place 1 tick better than best bid/ask
                                           # LONG: best_bid + 1 tick
                                           # SHORT: best_ask - 1 tick

# STOP LOSS: Stop Market Order
# - GUARANTEED fill — safety over fee savings
# - Taker fee (0.045%) — accepted cost of capital protection
# - Trigger: oracle/mark price (consistent with HL liquidation engine)
SL_ORDER_TYPE = "stop_market"              # Hyperliquid trigger order with market execution
                                           # {"trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}}

# TAKE PROFIT (TP1 & TP2): Limit GTC
# - Passive resting orders — collect maker fee
# - GTC = Good Til Canceled — stays in book until filled or manually canceled
TP_ORDER_TYPE = "limit_gtc"                # Hyperliquid: {"limit": {"tif": "Gtc"}}

# PARTIAL FILL HANDLING
TP_PARTIAL_FILL_TIMEOUT = 60               # If TP partially fills, wait 60s for rest
TP_PARTIAL_FILL_ESCALATION = "ioc"         # After timeout: send IOC for remaining
                                           # IOC = Immediate-or-Cancel
                                           # If IOC also doesn't fill remaining: leave as-is
                                           # (position is smaller, SL still protects)
```

### Order Lifecycle — Complete Flow

```
ENTRY FLOW:
  1. Signal triggers (score >= threshold, all filters pass)
  2. Calculate: entry_price = mid_price (or best_bid+1tick for LONG)
  3. Place limit ALO at entry_price
  4. Wait up to ENTRY_ORDER_TIMEOUT_SECONDS (5s)
     → If filled: proceed to step 5
     → If not filled: cancel order, re-calculate price, retry (max 3×)
     → If 3 retries exhausted: ABORT entry. Log "entry_failed_no_fill"
       Do NOT fall back to market order. If we can't get a good fill, we don't trade.
  5. Entry confirmed. Immediately place:
     a. SL stop-market order at sl_price
     b. TP1 limit GTC at tp1_price (size = 60% of position)
     c. TP2 limit GTC at tp2_price (size = 40% of position)
  6. Subscribe to orderUpdates WebSocket for fill notifications

EXIT FLOW (TP1 hit):
  1. HL WebSocket reports TP1 order filled (or partially filled)
  2. If fully filled:
     - Record: tp1_hit = True, realized PnL from TP1 portion
     - Remaining position: 40% protected by SL and TP2
     - Optionally: move SL to breakeven (entry price) — lock in guaranteed no-loss
  3. If partially filled:
     - Wait TP_PARTIAL_FILL_TIMEOUT (60s)
     - Send IOC for remaining unfilled TP1 amount
     - Whatever fills, fills. Rest stays as position.

EXIT FLOW (TP2 hit):
  1. Same as TP1 but for remaining 40%
  2. All TP2 filled → position fully closed
  3. Cancel any remaining SL order (position gone, SL no longer needed)

EXIT FLOW (SL hit):
  1. HL WebSocket reports SL trigger order executed
  2. Record loss
  3. Cancel any remaining TP1/TP2 limit orders
  4. Update risk state (consecutive losses, cooldown check)

DISCONNECT SAFETY:
  - All orders (SL, TP1, TP2) live on Hyperliquid L1 on-chain
  - They persist even if our bot disconnects
  - On reconnect: query open orders via REST to verify state
  - Log: "reconnected — verified N orders still active"
```

### Fee Impact Analysis

```
Scenario: BTC trade, $3,000 notional position

ENTRY (limit ALO):     $3,000 × 0.015% = $0.45  (maker)
SL hit:                $3,000 × 0.045% = $1.35  (taker — market order)
Total round-trip (loss): $1.80

ENTRY (limit ALO):     $3,000 × 0.015% = $0.45  (maker)
TP1 (limit GTC, 60%):  $1,800 × 0.015% = $0.27  (maker)
TP2 (limit GTC, 40%):  $1,200 × 0.015% = $0.18  (maker)
Total round-trip (win): $0.90

Savings vs all-market-order approach:
  All market: $3,000 × 0.045% × 2 = $2.70 round-trip
  Our approach (win): $0.90 — saves $1.80 per winning trade (67% fee reduction)
  Our approach (loss): $1.80 — saves $0.90 per losing trade (33% fee reduction)
```

---

## SUPPLEMENT C: .env.example — Final Version

```bash
# ════════════════════════════════════════════════════════
# BayesMarket Environment Configuration
# ════════════════════════════════════════════════════════

# ── MODE ──────────────────────────────────────────────
# Shadow mode (default): No credentials needed.
# Live mode: All fields below must be filled.

# ── HYPERLIQUID CREDENTIALS ──────────────────────────
# NOT required for shadow mode.
# Required for live mode (LIVE_MODE=True in config.py).
#
# Option A: Export your main wallet private key
#   Login → Cash → ⋯ → Export Private Key
#   WARNING: This key can withdraw funds. Use Option B for safety.
#
# Option B (RECOMMENDED): Create an API wallet
#   Login → app.hyperliquid.xyz/API → Create API Wallet
#   This key can trade but CANNOT withdraw funds.
#   Set HL_PRIVATE_KEY to the API wallet's private key.
#   Set HL_ACCOUNT_ADDRESS to your MAIN wallet address (not the API wallet).

HL_PRIVATE_KEY=
HL_ACCOUNT_ADDRESS=
```

---

## SUPPLEMENT D: Live Mode Activation Checklist

```
SWITCHING FROM SHADOW TO LIVE — Step by step:

PRE-FLIGHT (before changing any code):
  □ Run gate_checker.py — ALL 3 gates PASS, NO kill signals
  □ Review CHANGELOG.md — understand what parameters were tuned during shadow
  □ Decide initial deposit amount (recommend: $200-300 for first week)

FUND THE ACCOUNT:
  □ Bridge USDC to Hyperliquid (via Arbitrum bridge at app.hyperliquid.xyz)
  □ Wait for deposit confirmation
  □ Verify balance appears in Hyperliquid UI

CREATE API WALLET (recommended over main key):
  □ Go to app.hyperliquid.xyz/API
  □ Create new API wallet with a name (e.g., "bayesmarket-bot")
  □ Authorize the API wallet
  □ Copy the API wallet's PRIVATE KEY → paste into .env as HL_PRIVATE_KEY
  □ Copy your MAIN wallet ADDRESS → paste into .env as HL_ACCOUNT_ADDRESS
  □ IMPORTANT: HL_ACCOUNT_ADDRESS = main wallet, NOT the API wallet address

CONFIGURE:
  □ Fill in .env file (HL_PRIVATE_KEY and HL_ACCOUNT_ADDRESS)
  □ In config.py: change LIVE_MODE = False → LIVE_MODE = True
  □ In config.py: set SIMULATED_CAPITAL to match your deposit (fallback only)
  □ That's it. Capital auto-detects from your HL account in live mode.

FIRST LAUNCH:
  □ Run: python main.py
  □ Dashboard should show "LIVE MODE" indicator (not "SHADOW")
  □ Wait for first signal to trigger
  □ MANUALLY VERIFY in Hyperliquid UI:
    □ Entry limit order appeared in "Open Orders"
    □ After fill: SL stop order appeared
    □ After fill: TP1 and TP2 limit orders appeared
    □ All order sizes match expected values
  □ Let first trade complete (win or loss)
  □ Verify PnL in dashboard matches Hyperliquid UI PnL

SCALING:
  □ Week 1: initial deposit ($200-300) — verify mechanics
  □ Week 2: if profitable → add to $500
  □ Week 3: if still profitable → add to $1,000
  □ Week 4+: scale based on confidence and gate_checker results
  □ NEVER deposit more than you're willing to lose entirely
```

---

## UPDATED CLAUDE.md ADDITIONS

Add these to the CLAUDE.md file under "Critical Implementation Rules":

```
### Rule 11: Capital Auto-Detection
In live mode, capital is fetched from Hyperliquid API (user_state → accountValue)
before every new trade. In shadow mode, capital starts at SIMULATED_CAPITAL and
compounds with simulated PnL. Never hardcode capital. See Errata Supplement A.

### Rule 12: Order Types Are Fixed
Entry = Limit ALO (post-only, maker fee)
SL = Stop Market (guaranteed fill, taker fee)
TP1/TP2 = Limit GTC (passive, maker fee)
No other combinations. See Errata Supplement B for full lifecycle.

### Rule 13: Entry Never Chases
If limit entry doesn't fill in 5s × 3 retries = 15s, ABORT.
Never fall back to market order for entry. If we can't get a good price,
we don't trade. This is an accuracy system, not a speed system.
```
