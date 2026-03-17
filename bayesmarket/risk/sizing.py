"""Position sizing with leverage cap (errata Patch #5)."""

from typing import TYPE_CHECKING, Optional

import structlog

from bayesmarket import config

if TYPE_CHECKING:
    from bayesmarket.runtime import RuntimeConfig

logger = structlog.get_logger()


def calculate_position_size(
    capital: float,
    entry_price: float,
    sl_price: float,
    cooldown_active: bool = False,
    funding_tier: str = "safe",
    runtime: Optional["RuntimeConfig"] = None,
) -> Optional[float]:
    """Calculate position size respecting risk rule and leverage cap.

    Returns size in base asset units, or None if trade should be skipped.
    Uses runtime config values when available, falls back to static config.
    """
    if capital <= 0 or entry_price <= 0:
        return None

    # Step 1: Risk-based sizing (runtime-configurable)
    risk_pct = runtime.max_risk_per_trade if runtime else config.MAX_RISK_PER_TRADE
    risk_amount = capital * risk_pct
    sl_distance = abs(entry_price - sl_price)

    if sl_distance <= 0:
        logger.warning("sl_distance_zero", entry=entry_price, sl=sl_price)
        return None

    risk_based_size = risk_amount / sl_distance

    # Step 2: Apply modifiers
    if cooldown_active:
        risk_based_size *= config.COOLDOWN_SIZE_MULTIPLIER

    if funding_tier == "caution":
        risk_based_size *= config.FUNDING_CAUTION_SIZE_MULT

    # Step 3: Cap by leverage limit (runtime-configurable)
    max_lev = runtime.max_leverage if runtime else config.MAX_LEVERAGE
    max_notional = capital * max_lev
    max_size_by_leverage = max_notional / entry_price
    final_size = min(risk_based_size, max_size_by_leverage)

    # Step 4: Check minimum
    if final_size * entry_price < config.MIN_ORDER_VALUE_USD:
        logger.info(
            "position_too_small",
            notional=round(final_size * entry_price, 2),
            minimum=config.MIN_ORDER_VALUE_USD,
        )
        return None

    logger.info(
        "position_sized",
        size=round(final_size, 6),
        notional=round(final_size * entry_price, 2),
        leverage=round(final_size * entry_price / capital, 2),
        risk_based=round(risk_based_size, 6),
        capped=risk_based_size > max_size_by_leverage,
        cooldown=cooldown_active,
    )

    return final_size
