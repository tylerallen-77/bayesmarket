"""Daily loss limit, cooldown state machine, circuit breakers.

Fixes:
  - check_daily_reset: use date comparison instead of minute-exact check
    Prevents missing reset due to event loop timing drift.
  - update_after_trade: fire Telegram risk alerts (non-blocking)
"""

import asyncio
import time
from datetime import date, datetime, timezone

import structlog

from bayesmarket import config
from bayesmarket.data.state import RiskState

logger = structlog.get_logger()

# Track last reset date to prevent double-reset
_last_reset_date: date = date.min


def can_trade(risk: RiskState, capital: float) -> tuple[bool, str]:
    """Check if trading is currently allowed. Handles state expiry.

    Returns (allowed, reason).
    """
    now = time.time()

    # Expire full stop
    if risk.full_stop_active:
        if now >= risk.full_stop_until:
            risk.full_stop_active = False
            logger.info("full_stop_expired")
            asyncio.create_task(_alert_risk("full_stop_reset", "Full stop expired, trading resumed"))
        else:
            remaining = risk.full_stop_until - now
            return False, f"full_stop ({remaining:.0f}s remaining)"

    # Expire daily pause
    if risk.daily_paused:
        if now >= risk.daily_pause_until:
            risk.daily_paused = False
            logger.info("daily_pause_expired")
        else:
            remaining = risk.daily_pause_until - now
            return False, f"daily_paused ({remaining:.0f}s remaining)"

    # Expire cooldown timeout
    if risk.cooldown_active:
        elapsed = now - risk.cooldown_start_time
        if elapsed >= config.COOLDOWN_RESET_SECONDS:
            risk.cooldown_active = False
            risk.consecutive_losses = 0
            logger.info("cooldown_time_reset", elapsed_s=round(elapsed, 0))
            asyncio.create_task(_alert_risk("cooldown_reset", f"Cooldown expired after {elapsed/60:.0f}m"))

    # Check daily loss limit
    if capital > 0 and risk.daily_pnl <= -(capital * config.DAILY_LOSS_LIMIT):
        risk.daily_paused = True
        risk.daily_pause_until = now + config.DAILY_PAUSE_HOURS * 3600
        msg = (
            f"daily_pnl={risk.daily_pnl:.2f} "
            f"limit={config.DAILY_LOSS_LIMIT*100:.0f}% "
            f"pause={config.DAILY_PAUSE_HOURS}h"
        )
        logger.warning("daily_limit_triggered", **{k: v for k, v in [p.split("=") for p in msg.split()]})
        asyncio.create_task(_alert_risk("daily_pause", msg))
        return False, "daily_loss_limit"

    return True, "ok"


def update_after_trade(risk: RiskState, pnl: float, capital: float) -> None:
    """Update risk state after a trade completes."""
    risk.daily_pnl += pnl
    risk.trades_today += 1

    if pnl < 0:
        risk.consecutive_losses += 1
        risk.consecutive_wins = 0

        if risk.consecutive_losses >= config.COOLDOWN_TRIGGER_LOSSES:
            if risk.cooldown_active:
                # Already in cooldown + hit trigger again → full stop
                risk.full_stop_active = True
                risk.full_stop_until = time.time() + config.FULL_STOP_DURATION_SECONDS
                risk.cooldown_active = False
                msg = (
                    f"consecutive_losses={risk.consecutive_losses} "
                    f"duration={config.FULL_STOP_DURATION_SECONDS/3600:.1f}h"
                )
                logger.warning("full_stop_activated", consecutive_losses=risk.consecutive_losses)
                asyncio.create_task(_alert_risk("full_stop", msg))
            else:
                risk.cooldown_active = True
                risk.cooldown_start_time = time.time()
                msg = (
                    f"consecutive_losses={risk.consecutive_losses} "
                    f"size_mult={config.COOLDOWN_SIZE_MULTIPLIER}"
                )
                logger.warning("cooldown_activated", consecutive_losses=risk.consecutive_losses)
                asyncio.create_task(_alert_risk("cooldown", msg))
    else:
        risk.consecutive_wins += 1
        risk.consecutive_losses = 0

        if risk.cooldown_active and risk.consecutive_wins >= config.COOLDOWN_RESET_WINS:
            risk.cooldown_active = False
            logger.info("cooldown_win_reset", consecutive_wins=risk.consecutive_wins)
            asyncio.create_task(_alert_risk("cooldown_reset", f"Cooldown reset after {risk.consecutive_wins} wins"))

    logger.info(
        "risk_updated",
        daily_pnl=round(risk.daily_pnl, 2),
        trades_today=risk.trades_today,
        consec_losses=risk.consecutive_losses,
        consec_wins=risk.consecutive_wins,
        cooldown=risk.cooldown_active,
    )


def check_daily_reset(risk: RiskState) -> None:
    """Reset daily counters at UTC midnight.

    FIX: Use date comparison (not minute-exact check) to prevent missing
    the reset window due to event loop drift. Guard against double-reset
    in same calendar day.
    """
    global _last_reset_date

    today_utc = datetime.now(timezone.utc).date()

    # Only reset once per UTC calendar day
    if today_utc <= _last_reset_date:
        return

    # Only reset after UTC midnight (hour >= 0 is always true, but
    # ensure we don't reset mid-session on first run)
    now_utc = datetime.now(timezone.utc)
    if now_utc.hour != config.DAILY_RESET_HOUR_UTC:
        return

    _last_reset_date = today_utc
    risk.daily_pnl = 0.0
    risk.trades_today = 0
    risk.daily_paused = False
    risk.daily_pnl_reset_time = time.time()
    logger.info("daily_reset", date=today_utc.isoformat())


async def _alert_risk(event: str, details: str) -> None:
    """Fire Telegram risk alert. Silent-fail."""
    try:
        from bayesmarket.telegram_bot.alerts import alert_risk_event
        await alert_risk_event(event, details)
    except Exception:
        pass
