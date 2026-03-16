# BayesMarket v3 — Rollout Brief
## For: Claude Code
## Type: Feature Update (4 areas)
## Prerequisite: v2 (BAYESMARKET_MAJOR_UPDATE.md) must be complete

---

> **Sebelum mengeksekusi apapun:** Baca dokumen ini sepenuhnya, review
> setiap perubahan terhadap kode existing di repo, berikan pendapat jika
> ada risiko atau pendekatan yang lebih baik, baru eksekusi per section.
> Jangan telan mentah-mentah. Konfirmasi setiap file yang diubah.

---

## Scope Overview

| Section | Area | Jenis |
|---------|------|-------|
| 1 | Testnet Support | Targeted edits (6 files) |
| 2 | Railway Deployment | New files + 2 edits |
| 3 | Telegram Dashboard (Pull + Push) | New file + 3 edits |
| 4 | Loss Trade Analysis | New file + 3 edits |

**Total: 4 new files + ~15 targeted edits. Tidak ada full file replacement.**

---

## Section 1 — Testnet Support

### 1.1 `config.py` — 3 line edits

Find:
```python
HL_WS_URL  = "wss://api.hyperliquid.xyz/ws"
HL_REST_URL = "https://api.hyperliquid.xyz"
```

Replace with:
```python
HL_REST_URL = os.getenv("HL_REST_URL", "https://api.hyperliquid.xyz")
HL_WS_URL   = os.getenv("HL_WS_URL",   "wss://api.hyperliquid.xyz/ws")
IS_TESTNET  = "testnet" in HL_REST_URL
```

Why: Single env var swap to switch networks. IS_TESTNET flag used by
dashboard and Telegram for visual labeling.

---

### 1.2 `engine/executor.py` — SDK base_url routing

Find the section where Hyperliquid Exchange SDK is instantiated for
LIVE_MODE order placement. Add base_url parameter:

```python
from hyperliquid.utils.constants import TESTNET_API_URL, MAINNET_API_URL

base_url = TESTNET_API_URL if config.IS_TESTNET else MAINNET_API_URL
exchange = Exchange(
    wallet,
    base_url=base_url,
    account_address=config.HL_ACCOUNT_ADDRESS,
)
```

Why: Without this, SDK routes to mainnet even if .env points to testnet.
SDK already supports base_url — confirmed from source inspection.

---

### 1.3 `runtime.py` — network_label property

Add this property to RuntimeConfig dataclass:
```python
@property
def network_label(self) -> str:
    from bayesmarket import config
    if not self.live_mode:
        return "🟡 SHADOW"
    return "🟠 TESTNET" if config.IS_TESTNET else "🔴 LIVE"
```

Update mode_label to delegate:
```python
@property
def mode_label(self) -> str:
    return self.network_label
```

---

### 1.4 `dashboard/terminal.py` — testnet badge

In _build_status_bar(), find mode display and update:
```python
# Replace:
mode_color = "red" if rt.live_mode else "yellow"
mode_label = "LIVE" if rt.live_mode else "SHADOW"

# With:
from bayesmarket import config as _cfg
if not rt or not rt.live_mode:
    mode_color, mode_label = "yellow", "SHADOW"
elif _cfg.IS_TESTNET:
    mode_color, mode_label = "dark_orange", "TESTNET"
else:
    mode_color, mode_label = "red", "LIVE"
```

---

### 1.5 `telegram_bot/handlers.py` — network in status

In _format_status(), update header line:
```python
# Replace:
lines.append(f"{rt.mode_label}  {rt.status_label}")

# With:
network_tag = " | 🟠 TESTNET" if (rt.live_mode and config.IS_TESTNET) else ""
lines.append(f"{rt.network_label}  {rt.status_label}{network_tag}")
```

---

### 1.6 `.env.example` — append testnet section

Append to end of file:
```bash
# ── TESTNET MODE ──────────────────────────────────────
# Step 1: Mainnet account must be activated (deposited once)
# Step 2: Get mock USDC → https://app.hyperliquid-testnet.xyz/drip
# Step 3: Create testnet API wallet → https://app.hyperliquid-testnet.xyz/API
# Step 4: Set these vars:
# LIVE_MODE=true
# HL_REST_URL=https://api.hyperliquid-testnet.xyz
# HL_WS_URL=wss://api.hyperliquid-testnet.xyz/ws
# HL_PRIVATE_KEY=<testnet API wallet private key>
# HL_ACCOUNT_ADDRESS=<testnet main wallet address>
# DB_PATH=/app/data/bayesmarket_testnet.db
```

### 1.7 New file: `.env.testnet`

Copy from: `v3/.env.testnet` (provided in this rollout package)

---

## Section 2 — Railway Deployment

### 2.1 New deployment files (copy as-is from v3/ package)

```
v3/Procfile        → repo root/Procfile
v3/railway.toml    → repo root/railway.toml
v3/nixpacks.toml   → repo root/nixpacks.toml
v3/.env.railway    → repo root/.env.railway
```

These files tell Railway how to build and start the bot.

---

### 2.2 `config.py` — add DEPLOYMENT_ENV

Add after IS_TESTNET line:
```python
DEPLOYMENT_ENV = os.getenv("DEPLOYMENT_ENV", "local")
# Values: "railway" | "vps" | "local"
# Used to toggle features incompatible with Railway (e.g. terminal dashboard)

IS_RAILWAY = DEPLOYMENT_ENV == "railway"
```

---

### 2.3 `main.py` — conditional dashboard loading

Find the task list where dashboard_loop is added:
```python
# Terminal dashboard
dashboard_loop(state),
```

Replace with:
```python
# Terminal dashboard — disabled on Railway (no TTY)
# Monitoring via Telegram instead when IS_RAILWAY=True
*([dashboard_loop(state)] if not config.IS_RAILWAY else []),
```

Why: Railway containers are headless — Rich terminal UI crashes or
produces garbage output. Telegram is the sole monitoring interface
on Railway.

---

### 2.4 `main.py` — structlog format for Railway

In Railway, logs go to journald. Colors break parsing.
Find structlog.configure() and update:

```python
import os
_use_colors = not config.IS_RAILWAY  # no color codes in Railway logs

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(colors=_use_colors),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
```

---

### 2.5 `data/storage.py` — Railway volume path

In Storage.__init__(), add path creation:
```python
def __init__(self, db_path=None):
    self.db_path = db_path or config.DB_PATH
    # Ensure parent directory exists (needed for Railway volume mount)
    self.db_path.parent.mkdir(parents=True, exist_ok=True)
    self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
    # ... rest unchanged
```

Why: Railway volume mount path `/app/data/` may not exist on first run.

---

### Platform Portability

After this change, switching platform requires only `.env` change + redeploy:

```
Railway:   DEPLOYMENT_ENV=railway  DB_PATH=/app/data/bayesmarket.db
Contabo:   DEPLOYMENT_ENV=vps     DB_PATH=bayesmarket.db
Local:     DEPLOYMENT_ENV=local   DB_PATH=bayesmarket.db
```

No code changes needed when switching platforms.

---

## Section 3 — Telegram Dashboard (Pull + Push)

### 3.1 New file: `telegram_bot/dashboard_push.py`

Copy from: `v3/telegram_bot/dashboard_push.py` (provided in package)

This module contains:
- `build_dashboard_text(state, rt)` — builds full ASCII monospace table
- `dashboard_push_loop(state, rt, app, chat_id)` — async loop that edits
  one Telegram message every 30 seconds (live ticker)
- `send_dashboard_once(...)` / `edit_dashboard(...)` — send/edit helpers
- `toggle_push(enable)` — on/off switch
- `reset_dashboard_message()` — force new message on next cycle

Dashboard format follows the monospace style from reference screenshot:
```
METRIC          | VALUE      | STATUS
----------------|------------|--------
SCORE 5m        | -8.7 ▓▓▓▓░ | ▼ SHORT
...
```

---

### 3.2 `telegram_bot/bot.py` — start push loop

In telegram_bot_loop(), after app.start(), add push loop task:

```python
from bayesmarket.telegram_bot.dashboard_push import (
    dashboard_push_loop,
    init_push_dashboard,
)

# Init push dashboard module
init_push_dashboard(app, chat_id)

# Add push loop as background task
asyncio.create_task(dashboard_push_loop(state, rt, app, chat_id))
```

---

### 3.3 `telegram_bot/handlers.py` — add /dashboard command

Add new command handler inside build_handlers():

```python
async def cmd_dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/dashboard — pull mode: send current snapshot immediately."""
    from bayesmarket.telegram_bot.dashboard_push import (
        build_dashboard_text, send_dashboard_once, toggle_push,
        reset_dashboard_message, PUSH_AUTO
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
    status = f"\n\n_Auto-push: {'ON ✅' if PUSH_AUTO else 'OFF ⏸️'}_"
    await update.message.reply_text(
        text + status,
        parse_mode="Markdown"
    )
```

Register it:
```python
CommandHandler("dashboard", cmd_dashboard),
```

---

### 3.4 `telegram_bot/handlers.py` — update /help

Add to cmd_help message:
```
*/dashboard* — Lihat live dashboard sekarang (pull)\n
*/dashboard auto* — Aktifkan auto-push (update tiap 30s)\n
*/dashboard off* — Matikan auto-push\n
```

---

### 3.5 `telegram_bot/keyboards.py` — add dashboard button to main menu

In main_menu_keyboard(), add button:
```python
InlineKeyboardButton("📊 Dashboard", callback_data="dashboard_pull"),
```

In on_callback() handler, add case:
```python
elif data == "dashboard_pull":
    from bayesmarket.telegram_bot.dashboard_push import build_dashboard_text
    text = build_dashboard_text(state, rt)
    await query.edit_message_text(text, parse_mode="Markdown",
        reply_markup=_back_keyboard())
```

---

## Section 4 — Loss Trade Analysis

### 4.1 New file: `engine/loss_analyzer.py`

Copy from: `v3/engine/loss_analyzer.py` (provided in package)

This module contains:
- `classify_loss(pos, state, exit_price, exit_reason, exit_score)` →
  returns `LossDiagnosis` dataclass
- `format_loss_alert(diagnosis, pos, exit_price, pnl, mode)` →
  returns formatted Telegram string in monospace style
- Loss categories:
  - `stale_poc_sl` — SL basis POC > 1% away (critical)
  - `poor_rr_entry` — RR ratio < 0.5 at entry (critical)
  - `trend_reversal` — score flipped before SL hit (moderate)
  - `time_overheld` — held > 2x TIME_EXIT limit (critical)
  - `choppy_market` — borderline entry score (moderate)
  - `normal_sl` — clean loss, no anomaly (minor)

---

### 4.2 `data/storage.py` — add loss_diagnosis columns to trades table

In the _SCHEMA string, find the trades table CREATE statement.
Add these columns before the closing `);`:

```sql
-- Loss analysis columns (added v3)
score_at_exit REAL,
rr_actual REAL,
hold_minutes REAL,
loss_category TEXT,
loss_severity TEXT,
loss_diagnosis TEXT,
loss_recommendation TEXT,
score_flipped INTEGER
```

Also add migration for existing databases (add after _init_schema()):
```python
def _migrate_v3(self) -> None:
    """Add v3 loss analysis columns if not present."""
    existing = {
        row[1] for row in
        self.conn.execute("PRAGMA table_info(trades)")
    }
    new_cols = {
        "score_at_exit": "REAL",
        "rr_actual": "REAL",
        "hold_minutes": "REAL",
        "loss_category": "TEXT",
        "loss_severity": "TEXT",
        "loss_diagnosis": "TEXT",
        "loss_recommendation": "TEXT",
        "score_flipped": "INTEGER",
    }
    for col, dtype in new_cols.items():
        if col not in existing:
            self.conn.execute(
                f"ALTER TABLE trades ADD COLUMN {col} {dtype}"
            )
    self.conn.commit()
```

Call `self._migrate_v3()` at end of `__init__`.

---

### 4.3 `data/storage.py` — update insert_trade to accept diagnosis

Update insert_trade() signature:
```python
def insert_trade(
    self,
    position: Position,
    exit_price: float,
    exit_reason: str,
    pnl: float,
    pnl_pct: float,
    merge_type: str,
    regime: str,
    funding_cost: float = 0.0,
    diagnosis=None,  # Optional[LossDiagnosis]
) -> int:  # returns row id
```

Add to INSERT statement and values tuple:
```python
# In INSERT cols, add:
score_at_exit, rr_actual, hold_minutes,
loss_category, loss_severity, loss_diagnosis,
loss_recommendation, score_flipped

# In values:
(diagnosis.score_at_exit if diagnosis else None),
(diagnosis.rr_ratio if diagnosis else None),
(diagnosis.hold_minutes if diagnosis else None),
(diagnosis.category if diagnosis else None),
(diagnosis.severity if diagnosis else None),
(diagnosis.diagnosis_text if diagnosis else None),
(diagnosis.recommendation if diagnosis else None),
(int(diagnosis.score_flipped) if diagnosis else None),
```

Return lastrowid:
```python
cursor = self.conn.execute(INSERT_SQL, values)
self.conn.commit()
return cursor.lastrowid
```

---

### 4.4 `engine/executor.py` — wire loss classifier on close

In `_close_position()`, add diagnosis when pnl < 0:

```python
from bayesmarket.engine.loss_analyzer import classify_loss, format_loss_alert

diagnosis = None
if pnl < 0:
    # Get current 5m score for exit context
    exit_score = 0.0
    tf5 = state.tf_states.get("5m")
    if tf5 and tf5.signal:
        exit_score = tf5.signal.total_score

    diagnosis = classify_loss(
        pos=pos,
        state=state,
        exit_price=exit_price,
        exit_reason=exit_reason,
        exit_score=exit_score,
    )
    logger.warning(
        "loss_classified",
        category=diagnosis.category,
        severity=diagnosis.severity,
        rr=diagnosis.rr_ratio,
        hold_min=diagnosis.hold_minutes,
    )
```

Pass diagnosis to insert_trade():
```python
trade_id = storage.insert_trade(
    position=pos,
    exit_price=exit_price,
    exit_reason=exit_reason,
    pnl=pos.pnl_realized + pnl,
    pnl_pct=pnl_pct,
    merge_type=merge_type,
    regime=regime,
    diagnosis=diagnosis,
)
if diagnosis:
    diagnosis.trade_id = trade_id
```

---

### 4.5 `telegram_bot/alerts.py` — enrich loss alert

Update `alert_exit()` to accept and use diagnosis:

```python
async def alert_exit(
    side, entry_price, exit_price, pnl, pnl_pct,
    exit_reason, duration_seconds, tp1_hit, mode,
    diagnosis=None,  # Optional[LossDiagnosis]
) -> None:
```

If diagnosis is present (loss trade), use rich format:
```python
if diagnosis and pnl < 0:
    from bayesmarket.engine.loss_analyzer import format_loss_alert
    # Build position mock for formatter
    class _PosMock:
        pass
    pos_mock = _PosMock()
    pos_mock.side = side
    pos_mock.entry_price = entry_price
    pos_mock.sl_price = entry_price * (1 - 0.01)  # approx
    pos_mock.tp1_price = entry_price * (1 - 0.002)
    pos_mock.sl_basis = diagnosis.sl_basis
    pos_mock.source_tfs = ["5m"]  # approx
    pos_mock.entry_score_5m = diagnosis.score_at_entry
    pos_mock.entry_score_15m = diagnosis.score_at_entry
    pos_mock.entry_time = time.time() - diagnosis.hold_minutes * 60
    msg = format_loss_alert(diagnosis, pos_mock, exit_price, pnl, mode)
else:
    # Normal win/exit format (existing code)
    ...
await send_alert(msg)
```

**Better approach:** pass the actual `pos` object to `alert_exit` from
executor instead of reconstructing mock. Update call site in
_send_exit_alert() to pass pos directly.

---

### 4.6 `telegram_bot/handlers.py` — add /analysis command

Add new handler:

```python
async def cmd_analysis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/analysis — loss pattern summary from DB."""
    import sqlite3
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

        # Category distribution
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

        # Most recent loss detail
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
```

Register it:
```python
CommandHandler("analysis", cmd_analysis),
```

Add to /help:
```
*/analysis [1d|7d|30d|all]* — Loss pattern analysis\n
```

---

## Validation After All Sections

```bash
# Section 1: testnet flag
python3 -c "
import os; os.environ['HL_REST_URL']='https://api.hyperliquid-testnet.xyz'
from bayesmarket import config; print('IS_TESTNET:', config.IS_TESTNET)
"
# Expected: True

# Section 2: Railway detection
python3 -c "
import os; os.environ['DEPLOYMENT_ENV']='railway'
from bayesmarket import config; print('IS_RAILWAY:', config.IS_RAILWAY)
"
# Expected: True

# Section 3: dashboard_push import
python3 -c "
from bayesmarket.telegram_bot.dashboard_push import build_dashboard_text
print('Import OK')
"

# Section 4: loss_analyzer import
python3 -c "
from bayesmarket.engine.loss_analyzer import classify_loss, LossDiagnosis
print('Import OK')
"

# Section 4: DB migration (run with existing DB)
python3 -c "
from bayesmarket.data.storage import Storage
s = Storage(); print('Migration OK')
import sqlite3; conn = sqlite3.connect(str(s.db_path))
cols = {r[1] for r in conn.execute('PRAGMA table_info(trades)')}
print('loss_category in DB:', 'loss_category' in cols)
"
# Expected: True
```

---

## Switch Reference Card (Updated)

```
SHADOW + LOCAL:
  DEPLOYMENT_ENV=local
  LIVE_MODE=false
  DB_PATH=bayesmarket.db

SHADOW + RAILWAY:
  DEPLOYMENT_ENV=railway
  LIVE_MODE=false
  DB_PATH=/app/data/bayesmarket.db    ← Railway Volume required

TESTNET + RAILWAY:
  DEPLOYMENT_ENV=railway
  LIVE_MODE=true
  HL_REST_URL=https://api.hyperliquid-testnet.xyz
  HL_WS_URL=wss://api.hyperliquid-testnet.xyz/ws
  DB_PATH=/app/data/bayesmarket_testnet.db

LIVE + VPS (Contabo/Oracle):
  DEPLOYMENT_ENV=vps
  LIVE_MODE=true
  HL_REST_URL=https://api.hyperliquid.xyz
  HL_WS_URL=wss://api.hyperliquid.xyz/ws
  DB_PATH=bayesmarket.db
```

---

## New Telegram Commands (v3)

| Command | Fungsi |
|---------|--------|
| `/dashboard` | Pull: snapshot sekarang |
| `/dashboard auto` | Aktifkan push (edit tiap 30s) |
| `/dashboard off` | Matikan push |
| `/analysis [1d\|7d\|30d\|all]` | Loss pattern summary |

---

## File Manifest

### New files to copy from v3/ package:

| Source | Destination in repo |
|--------|---------------------|
| `v3/Procfile` | `Procfile` |
| `v3/railway.toml` | `railway.toml` |
| `v3/nixpacks.toml` | `nixpacks.toml` |
| `v3/.env.railway` | `.env.railway` |
| `v3/.env.testnet` | `.env.testnet` |
| `v3/engine/loss_analyzer.py` | `bayesmarket/engine/loss_analyzer.py` |
| `v3/telegram_bot/dashboard_push.py` | `bayesmarket/telegram_bot/dashboard_push.py` |

### Files with targeted edits (section reference):

| File | Sections |
|------|---------|
| `bayesmarket/config.py` | 1.1, 2.2 |
| `bayesmarket/engine/executor.py` | 1.2, 4.4 |
| `bayesmarket/runtime.py` | 1.3 |
| `bayesmarket/dashboard/terminal.py` | 1.4 |
| `bayesmarket/telegram_bot/handlers.py` | 1.5, 3.3, 3.4, 4.6 |
| `bayesmarket/.env.example` | 1.6 |
| `bayesmarket/main.py` | 2.3, 2.4 |
| `bayesmarket/data/storage.py` | 2.5, 4.2, 4.3 |
| `bayesmarket/telegram_bot/bot.py` | 3.2 |
| `bayesmarket/telegram_bot/keyboards.py` | 3.5 |
| `bayesmarket/telegram_bot/alerts.py` | 4.5 |

**Total: 7 new files + 11 files with targeted edits.**

---

## What This Does NOT Change

- Trading logic: unchanged
- Signal scoring: unchanged
- Risk management: unchanged
- All v2 bug fixes: unchanged
- Shadow mode behavior on local/VPS: unchanged
- Existing Telegram commands (/status, /report, etc.): unchanged
- Binance fallback klines: unchanged

---

*Review each section before executing. If any implementation conflicts
with v2 code, flag it and propose a better approach before proceeding.*
