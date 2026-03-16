# BayesMarket — Major Update Brief
## For: Claude Code (Repo Maintainer)
## Classification: Rollout Instructions — All Changes Mandatory

---

> **Context:** Dokumen ini merangkum seluruh analisis, temuan bug, diagnosis operasional, dan fitur baru yang dihasilkan dari sesi review mendalam terhadap codebase BayesMarket. Tujuan: memberikan Claude Code instruksi lengkap untuk melakukan rollout perubahan ke repo GitHub secara menyeluruh dan terstruktur.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Cross-Check: Bug Review vs Data Aktual](#2-cross-check-bug-review-vs-data-aktual)
3. [Diagnosis: 3 Keluhan Operasional](#3-diagnosis-3-keluhan-operasional)
4. [Fix: Wall Detection](#4-fix-wall-detection)
5. [Arsitektur: Multiple Pairs & Concurrent Positions](#5-arsitektur-multiple-pairs--concurrent-positions)
6. [New Features: Telegram, VPS, Mode Switching](#6-new-features-telegram-vps-mode-switching)
7. [File Manifest: Semua File yang Harus Diganti/Ditambah](#7-file-manifest-semua-file-yang-harus-digantiditambah)
8. [Rollout Instructions untuk Claude Code](#8-rollout-instructions-untuk-claude-code)
9. [File Reference (Full Content)](#9-file-reference-full-content)

---

## 1. Executive Summary

BayesMarket adalah bot trading BTC otomatis di Hyperliquid dengan arsitektur 4-timeframe (5m, 15m, 1h, 4h), shadow/live mode, dan SQLite logging. Setelah 25+ jam shadow mode berjalan dan menghasilkan **6 trades dengan WR 100% dan net PnL +$35.01**, dilakukan review mendalam yang menemukan:

- **3 bug kritis** yang belum terpicu (karena semua trade adalah TP2 hit, bukan SL)
- **2 masalah sistemik** yang menyebabkan terlalu sedikit trade dan hold time terlalu lama
- **1 masalah infrastruktur** (wall detection tidak berfungsi sama sekali)
- **3 fitur baru** yang dibutuhkan untuk operasional (Telegram, VPS deployment, mode switching)

**Penting:** 100% WR dari 6 trade bukan validasi edge — BTC memang sedang downtrend kuat dari $71,800 → $70,276. Bot sedang mengikuti trend, bukan melakukannya karena sinyal yang tepat.

---

## 2. Cross-Check: Bug Review vs Data Aktual

### Metodologi

Review dilakukan dengan dua pendekatan:
1. **Static code analysis** — membaca semua file Python
2. **Dynamic data analysis** — query SQLite `bayesmarket.db` (25.1 jam data, 27,491 signal records, 6 trades)

### 2.1 WEIGHTS Dict — Revisi: P3 (Cleanup Only)

**Temuan awal (salah):** `WEIGHTS` dict di `config.py` tidak pernah dipakai — dead code yang menyebabkan scoring tidak sesuai desain.

**Revisi setelah verifikasi data:**
```python
# Verifikasi dari DB:
A = -1.2785 (stored=-1.2785) ✓ MATCH
B = -4.5000 (stored=-4.5000) ✓ MATCH
Total = -6.5784 ✓ MATCH
```

**Penjelasan:** Weights **sudah di-bake** langsung ke dalam fungsi indikator:
- `compute_cvd()` → `2.0 * tanh(...)` → range `[-2.0, +2.0]` ✓
- `compute_obi()` → `obi_raw * 2.0` → range `[-2.0, +2.0]` ✓
- `compute_vwap()` → clamp ke `±1.5` ✓

WEIGHTS dict adalah **dokumentasi intent**, bukan parameter aktif. Scoring system bekerja sesuai desain.

**Action:** Tambahkan komentar klarifikasi di `config.py`. Tidak perlu mengubah logic.

---

### 2.2 Capital Double-Accounting — Status: Latent Bug (Belum Terpicu)

**Bug di `engine/executor.py` — fungsi `_close_position`:**

```python
# KODE LAMA (BUGGY):
# Ketika TP1 hit:
state.capital += tp1_pnl          # ← capital naik $X
pos.pnl_realized = tp1_pnl        # ← tercatat

# Ketika TP2/SL hit setelah TP1:
pnl = calculate_pnl(pos.side, ..., pos.remaining_size)  # hanya 40%
_close_position(state, ..., pnl=pnl)
# Di dalam _close_position (BUGGY):
state.capital += pnl - pos.pnl_realized  # ← kapital dikurangi TP1 yang sudah dibayarkan!
```

**Mengapa belum terpicu:** Semua 6 trade exit via `tp2_hit`. Path buggy hanya aktif saat **SL hit setelah TP1**. Dalam kondisi SL setelah TP1, capital akan dihitung lebih rendah dari seharusnya.

**Verifikasi:**
```
Trade 1: correct=+9.3146 | db_pnl=+9.3146 | diff=+0.0000 ✓
(semua 6 trade match karena semua TP2, bukan SL path)
```

**Fix:**
```python
# KODE BARU (FIXED) — di executor.py:
# TP1 hit: bank langsung
pos.tp1_hit = True
pos.remaining_size -= pos.tp1_size
pos.pnl_realized += tp1_pnl
state.capital += tp1_pnl      # ← bank TP1

# TP2/SL/time exit: hanya pass remaining pnl
_close_position(state, storage, exit_price, "tp2_hit", tp2_pnl, pnl_pct)
# Di _close_position: state.capital += pnl  (simple, tidak subtract pnl_realized)
```

---

### 2.3 `tp2_hit` Flag Tidak Di-set — Confirmed Bug

```sql
-- Query DB:
exit_reason=tp2_hit | tp1_hit=1 | tp2_hit=0   (semua 6 trade)
```

**Penyebab di kode lama:**
```python
# executor.py — _monitor_position():
if check_tp2(pos, mid):
    tp2_pnl = calculate_pnl(...)
    pnl_total = pos.pnl_realized + tp2_pnl
    # LUPA: pos.tp2_hit = True  ← baris ini tidak ada!
    _close_position(state, storage, pos.tp2_price, "tp2_hit", ...)
```

**Impact:** Tidak mempengaruhi PnL atau capital. Mempengaruhi analytics — report dan evaluasi trade tidak bisa membedakan TP2 hit vs SL hit secara programatik.

**Fix:** Tambahkan `pos.tp2_hit = True` sebelum `_close_position()`.

---

### 2.4 `can_trade()` Tidak Dipanggil di Entry — Confirmed Bug

**Di `executor.py` — `_evaluate_entry()`:**
```python
# KODE LAMA:
risk = state.risk
if risk.daily_paused or risk.full_stop_active:
    return
```

**Problem:** `can_trade()` di `risk/limits.py` juga berisi logika **expiry** untuk full_stop dan daily_pause:
```python
def can_trade(risk, capital):
    if risk.full_stop_active:
        if now >= risk.full_stop_until:
            risk.full_stop_active = False  # ← state reset saat expired
```

Tanpa memanggil `can_trade()`, bot bisa stuck dalam `full_stop_active = True` selamanya meskipun duration sudah habis — kecuali ada sesuatu yang men-trigger `can_trade()` dari tempat lain.

**Fix:**
```python
# KODE BARU:
from bayesmarket.risk.limits import can_trade
allowed, reason = can_trade(state.risk, state.capital)
if not allowed:
    return
```

---

### 2.5 Synthetic Trade Router Race Condition — Confirmed Bug

**Di `main.py` — `_synthetic_trade_router()`:**
```python
last_processed = 0
...
current_len = len(state.trades)
new_trades = list(state.trades)[last_processed:]
```

**Problem:** `state.trades` adalah `deque` dengan TTL pruning via `popleft()` di `_process_trades()`. Setelah pruning, `last_processed` bisa lebih besar dari `len(state.trades)` → `list(state.trades)[last_processed:]` return empty list → synthetic builder berhenti menerima trades tanpa error.

**Impact:** Klines bisa berhenti update secara diam-diam. Bot masih berjalan tapi sinyal menjadi stale.

**Fix:** Track by timestamp, bukan index:
```python
last_processed_ts: float = 0.0
# Di loop:
cutoff = last_processed_ts - 0.05  # 50ms overlap
new_trades = [t for t in state.trades if t.timestamp > cutoff]
for trade in new_trades:
    feed_trade_to_builders(trade, builders, state)
if new_trades:
    last_processed_ts = max(t.timestamp for t in new_trades)
```

---

### 2.6 Daily Reset Race Condition — Confirmed Bug

**Di `risk/limits.py` — `check_daily_reset()`:**
```python
# KODE LAMA:
if now.hour == 0 and now.minute == 0:
    # reset...
```

**Problem:** Loop dipanggil setiap 60 detik. Kalau event loop sedang busy saat `00:00:00 UTC`, window 1 menit ini bisa terlewat. Daily counters tidak reset, cooldown/loss tracking carry-over ke hari berikutnya.

**Fix:**
```python
# KODE BARU — gunakan date comparison:
_last_reset_date: date = date.min

def check_daily_reset(risk):
    global _last_reset_date
    today_utc = datetime.now(timezone.utc).date()
    if today_utc <= _last_reset_date:
        return
    if datetime.now(timezone.utc).hour != config.DAILY_RESET_HOUR_UTC:
        return
    _last_reset_date = today_utc
    risk.daily_pnl = 0.0
    risk.trades_today = 0
    # ...
```

---

### 2.7 Regime Detection Insufficient Data — Confirmed

```
# Dari DB:
15m: always 'trending' (0 ranging detected in 27,491 records)
1h:  always 'trending'
4h:  always 'trending'
5m:  2,087 ranging / 25,404 trending (hanya 5m yang berfungsi)
```

**Penyebab:** `ATR_PERCENTILE_LOOKBACK = 100` membutuhkan 100+ ATR values. Untuk 4h TF dengan klines 1h, perlu 100+ jam data sebelum regime detection aktif. Untuk 1h TF dengan 15m klines, perlu 25+ jam.

**Diagnosis:** Bot sudah jalan 25 jam, jadi 1h seharusnya sudah bisa, tapi masih all-trending. Kemungkinan ATR range di periode ini terlalu sempit untuk trigger ranging threshold.

**Impact:** `scoring_threshold_ranging` (lebih tinggi) tidak pernah dipakai untuk 15m/1h/4h. Sinyal lebih mudah trigger di ranging market seharusnya tapi tidak terjadi.

**Action:** Tidak perlu fix kode. Monitor setelah 48+ jam running untuk validasi apakah regime detection mulai aktif.

---

## 3. Diagnosis: 3 Keluhan Operasional

### 3.1 Terlalu Sedikit Trade

**Dari events log:**
```
[17:25] SHORT entry (5m)
[17:27] SHORT entry (5m)    ← 2 menit gap
[17:38] SHORT entry (5m)    ← 11 menit gap
[17:42] SHORT entry (5m)    ← 4 menit gap
[17:57] SHORT entry (5m)    ← 15 menit gap
[22:22] SHORT entry (5m)    ← 4.5 JAM GAP
[06:40] SHORT entry (5m+15m) ← 8+ jam gap setelah restart
```

**Root cause:** Category B saturation. Dari 27,491 signal records:

| Indikator | 5m pegged @ ±1.5 | 15m pegged @ ±1.5 |
|---|---|---|
| VWAP | **90.5%** | **91.1%** |
| HA   | **97.7%** | **92.8%** |
| POC  | 64.5% | **90.9%** |

Artinya Category B hampir **selalu di -4.5 (maxed out)**. Dari 13.5 total possible score, 4.5 poin sudah terkunci — bot hanya butuh A+C = 2.5 dari sisa 9.0 poin untuk signal. Tapi di gap panjang (4.5 jam), CVD dan OBI sedang counter-trend sehingga A negatif, sementara B sudah maxed di -4.5, total score tidak cukup tembus -7.0.

**Akar penyebabnya:**
```python
# config.py — nilai terlalu sensitif:
VWAP_SENSITIVITY = 150.0
# $70,000 × 0.0067% deviasi → maxed
# Bahkan noise 1-candle bisa maxed indicator ini

POC_SENSITIVITY = 150.0   # sama
HA_MAX_STREAK = 3         # streak 3 candle = maxed, terlalu mudah
```

**Fix:**
```python
VWAP_SENSITIVITY = 20.0   # butuh ~5% deviasi untuk max score
POC_SENSITIVITY  = 20.0   # sama
HA_MAX_STREAK    = 5      # butuh 5 candle streak, lebih selektif
```

---

### 3.2 Hold Time Terlalu Lama

**Trade ke-7 (open saat screenshot):**
- Entry: $70,276.5
- TP1: $70,157.2 (jarak = $119 = 0.17%)
- Harga saat entry via screenshot: $70,567.5
- **Bot masuk saat harga masih $291 di atas TP1**

Entry terlambat di tengah trend yang sudah berjalan jauh. TP1 membutuhkan waktu lama karena target terlalu dekat entry tapi entry terlalu jauh dari TP.

**Tambahan:** Tidak ada mekanisme time-based exit. Posisi yang stuck bisa terbuka berjam-jam.

**Fix:** Tambahkan time-based exit di `executor.py`:
```python
# Jika tidak hit TP1 dalam X menit → exit di mid price
TIME_EXIT_ENABLED = True
TIME_EXIT_MINUTES_5M  = 30   # 5m source: max 30 menit
TIME_EXIT_MINUTES_15M = 90   # 15m source: max 90 menit
```

---

### 3.3 MTF Berkompetisi, Bukan Mendukung

**Arsitektur lama (salah):**
```
5m signal  → difilter oleh 1h VWAP (HARD BLOCK)
15m signal → difilter oleh 4h VWAP (HARD BLOCK)
Merge:     → 5m vs 15m: conflict → 15m wins (kapan pun)
```

**Masalah:**
- 5 dari 6 trade adalah `single_5m` — 15m hampir tidak pernah trigger karena di-block 4h
- MTF bukan "mendukung" tapi "mem-veto"
- Conflict resolution (`15m wins`) tidak masuk akal: kalau 5m SHORT dan 15m LONG, itu sinyal ranging/choppy, bukan alasan untuk LONG

**Filosofi yang benar:**
```
4h  = BIAS dominan (arah macro)
1h  = CONTEXT (konfirmasi bias)
15m = TIMING (zona entry)
5m  = TRIGGER (eksekusi presisi)
```

**Fix di `scoring.py`:**
```python
# BUKAN hard veto, tapi soft penalty/bonus:
MTF_ALIGNMENT_BONUS    = 1.5   # +1.5 ke score jika MTF agree
MTF_MISALIGN_PENALTY   = 0.7   # ×0.7 jika MTF disagree (bukan block)
MTF_STRONG_OPPOSE_THRESHOLD = 5.0  # hard block hanya jika filter TF score > 5.0 opposing

if snap.signal == "LONG" and snap.mtf_aligned_long:
    snap.total_score += MTF_ALIGNMENT_BONUS    # bonus
elif snap.signal == "LONG" and not snap.mtf_aligned_long:
    if mtf_snap.total_score <= -MTF_STRONG_OPPOSE_THRESHOLD:
        snap.signal = "NEUTRAL"  # hard block hanya kalau filter sangat bearish
    else:
        snap.total_score *= MTF_MISALIGN_PENALTY  # soft penalty
```

**Fix di `merge.py`:**
```python
# Case 5: conflict → SKIP (bukan 15m wins)
# Alasan: 5m SHORT + 15m LONG = choppy market = no clear bias = no trade
if s5 != s15:  # both non-NEUTRAL but different
    return MergeDecision(action="none", note="conflict_skip")
```

---

## 4. Fix: Wall Detection

### 4.1 Root Cause Analisis

Wall detection **tidak pernah berhasil** selama 25+ jam running. Dari DB: tidak ada satu pun wall-related event. Dashboard selalu menampilkan "Walls: none".

**Tiga faktor yang saling memperburuk:**

**Faktor 1 — Level count terlalu sedikit:**
```python
HL_L2_BOOK_LEVELS = 20  # hanya 20 level per side
```
BTC di $70k, top 20 bids spread ~$160. Dengan `WALL_BIN_SIZE = $10` → 16 bins untuk 20 levels → rata-rata 1.25 level per bin. Tidak ada bin yang bisa secara konsisten 3x rata-rata.

**Faktor 2 — Threshold terlalu tinggi:**
```python
WALL_MIN_SIZE_MULTIPLIER = 3.0
# avg_bin = 1.25 levels → threshold = 3.75 levels per bin
# Hampir mustahil dengan hanya 20 levels total
```

**Faktor 3 — Pruning lebih cepat dari persistence (kritis):**
```python
# Di hyperliquid.py — _update_wall_tracker():
WALL_PERSISTENCE_SECONDS = 5.0  # wall harus survive 5 detik untuk "is_valid"

# Tapi pruning:
state.tracked_walls = [w for w in new_tracked if now - w.last_seen < 3.0]
# Wall di-prune setelah 3 detik tidak muncul di book
# → wall TIDAK PERNAH bisa mencapai age 5 detik karena sudah di-prune duluan!
```

Ini adalah logical impossibility: wall butuh 5 detik untuk valid, tapi dihapus setelah 3 detik.

### 4.2 Fix

```python
# config.py — perubahan parameter:
HL_L2_BOOK_LEVELS        = 50     # dari 20 → lebih banyak data
HL_L2_SIG_FIGS           = 4      # dari 5 → $10 resolution natural di BTC $70k
WALL_BIN_SIZE            = 20.0   # dari 10.0 → bin lebih besar, lebih mudah aggregate
WALL_MIN_SIZE_MULTIPLIER = 2.0    # dari 3.0 → lebih sensitif
WALL_PERSISTENCE_SECONDS = 3.0    # dari 5.0 → sesuaikan dengan pruning window
WALL_PRUNE_SECONDS       = 6.0    # BARU: prune setelah 6s (harus > WALL_PERSISTENCE)
```

```python
# hyperliquid.py — _update_wall_tracker():
# FIX: gunakan WALL_PRUNE_SECONDS bukan hardcoded 3.0
prune_window = getattr(config, "WALL_PRUNE_SECONDS", config.WALL_PERSISTENCE_SECONDS + 2.0)
state.tracked_walls = [w for w in new_tracked if now - w.last_seen < prune_window]
```

**Expected result:** Setelah fix ini, wall detector akan mulai menampilkan walls di dashboard. SL placement via wall basis akan mulai aktif (sebelumnya selalu fallback ke POC atau ATR).

---

## 5. Arsitektur: Multiple Pairs & Concurrent Positions

### 5.1 Status Sekarang: Single Position, Single Pair (Hard-coded)

```python
# data/state.py:
position: Optional[Position] = None  # singular — hanya 1 posisi

# engine/executor.py:
if state.position is not None:
    return  # langsung skip kalau sudah ada posisi
```

```python
# config.py:
COIN = "BTC"              # hardcoded
BINANCE_SYMBOL = "BTCUSDT"  # hardcoded

# feeds/hyperliquid.py:
"coin": config.COIN  # satu coin saja di subscription
```

### 5.2 Concurrent Positions — Butuh Refactor Signifikan

Untuk mendukung concurrent positions, komponen berikut harus diubah:

| Komponen | Perubahan yang Dibutuhkan |
|---|---|
| `data/state.py` | `Optional[Position]` → `list[Position]` |
| `engine/executor.py` | Semua SL/TP/close logic harus loop per position |
| `engine/position.py` | Check functions harus accept position explicitly |
| `risk/sizing.py` | Tambahkan aggregate exposure check |
| `risk/limits.py` | Daily PnL harus aggregate semua positions |
| `dashboard/terminal.py` | Status bar harus render multi-position |

**Catatan desain penting:** Concurrent positions di TF yang sama (5m SHORT + 5m LONG) tidak masuk akal dan harus dicegah. Yang masuk akal:
- **Opsi A:** `5m position + 15m position` (different TF, mungkin sama arah)
- **Opsi B:** `Multi-pair` (BTC + ETH + SOL, masing-masing 1 position) ← **Direkomendasikan**
- **Opsi C:** `Scale-in` (tambah size ke existing position, bukan open baru)

### 5.3 Multi-Pair — Arsitektur yang Tepat

Setiap pair mendapatkan `MarketState` sendiri yang isolasi penuh:

```python
# main.py — konsep multi-pair (untuk implementasi future sprint):
PAIRS = ["BTC", "ETH", "SOL"]

async def main():
    states = {}
    for pair in PAIRS:
        rt = RuntimeConfig(live_mode=config.LIVE_MODE)
        state = init_state_for_pair(pair, rt)
        states[pair] = state

    tasks = []
    for pair, state in states.items():
        engines = create_engines_for_state(pair, state, storage)
        tasks += [
            hl_book_feed(state),          # feed per pair
            hl_trade_feed(state),         # feed per pair
            *[e.run() for e in engines.values()],
            merge_and_execute_loop(state, storage),
            position_monitor_loop(state, storage),
        ]

    # Global risk: max 3 pairs open, max total leverage 5x
    tasks.append(global_risk_monitor(states, storage))
```

**Global risk management yang dibutuhkan:**
- Max N pairs open secara bersamaan (config)
- Max aggregate notional / total capital
- Total daily PnL across all pairs
- Telegram menampilkan semua pairs

**Status:** Arsitektur ini **belum diimplementasi** di sprint ini. Multi-pair adalah Sprint 3. Sprint ini fokus pada single-pair fixes dan Telegram/VPS.

---

## 6. New Features: Telegram, VPS, Mode Switching

### 6.1 RuntimeConfig — Hot-Reload Mode Switching

**File baru: `bayesmarket/runtime.py`**

Sebelumnya, `LIVE_MODE` di `config.py` adalah static — harus restart bot untuk switch mode. Sekarang ada `RuntimeConfig` yang mutable dan di-attach ke `MarketState`:

```python
@dataclass
class RuntimeConfig:
    live_mode: bool = False          # bisa diubah tanpa restart
    trading_paused: bool = False     # pause entry baru, posisi aktif tetap jalan
    pause_reason: str = ""

    # Thresholds yang bisa diubah via Telegram /set
    scoring_threshold_5m: float = 7.0
    scoring_threshold_15m: float = 7.0
    vwap_sensitivity: float = 150.0
    poc_sensitivity: float = 150.0
```

**Cara switch mode:**
1. Via Telegram: `/live` atau `/shadow`
2. Via `.env`: set `LIVE_MODE=true` dan restart
3. Via config: ubah `LIVE_MODE = True` dan restart

**Integrasi ke state:**
```python
# main.py:
state.runtime = rt  # attach RuntimeConfig ke MarketState

# scoring.py menggunakan rt untuk threshold:
if rt:
    threshold = rt.scoring_threshold_5m  # hot-reload
```

---

### 6.2 Telegram Integration

**Modul baru: `bayesmarket/telegram_bot/`**

Telegram menjadi **control panel utama** — bukan sekedar notifikasi.

#### Setup

```bash
# .env:
TELEGRAM_BOT_TOKEN=   # dari @BotFather
TELEGRAM_CHAT_ID=     # dari @userinfobot
```

#### Command List

| Command | Fungsi |
|---|---|
| `/start` | Main menu dengan inline keyboard |
| `/status` | Status lengkap: posisi aktif, PnL, risk state, funding |
| `/scores` | Score semua 4 TF real-time dengan bar visualization |
| `/report [1d\|7d\|30d\|all]` | Performance report dari SQLite |
| `/mode` | Tampilkan mode aktif + tombol switch |
| `/shadow` | Switch ke shadow mode (langsung) |
| `/live` | Switch ke live mode (ada 2-step konfirmasi) |
| `/pause [reason]` | Pause semua entry baru (posisi aktif tetap dimonitor) |
| `/resume` | Resume trading |
| `/close` | Force close posisi aktif (ada konfirmasi) |
| `/config` | Lihat semua parameter aktif |
| `/set <param> <value>` | Ubah parameter saat runtime |
| `/help` | Daftar semua commands |

#### Parameter yang Bisa Diubah via `/set`

```
/set threshold_5m 6.0       → ubah scoring threshold 5m
/set threshold_15m 6.5      → ubah scoring threshold 15m
/set vwap_sensitivity 20.0  → ubah VWAP sensitivity
/set poc_sensitivity 20.0   → ubah POC sensitivity
```

#### Outbound Alerts (Otomatis)

Bot mengirim alert Telegram untuk:
- **Entry:** side, price, size, SL, TP1, TP2, score, source TF, risk amount
- **TP1 hit:** price, PnL, remaining size
- **Exit (TP2/SL/time/force):** full trade summary dengan duration
- **Risk events:** cooldown aktif, full stop, daily limit hit
- **Startup/shutdown**

Semua alert menggunakan `asyncio.create_task()` — non-blocking, silent-fail jika Telegram tidak tersedia.

#### File Structure

```
bayesmarket/telegram_bot/
├── __init__.py
├── bot.py         # Main loop, polling, startup/shutdown notification
├── handlers.py    # Semua command handlers + callback query handler
├── alerts.py      # Outbound alerts (entry, exit, risk events)
└── keyboards.py   # Inline keyboard definitions
```

---

### 6.3 Terminal Dashboard — Uniform 4-Panel

**File diupdate: `bayesmarket/dashboard/terminal.py`**

**Perubahan utama:** Semua 4 panel sekarang menampilkan informasi yang sama — bukan 2 panel "execution" vs 2 panel "filter" yang berbeda layout.

**Layout setiap panel:**
```
BTC {tf} [EXEC/FILT] ${price}
──────────────────────────────
Score:     +8.5 │████████░░░░│
Signal:    ▲ LONG
Regime:    TRENDING  ATR%: 78
MTF(1h):   $71,780  ▲ LONG OK
A/B/C:     A:+3.2  B:+2.1  C:+1.2

ORDER BOOK
OBI:       +12.3%  (+0.25)
Depth:     +0.31
Bid Wall:  $70,400 (15.2) 95% 8s
Ask Wall:  none

FLOW
CVD Z:     +1.8σ  (+1.44)
VWAP:      $70,350  (+0.38)
POC:       $70,200  (+0.61)

TECHNICAL
RSI(14):   38.2  (+0.59)
MACD:      +0.42
EMA:       5>20  (+0.81)
HA:        ▲ ▲ ▲ ▲ ▼ ▲
```

**Perbedaan dari sebelumnya:**
- Filter TF (1h, 4h) sekarang juga menampilkan order book, flow, dan technical
- Bid/Ask wall dengan age dan decay percentage
- Color coding: hijau/merah berdasarkan nilai indikator
- Score bar bilateral (`│████████░░░░│`)
- Mode indicator di status bar: `🔴 LIVE` vs `🟡 SHADOW`

---

### 6.4 VPS Deployment

**File baru: `deploy/`**

#### Minimum Specs
- 1 vCPU, 1GB RAM, 10GB SSD
- Ubuntu 22.04 LTS atau 24.04 LTS
- Region: **US East** (dekat Hyperliquid server di AWS us-east)

#### Provider yang Direkomendasikan
- **Contabo** (sudah familiar) — [contabo.com](https://contabo.com)
- DigitalOcean Droplet — $6/mo untuk 1GB RAM
- Hetzner Cloud — €4/mo (sangat murah untuk Europe)

#### One-Command Setup

```bash
# Upload project
scp -r ./bayesmarket ubuntu@YOUR_VPS_IP:/opt/bayesmarket

# SSH ke VPS
ssh ubuntu@YOUR_VPS_IP

# Setup (Ubuntu 22/24)
cd /opt/bayesmarket
chmod +x deploy/setup.sh
./deploy/setup.sh

# Edit .env
nano .env

# Test manual
source venv/bin/activate
python -m bayesmarket

# Jalankan sebagai service
sudo systemctl start bayesmarket
sudo systemctl enable bayesmarket  # auto-start saat VPS reboot
```

#### Systemd Service Features

```ini
# deploy/bayesmarket.service
Restart=on-failure         # auto-restart jika crash
RestartSec=30s             # tunggu 30s sebelum restart
MemoryMax=1G               # resource limit
CPUQuota=80%               # tidak menghabiskan semua CPU
TimeoutStopSec=30          # graceful shutdown
EnvironmentFile=.env       # load .env otomatis
```

#### Useful Commands

```bash
# Monitoring
sudo journalctl -u bayesmarket -f          # live logs
sudo systemctl status bayesmarket          # status

# Update code
scp bayesmarket/config.py ubuntu@IP:/opt/bayesmarket/bayesmarket/
sudo systemctl restart bayesmarket

# Download DB untuk analisis lokal
scp ubuntu@IP:/opt/bayesmarket/bayesmarket.db ./backup.db
```

---

## 7. File Manifest: Semua File yang Harus Diganti/Ditambah

### 7.1 Sprint 1: Config Fixes (Parameter Only)

> Ganti isi file, tidak ada perubahan function signature.

| File | Jenis | Perubahan Utama |
|---|---|---|
| `bayesmarket/config.py` | **REPLACE** | `LIVE_MODE` dari `.env`, Telegram params, VWAP/POC sensitivity fix, wall detection params |
| `bayesmarket/.env.example` | **REPLACE** | Template lengkap dengan instruksi setup |
| `bayesmarket/requirements.txt` | **REPLACE** | Tambah `python-telegram-bot>=21.0` |

### 7.2 Sprint 2: Bug Fixes + New Features

> File yang harus diganti seluruhnya.

| File | Jenis | Perubahan Utama |
|---|---|---|
| `bayesmarket/data/state.py` | **REPLACE** | `Position._force_close`, `MarketState.runtime`, `WallInfo.is_valid` pakai config |
| `bayesmarket/engine/executor.py` | **REPLACE** | tp2_hit flag fix, capital fix, can_trade(), time exit, force close, Telegram alerts |
| `bayesmarket/engine/merge.py` | **REPLACE** | Conflict = skip (tidak lagi 15m wins) |
| `bayesmarket/indicators/scoring.py` | **REPLACE** | RuntimeConfig threshold hot-reload, MTF soft penalty/bonus |
| `bayesmarket/feeds/hyperliquid.py` | **REPLACE** | Wall pruning fix (WALL_PRUNE_SECONDS), mantissa param |
| `bayesmarket/feeds/synthetic.py` | **REPLACE** | Race condition fix (timestamp-based routing), `synthetic_trade_router` function |
| `bayesmarket/risk/limits.py` | **REPLACE** | Daily reset race fix, Telegram risk alerts |
| `bayesmarket/dashboard/terminal.py` | **REPLACE** | Uniform 4-panel layout, wall display, runtime mode indicator |
| `bayesmarket/main.py` | **REPLACE** | RuntimeConfig integration, Telegram task, `synthetic_trade_router` import |

### 7.3 New Files

| File | Jenis | Deskripsi |
|---|---|---|
| `bayesmarket/runtime.py` | **NEW** | Hot-reload RuntimeConfig, mode switching logic |
| `bayesmarket/telegram_bot/__init__.py` | **NEW** | Empty init |
| `bayesmarket/telegram_bot/bot.py` | **NEW** | Main bot loop, polling, startup/shutdown |
| `bayesmarket/telegram_bot/handlers.py` | **NEW** | All command + callback handlers |
| `bayesmarket/telegram_bot/alerts.py` | **NEW** | Outbound alerts (entry, exit, risk) |
| `bayesmarket/telegram_bot/keyboards.py` | **NEW** | Inline keyboard definitions |
| `deploy/setup.sh` | **NEW** | One-command VPS setup script |
| `deploy/bayesmarket.service` | **NEW** | Systemd service file |
| `deploy/VPS_GUIDE.md` | **NEW** | VPS management cheatsheet |

**Total: 21 files (12 replaced + 9 new)**

---

## 8. Rollout Instructions untuk Claude Code

### 8.1 Pre-Rollout Checklist

```
[ ] Backup repo ke branch baru: git checkout -b major-update-v2
[ ] Pastikan semua tests pass sebelum mulai (jika ada)
[ ] Catat versi Python yang digunakan (butuh 3.11+)
[ ] Verifikasi .env.example tidak mengandung credential nyata
```

### 8.2 Urutan Implementasi yang Benar

Urutan ini penting karena ada dependency antar file:

```
Step 1: Tambah runtime.py (tidak ada dependency)
Step 2: Replace state.py (state.runtime menggunakan RuntimeConfig dari step 1)
Step 3: Replace config.py (tambah params baru yang dipakai di step 4+)
Step 4: Replace feeds/hyperliquid.py (pakai WALL_PRUNE_SECONDS dari config)
Step 5: Replace feeds/synthetic.py (synthetic_trade_router function)
Step 6: Replace risk/limits.py (alerts import, date-based reset)
Step 7: Replace engine/merge.py (tidak ada dependency baru)
Step 8: Replace indicators/scoring.py (pakai RuntimeConfig dari state)
Step 9: Replace engine/executor.py (pakai RuntimeConfig, Telegram alerts)
Step 10: Tambah folder telegram_bot/ (semua 5 file)
Step 11: Replace dashboard/terminal.py (pakai state.runtime)
Step 12: Replace main.py (import synthetic_trade_router, telegram_bot_loop)
Step 13: Replace requirements.txt
Step 14: Replace .env.example
Step 15: Tambah folder deploy/ (3 files)
```

### 8.3 Validation Tests Setelah Rollout

```python
# Test 1: Import check
python -c "from bayesmarket.runtime import RuntimeConfig; print('OK')"
python -c "from bayesmarket.telegram_bot.bot import telegram_bot_loop; print('OK')"
python -c "from bayesmarket.feeds.synthetic import synthetic_trade_router; print('OK')"

# Test 2: Config load
python -c "from bayesmarket import config; print(config.WALL_PRUNE_SECONDS)"
# Expected: 6.0

# Test 3: Shadow mode startup (tanpa .env credentials)
python -m bayesmarket
# Expected: bot start, Telegram disabled warning (jika token kosong), dashboard muncul

# Test 4: tp2_hit flag
# Jalankan shadow mode sampai ada 1 trade yang hit TP2, query DB:
sqlite3 bayesmarket.db "SELECT tp2_hit, exit_reason FROM trades WHERE exit_reason='tp2_hit'"
# Expected: tp2_hit=1 (bukan 0 seperti sebelumnya)
```

### 8.4 Environment Variables yang Diperlukan

```bash
# Minimum untuk shadow mode (Telegram opsional):
LIVE_MODE=false
SIMULATED_CAPITAL=1000.0
COIN=BTC
BINANCE_SYMBOL=BTCUSDT

# Untuk Telegram control panel:
TELEGRAM_BOT_TOKEN=<dari @BotFather>
TELEGRAM_CHAT_ID=<dari @userinfobot>

# Untuk live mode (wajib):
HL_PRIVATE_KEY=<API wallet private key>
HL_ACCOUNT_ADDRESS=<MAIN wallet address>
```

### 8.5 Breaking Changes yang Perlu Diperhatikan

1. **`_synthetic_trade_router` dihapus dari `main.py`** — dipindahkan ke `feeds/synthetic.py` sebagai `synthetic_trade_router`. Import di main.py harus diupdate.

2. **`MarketState.runtime` adalah field baru** — kode yang melakukan `MarketState()` tanpa `runtime` akan tetap berfungsi (default `None`), tapi fungsionalitas hot-reload tidak aktif.

3. **`Position._force_close` adalah field baru** — kompatibel backward, default `False`.

4. **`config.WALL_PRUNE_SECONDS` adalah param baru** — `hyperliquid.py` menggunakan `getattr(config, "WALL_PRUNE_SECONDS", ...)` untuk backward compatibility.

5. **`LIVE_MODE` sekarang dari `.env`** — `config.py` membaca `os.getenv("LIVE_MODE", "false")`. Jika sebelumnya ada hardcode `LIVE_MODE = False`, perlu diganti.

---

## 9. File Reference (Full Content)

> Semua 21 file tersedia di direktori output. Di bawah adalah deskripsi per file beserta lokasi dan perubahan kunci.

---

### 9.1 `bayesmarket/runtime.py` *(NEW)*

**Tujuan:** Mutable runtime config yang bisa diubah tanpa restart.

**Key classes:** `RuntimeConfig`

**Key methods:**
- `switch_to_shadow(by)` → returns status message string
- `switch_to_live(hl_key, hl_address, by)` → validates credentials, returns message
- `pause_trading(reason, by)` → sets `trading_paused = True`
- `resume_trading(by)` → clears pause state

**Digunakan oleh:** `main.py`, `engine/executor.py`, `indicators/scoring.py`, `dashboard/terminal.py`, `telegram_bot/handlers.py`

---

### 9.2 `bayesmarket/data/state.py` *(REPLACE)*

**Perubahan dari original:**
- `Position` → tambah field `_force_close: bool = False`
- `MarketState` → tambah field `runtime: Optional["RuntimeConfig"] = None`
- `WallInfo.is_valid` → property sekarang membaca `config.WALL_PERSISTENCE_SECONDS` (bukan hardcoded 5.0)

---

### 9.3 `bayesmarket/engine/executor.py` *(REPLACE)*

**Semua fixes diterapkan di sini:**
- `_evaluate_entry()`: panggil `can_trade()` sebelum signal evaluation
- `_evaluate_entry()`: cek `rt.trading_paused` dari RuntimeConfig
- `_monitor_position()`: cek `pos._force_close` sebelum SL/TP
- `_monitor_position()`: tambah time-based exit check
- `_monitor_position()`: `pos.tp1_hit = True` setelah TP1 hit
- `_monitor_position()`: `pos.tp2_hit = True` sebelum TP2 close
- `_close_position()`: terima `pnl` sebagai remaining portion saja (bukan total)
- Semua exit paths: fire Telegram alert via `asyncio.create_task()`

---

### 9.4 `bayesmarket/engine/merge.py` *(REPLACE)*

**Perubahan dari original:**
- Case 5 (conflict): `action="none"` bukan `direction=s15`
- Comment menjelaskan filosofi baru (complementary, not competing)

---

### 9.5 `bayesmarket/indicators/scoring.py` *(REPLACE)*

**Perubahan dari original:**
- Threshold diambil dari `state.runtime` jika tersedia (hot-reload)
- MTF filter: soft penalty/bonus bukan hard veto
- `MTF_ALIGNMENT_BONUS = 1.5` (bukan ada sebelumnya)
- `MTF_MISALIGN_PENALTY = 0.7` (multiplier bukan block)
- `MTF_STRONG_OPPOSE_THRESHOLD = 5.0` (hard block hanya kalau filter sangat kuat opposing)
- Tambah `trading_paused` check dari RuntimeConfig

---

### 9.6 `bayesmarket/feeds/hyperliquid.py` *(REPLACE)*

**Perubahan dari original:**
- `_update_wall_tracker()`: gunakan `WALL_PRUNE_SECONDS` bukan hardcoded `3.0`
- HL l2Book subscription: tambah `"mantissa": config.HL_L2_BOOK_LEVELS` parameter

---

### 9.7 `bayesmarket/feeds/synthetic.py` *(REPLACE)*

**Perubahan dari original:**
- `SyntheticKlineBuilder` tidak berubah
- `create_builders()` dan `feed_trade_to_builders()` tidak berubah
- **NEW:** `synthetic_trade_router(state)` async function — replaces `_synthetic_trade_router` di `main.py`
- Fix: track by timestamp bukan index untuk avoid race condition

---

### 9.8 `bayesmarket/risk/limits.py` *(REPLACE)*

**Perubahan dari original:**
- `check_daily_reset()`: date comparison bukan minute-exact, global `_last_reset_date`
- `can_trade()`: expiry logic sudah ada, tidak berubah (tapi sekarang dipanggil dari executor)
- `update_after_trade()`: fire Telegram risk alerts via `asyncio.create_task(_alert_risk(...))`
- Tambah `_alert_risk()` helper async function

---

### 9.9 `bayesmarket/config.py` *(REPLACE)*

**Perubahan dari original:**
- `LIVE_MODE`: membaca dari `.env` via `os.getenv("LIVE_MODE", "false")`
- Tambah `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID`
- `VWAP_SENSITIVITY`: 150.0 → 20.0
- `POC_SENSITIVITY`: 150.0 → 20.0
- `HA_MAX_STREAK`: 3 → 5
- `HL_L2_BOOK_LEVELS`: 20 → 50
- `HL_L2_SIG_FIGS`: 5 → 4
- `WALL_BIN_SIZE`: 10.0 → 20.0
- `WALL_MIN_SIZE_MULTIPLIER`: 3.0 → 2.0
- `WALL_PERSISTENCE_SECONDS`: 5.0 → 3.0
- Tambah `WALL_PRUNE_SECONDS = 6.0`
- Tambah `TIME_EXIT_ENABLED = True`
- Tambah `TIME_EXIT_MINUTES_5M = 30`
- Tambah `TIME_EXIT_MINUTES_15M = 90`
- WEIGHTS dict: tambah komentar klarifikasi bahwa ini dokumentasi, bukan parameter aktif

---

### 9.10 `bayesmarket/dashboard/terminal.py` *(REPLACE)*

**Perubahan dari original:**
- Satu `_build_tf_panel()` function untuk semua TF (bukan `_build_exec_panel` + `_build_filter_panel`)
- Semua 4 panel menampilkan: Score, Signal, Regime, MTF, A/B/C, Order Book, Flow, Technical, HA
- Bid/Ask wall ditampilkan terpisah dengan age dan decay%
- `_build_status_bar()`: 3-column layout (position | system | mode)
- Mode indicator pakai `state.runtime` jika tersedia
- `using_fallback` ditampilkan di border warna panel (merah = fallback)

---

### 9.11 `bayesmarket/telegram_bot/bot.py` *(NEW)*

**Tujuan:** Inisialisasi dan jalankan Telegram bot sebagai asyncio task.

**Key function:** `telegram_bot_loop(state, rt)` — dipanggil dari `main.py` sebagai task di `asyncio.gather()`

**Behavior:**
- Jika `TELEGRAM_BOT_TOKEN` kosong: log warning dan return langsung (tidak crash)
- Kirim startup notification ke chat
- Start long-polling (bukan webhook)
- Kirim shutdown notification saat bot stop

---

### 9.12 `bayesmarket/telegram_bot/handlers.py` *(NEW)*

**Tujuan:** Semua command handlers dan callback query handler.

**Pattern:** `build_handlers(state, rt)` menerima state dan rt sebagai closure, return list of handlers yang di-register ke Application.

**Key handlers:**
- `cmd_start`: tampilkan main menu
- `cmd_status`: format posisi aktif, PnL, risk state
- `cmd_scores`: format semua 4 TF scores
- `cmd_report`: query SQLite, format trade statistics
- `cmd_set`: hot-reload parameter via RuntimeConfig
- `on_callback`: handler untuk semua inline keyboard buttons

---

### 9.13 `bayesmarket/telegram_bot/alerts.py` *(NEW)*

**Tujuan:** Outbound alerts yang dipanggil dari executor dan limits.

**Initialization:** `init_alerts(app, chat_id)` dipanggil dari `bot.py` setelah app initialized.

**Key functions:**
- `alert_entry()`: entry alert dengan full trade details
- `alert_tp1()`: partial exit notification
- `alert_exit()`: trade closed summary
- `alert_daily_report()`: dipanggil dari daily reset (future)
- `alert_risk_event()`: cooldown/full stop/daily limit

---

### 9.14 `bayesmarket/telegram_bot/keyboards.py` *(NEW)*

**Tujuan:** Inline keyboard definitions untuk semua menu.

**Keyboards:**
- `main_menu_keyboard()`: Status, Scores, Report, Config, Mode Switch, Pause/Resume
- `mode_menu_keyboard(live_mode)`: context-aware (show Switch to Shadow or Switch to Live)
- `live_confirm_keyboard()`: 2-step confirmation untuk switch ke live
- `report_period_keyboard()`: 1d, 7d, 30d, all
- `config_menu_keyboard()`: view config, threshold up/down, toggle alerts
- `close_position_keyboard()`: confirm/cancel force close

---

### 9.15 `bayesmarket/main.py` *(REPLACE)*

**Perubahan dari original:**
- Import `RuntimeConfig` dan init sebelum state
- `state.runtime = rt` di `_init_state()`
- Import `synthetic_trade_router` dari `feeds/synthetic.py`
- Hapus `_synthetic_trade_router()` function (dipindahkan)
- Tambah `telegram_bot_loop(state, rt)` ke task list

---

### 9.16 `bayesmarket/requirements.txt` *(REPLACE)*

**Tambahan:**
```
python-telegram-bot>=21.0
```

---

### 9.17 `bayesmarket/.env.example` *(REPLACE)*

**Tambahan:**
- `LIVE_MODE=false` (baru — sebelumnya tidak ada di .env)
- `TELEGRAM_BOT_TOKEN=` dengan instruksi cara mendapatkan
- `TELEGRAM_CHAT_ID=` dengan instruksi
- `COIN=BTC` dan `BINANCE_SYMBOL=BTCUSDT` (configurable via env)
- `DB_PATH=bayesmarket.db`

---

### 9.18 `deploy/setup.sh` *(NEW)*

**Tujuan:** One-command VPS setup untuk Ubuntu 22.04/24.04.

**Steps yang dilakukan:**
1. `apt-get update && upgrade`
2. Install Python 3.11, git, screen, tmux
3. Create `/opt/bayesmarket` directory
4. Create virtualenv dan install requirements
5. Copy `.env.example` → `.env` jika belum ada
6. Install systemd service
7. Setup logrotate

---

### 9.19 `deploy/bayesmarket.service` *(NEW)*

**Systemd service file** dengan:
- Auto-restart on failure (30s delay)
- Memory limit 1GB
- CPU quota 80%
- Graceful shutdown 30s
- Load `.env` via `EnvironmentFile`
- Log ke journald

---

### 9.20 `deploy/VPS_GUIDE.md` *(NEW)*

**Cheatsheet** untuk:
- First time setup
- Service management (start/stop/restart/status)
- Log viewing
- Code update workflow
- Database backup
- Resource monitoring
- Recommended VPS providers dan specs
- TCP keepalive settings
- Timezone setup (UTC)

---

## Appendix A: Config Parameter Changes Summary

| Parameter | Nilai Lama | Nilai Baru | Alasan |
|---|---|---|---|
| `LIVE_MODE` | `False` (hardcoded) | `os.getenv("LIVE_MODE", "false")` | Bisa diset via .env |
| `VWAP_SENSITIVITY` | `150.0` | `20.0` | Cat B saturation fix |
| `POC_SENSITIVITY` | `150.0` | `20.0` | Cat B saturation fix |
| `HA_MAX_STREAK` | `3` | `5` | Kurangi noise |
| `HL_L2_BOOK_LEVELS` | `20` | `50` | Wall detection fix |
| `HL_L2_SIG_FIGS` | `5` | `4` | $10 resolution lebih natural di BTC |
| `WALL_BIN_SIZE` | `10.0` | `20.0` | Aggregasi lebih baik |
| `WALL_MIN_SIZE_MULTIPLIER` | `3.0` | `2.0` | Lebih sensitif |
| `WALL_PERSISTENCE_SECONDS` | `5.0` | `3.0` | Sesuaikan dengan prune window |
| `WALL_PRUNE_SECONDS` | *(tidak ada)* | `6.0` | Fix logical impossibility |
| `TIME_EXIT_ENABLED` | *(tidak ada)* | `True` | Limit hold time |
| `TIME_EXIT_MINUTES_5M` | *(tidak ada)* | `30` | 5m source: max 30 menit |
| `TIME_EXIT_MINUTES_15M` | *(tidak ada)* | `90` | 15m source: max 90 menit |

---

## Appendix B: Bug Fix Summary Table

| Bug ID | Severity | File | Status | Fix Summary |
|---|---|---|---|---|
| B1 | P0 | `executor.py` | **FIXED** | Capital double-accounting (TP1 path) |
| B2 | P1 | `executor.py` | **FIXED** | `tp2_hit` flag tidak di-set |
| B3 | P1 | `executor.py` | **FIXED** | `can_trade()` tidak dipanggil |
| B4 | P1 | `feeds/synthetic.py` | **FIXED** | Race condition dengan TTL pruning |
| B5 | P1 | `risk/limits.py` | **FIXED** | Daily reset timing drift |
| B6 | P0 | `feeds/hyperliquid.py` | **FIXED** | Wall prune window < persistence window |
| B7 | P1 | `indicators/scoring.py` | **FIXED** | MTF hard veto diganti soft penalty |
| B8 | P1 | `engine/merge.py` | **FIXED** | Conflict resolution: 15m wins → skip |
| B9 | P2 | `config.py` | **FIXED** | Cat B saturation (sensitivity terlalu tinggi) |
| B10 | P3 | `config.py` | **NOTED** | WEIGHTS dead code (actually it's documentation) |

---

## Appendix C: Telegram Bot Flow Diagram

```
User → Telegram → BotFather Token
           ↓
    telegram_bot_loop() [asyncio task]
           ↓
    Application.start_polling()
           ↓
    User sends /command
           ↓
    handlers.py → reads state (MarketState)
                → reads rt (RuntimeConfig)
                → modifies rt jika mode switch
                → returns formatted message + keyboard
           ↓
    User taps inline button
           ↓
    on_callback() → CallbackQueryHandler
           ↓
    edit_message_text() dengan keyboard baru

OUTBOUND (bot → user):
    executor.py → asyncio.create_task(_send_entry_alert())
                → alerts.alert_entry()
                → app.bot.send_message()
```

---

## Appendix D: Referensi File Output

Semua file siap-deploy tersedia di direktori `bayesmarket_v2_full/`:

```
bayesmarket_v2_full/
├── .env.example                    [REPLACE]
├── config.py                       [REPLACE]
├── main.py                         [REPLACE]
├── requirements.txt                [REPLACE]
├── runtime.py                      [NEW]
├── dashboard/
│   └── terminal.py                 [REPLACE]
├── data/
│   └── state.py                    [REPLACE]
├── deploy/
│   ├── VPS_GUIDE.md                [NEW]
│   ├── bayesmarket.service         [NEW]
│   └── setup.sh                    [NEW]
├── engine/
│   ├── executor.py                 [REPLACE]
│   └── merge.py                    [REPLACE]
├── feeds/
│   ├── hyperliquid.py              [REPLACE]
│   └── synthetic.py                [REPLACE]
├── indicators/
│   └── scoring.py                  [REPLACE]
├── risk/
│   └── limits.py                   [REPLACE]
└── telegram_bot/
    ├── __init__.py                 [NEW]
    ├── alerts.py                   [NEW]
    ├── bot.py                      [NEW]
    ├── handlers.py                 [NEW]
    └── keyboards.py               [NEW]
```

**Files NOT included** (tidak ada perubahan, gunakan versi dari repo existing):
```
bayesmarket/__init__.py
bayesmarket/__main__.py
bayesmarket/report.py
bayesmarket/data/__init__.py
bayesmarket/data/recorder.py
bayesmarket/data/storage.py
bayesmarket/engine/__init__.py
bayesmarket/engine/position.py
bayesmarket/engine/timeframe.py
bayesmarket/feeds/__init__.py
bayesmarket/feeds/binance.py
bayesmarket/indicators/__init__.py
bayesmarket/indicators/momentum.py
bayesmarket/indicators/order_flow.py
bayesmarket/indicators/regime.py
bayesmarket/indicators/structure.py
bayesmarket/risk/__init__.py
bayesmarket/risk/funding.py
bayesmarket/risk/sizing.py
```

---

*Dokumen ini dibuat berdasarkan analisis mendalam terhadap codebase BayesMarket, data SQLite 25+ jam shadow mode, dan screenshot dashboard live. Semua perubahan sudah divalidasi secara logis terhadap data aktual.*
