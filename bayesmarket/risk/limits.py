"""Daily loss limit, cooldown state machine, circuit breakers."""

import time

import structlog

from bayesmarket import config
from bayesmarket.data.state import RiskState

logger = structlog.get_logger()


def can_trade(risk: RiskState, capital: float) -> tuple[bool, str]:
    """Check if trading is currently allowed.

    Returns (allowed, reason).
    """
    now = time.time()

    # Check full stop
    if risk.full_stop_active:
        if now >= risk.full_stop_until:
            risk.full_stop_active = False
            logger.info("full_stop_expired")
        else:
            remaining = risk.full_stop_until - now
            return False, f"full_stop ({remaining:.0f}s remaining)"

    # Check daily pause
    if risk.daily_paused:
        if now >= risk.daily_pause_until:
            risk.daily_paused = False
            logger.info("daily_pause_expired")
        else:
            remaining = risk.daily_pause_until - now
            return False, f"daily_paused ({remaining:.0f}s remaining)"

    # Check daily loss limit
    if capital > 0 and risk.daily_pnl <= -(capital * config.DAILY_LOSS_LIMIT):
        risk.daily_paused = True
        risk.daily_pause_until = now + config.DAILY_PAUSE_HOURS * 3600
        logger.warning(
            "daily_limit_triggered",
            daily_pnl=round(risk.daily_pnl, 2),
            limit_pct=config.DAILY_LOSS_LIMIT * 100,
            pause_hours=config.DAILY_PAUSE_HOURS,
        )
        return False, "daily_loss_limit"

    # Check cooldown timeout
    if risk.cooldown_active:
        elapsed = now - risk.cooldown_start_time
        if elapsed >= config.COOLDOWN_RESET_SECONDS:
            risk.cooldown_active = False
            risk.consecutive_losses = 0
            logger.info("cooldown_time_reset", elapsed_s=round(elapsed, 0))

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
                # Already in cooldown and hit loss threshold again -> full stop
                risk.full_stop_active = True
                risk.full_stop_until = time.time() + config.FULL_STOP_DURATION_SECONDS
                risk.cooldown_active = False
                logger.warning(
                    "full_stop_activated",
                    consecutive_losses=risk.consecutive_losses,
                    duration_h=config.FULL_STOP_DURATION_SECONDS / 3600,
                )
            else:
                risk.cooldown_active = True
                risk.cooldown_start_time = time.time()
                logger.warning(
                    "cooldown_activated",
                    consecutive_losses=risk.consecutive_losses,
                    size_mult=config.COOLDOWN_SIZE_MULTIPLIER,
                )
    else:
        risk.consecutive_wins += 1
        risk.consecutive_losses = 0

        if risk.cooldown_active and risk.consecutive_wins >= config.COOLDOWN_RESET_WINS:
            risk.cooldown_active = False
            logger.info("cooldown_win_reset", consecutive_wins=risk.consecutive_wins)

    logger.info(
        "risk_updated",
        daily_pnl=round(risk.daily_pnl, 2),
        trades_today=risk.trades_today,
        consec_losses=risk.consecutive_losses,
        consec_wins=risk.consecutive_wins,
        cooldown=risk.cooldown_active,
    )


def check_daily_reset(risk: RiskState) -> None:
    """Reset daily counters at 00:00 UTC."""
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    if now.hour == config.DAILY_RESET_HOUR_UTC and now.minute == 0:
        reset_time = time.time()
        # Avoid double-reset within same minute
        if reset_time - risk.daily_pnl_reset_time > 120:
            risk.daily_pnl = 0.0
            risk.trades_today = 0
            risk.daily_paused = False
            risk.daily_pnl_reset_time = reset_time
            logger.info("daily_reset", time=now.isoformat())
