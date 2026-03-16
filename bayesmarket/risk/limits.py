"""Daily loss limit, cooldown state machine, circuit breakers.

FIX CRITICAL-2: Removed asyncio.create_task() from synchronous functions.
  can_trade() and update_after_trade() now return pending alerts as list.
  Caller (async context) is responsible for firing them.
"""

import time
from datetime import date, datetime, timezone
from typing import Optional

import structlog

from bayesmarket import config
from bayesmarket.data.state import RiskState

logger = structlog.get_logger()

# Track last reset date to prevent double-reset
_last_reset_date: date = date.min


def can_trade(risk: RiskState, capital: float) -> tuple[bool, str, list[tuple[str, str]]]:
    """Check if trading is currently allowed. Handles state expiry.

    Returns (allowed, reason, pending_alerts).
    Caller must fire pending_alerts via asyncio.create_task.
    """
    now = time.time()
    alerts: list[tuple[str, str]] = []

    # Expire full stop
    if risk.full_stop_active:
        if now >= risk.full_stop_until:
            risk.full_stop_active = False
            logger.info("full_stop_expired")
            alerts.append(("full_stop_reset", "Full stop expired, trading resumed"))
        else:
            remaining = risk.full_stop_until - now
            return False, f"full_stop ({remaining:.0f}s remaining)", alerts

    # Expire daily pause
    if risk.daily_paused:
        if now >= risk.daily_pause_until:
            risk.daily_paused = False
            logger.info("daily_pause_expired")
        else:
            remaining = risk.daily_pause_until - now
            return False, f"daily_paused ({remaining:.0f}s remaining)", alerts

    # Expire cooldown timeout
    if risk.cooldown_active:
        elapsed = now - risk.cooldown_start_time
        if elapsed >= config.COOLDOWN_RESET_SECONDS:
            risk.cooldown_active = False
            risk.consecutive_losses = 0
            logger.info("cooldown_time_reset", elapsed_s=round(elapsed, 0))
            alerts.append(("cooldown_reset", f"Cooldown expired after {elapsed/60:.0f}m"))

    # Check daily loss limit
    if capital > 0 and risk.daily_pnl <= -(capital * config.DAILY_LOSS_LIMIT):
        risk.daily_paused = True
        risk.daily_pause_until = now + config.DAILY_PAUSE_HOURS * 3600
        msg = (
            f"daily_pnl={risk.daily_pnl:.2f} "
            f"limit={config.DAILY_LOSS_LIMIT*100:.0f}% "
            f"pause={config.DAILY_PAUSE_HOURS}h"
        )
        logger.warning("daily_limit_triggered", daily_pnl=risk.daily_pnl)
        alerts.append(("daily_pause", msg))
        return False, "daily_loss_limit", alerts

    return True, "ok", alerts


def update_after_trade(risk: RiskState, pnl: float, capital: float) -> list[tuple[str, str]]:
    """Update risk state after a trade completes.

    Returns list of pending alerts for caller to fire.
    """
    alerts: list[tuple[str, str]] = []
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
                alerts.append(("full_stop", msg))
            else:
                risk.cooldown_active = True
                risk.cooldown_start_time = time.time()
                msg = (
                    f"consecutive_losses={risk.consecutive_losses} "
                    f"size_mult={config.COOLDOWN_SIZE_MULTIPLIER}"
                )
                logger.warning("cooldown_activated", consecutive_losses=risk.consecutive_losses)
                alerts.append(("cooldown", msg))
    else:
        risk.consecutive_wins += 1
        risk.consecutive_losses = 0

        if risk.cooldown_active and risk.consecutive_wins >= config.COOLDOWN_RESET_WINS:
            risk.cooldown_active = False
            logger.info("cooldown_win_reset", consecutive_wins=risk.consecutive_wins)
            alerts.append(("cooldown_reset", f"Cooldown reset after {risk.consecutive_wins} wins"))

    logger.info(
        "risk_updated",
        daily_pnl=round(risk.daily_pnl, 2),
        trades_today=risk.trades_today,
        consec_losses=risk.consecutive_losses,
        consec_wins=risk.consecutive_wins,
        cooldown=risk.cooldown_active,
    )
    return alerts


def check_daily_reset(risk: RiskState) -> None:
    """Reset daily counters at UTC midnight.

    FIX: Use date comparison (not minute-exact check) to prevent missing
    the reset window due to event loop drift. Guard against double-reset
    in same calendar day.
    """
    global _last_reset_date

    today_utc = datetime.now(timezone.utc).date()

    # FIX MOD-1: Only check date, not hour. Previous hour check could miss
    # reset window if bot restarted after DAILY_RESET_HOUR_UTC.
    if today_utc <= _last_reset_date:
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
