"""RuntimeConfig — mutable config yang bisa diubah saat bot berjalan.

Semua parameter yang butuh hot-reload (mode, pause, thresholds)
disimpan di sini, bukan di config.py (yang static).
Diakses via state.runtime di seluruh sistem.
"""

import time
from dataclasses import dataclass, field
from typing import Optional
import structlog

logger = structlog.get_logger()


@dataclass
class RuntimeConfig:
    """Mutable runtime configuration. Thread-safe via asyncio (single event loop)."""

    # ── MODE ──────────────────────────────────────────────────────
    live_mode: bool = False
    trading_paused: bool = False
    pause_reason: str = ""
    paused_at: float = 0.0

    # ── ALERT THRESHOLDS (Telegram) ────────────────────────────────
    alert_on_entry: bool = True
    alert_on_exit: bool = True
    alert_on_sl_hit: bool = True
    alert_on_tp: bool = True
    alert_on_daily_report: bool = True
    alert_score_threshold: float = 0.0   # 0 = always alert on signal

    # ── SHADOW TUNING (hot-reload via Telegram /set) ───────────────
    scoring_threshold_5m: float = 7.0
    bias_threshold: float = 3.0       # 4h cascade bias threshold
    vwap_sensitivity: float = 150.0
    poc_sensitivity: float = 150.0

    # ── INTERNAL ──────────────────────────────────────────────────
    mode_switched_at: float = field(default_factory=time.time)
    mode_switched_by: str = "system"   # "system" | "telegram" | "api"
    total_mode_switches: int = 0

    def switch_to_shadow(self, by: str = "telegram") -> str:
        """Switch ke shadow mode. Returns status message."""
        if not self.live_mode:
            return "⚠️ Sudah dalam SHADOW MODE."
        self.live_mode = False
        self.mode_switched_at = time.time()
        self.mode_switched_by = by
        self.total_mode_switches += 1
        logger.info("mode_switched", to="shadow", by=by)
        return "✅ Switched ke SHADOW MODE. Tidak ada order nyata yang akan dieksekusi."

    def switch_to_live(self, hl_key: str, hl_address: str, by: str = "telegram") -> str:
        """Switch ke live mode. Returns status message."""
        if self.live_mode:
            return "⚠️ Sudah dalam LIVE MODE."
        if not hl_key or not hl_address:
            return "❌ Gagal: HL_PRIVATE_KEY dan HL_ACCOUNT_ADDRESS harus diisi di .env"
        self.live_mode = True
        self.mode_switched_at = time.time()
        self.mode_switched_by = by
        self.total_mode_switches += 1
        logger.warning("mode_switched", to="live", by=by)
        return (
            "🔴 *LIVE MODE AKTIF*\n"
            "Order nyata akan dieksekusi di Hyperliquid.\n"
            "Gunakan /shadow untuk kembali ke shadow mode."
        )

    def pause_trading(self, reason: str = "manual", by: str = "telegram") -> str:
        """Pause semua entry baru (tidak menutup posisi aktif)."""
        if self.trading_paused:
            return f"⚠️ Trading sudah di-pause. Reason: {self.pause_reason}"
        self.trading_paused = True
        self.pause_reason = reason
        self.paused_at = time.time()
        logger.info("trading_paused", reason=reason, by=by)
        return f"⏸️ Trading di-pause. Reason: {reason}\nPosisi aktif tetap dimonitor."

    def resume_trading(self, by: str = "telegram") -> str:
        """Resume trading."""
        if not self.trading_paused:
            return "⚠️ Trading tidak sedang di-pause."
        duration = time.time() - self.paused_at
        self.trading_paused = False
        self.pause_reason = ""
        logger.info("trading_resumed", paused_for_seconds=round(duration), by=by)
        return f"▶️ Trading dilanjutkan. (Pause selama {int(duration//60)}m {int(duration%60)}s)"

    @property
    def network_label(self) -> str:
        from bayesmarket import config
        if not self.live_mode:
            return "🟡 SHADOW"
        return "🟠 TESTNET" if config.IS_TESTNET else "🔴 LIVE"

    @property
    def mode_label(self) -> str:
        return self.network_label

    @property
    def status_label(self) -> str:
        if self.trading_paused:
            return "⏸️ PAUSED"
        return "✅ ACTIVE"
