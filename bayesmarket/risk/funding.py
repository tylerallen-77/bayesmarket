"""Funding rate fetch + 3-tier filter."""

import asyncio

import aiohttp
import structlog

from bayesmarket import config
from bayesmarket.data.state import MarketState

logger = structlog.get_logger()


async def funding_poller(state: MarketState) -> None:
    """Fetch funding rate from Hyperliquid every 60 seconds."""
    logger.info("funding_poller_started")

    while True:
        try:
            await _fetch_funding_rate(state)
        except Exception as exc:
            logger.error("funding_fetch_failed", error=str(exc))

        await asyncio.sleep(config.FUNDING_POLL_INTERVAL)


async def _fetch_funding_rate(state: MarketState) -> None:
    """Fetch current funding rate from Hyperliquid REST API."""
    url = f"{config.HL_REST_URL}/info"
    payload = {"type": "metaAndAssetCtxs"}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, ssl=False) as resp:
            if resp.status != 200:
                logger.warning("funding_http_error", status=resp.status)
                return

            data = await resp.json()

    # Response format: [meta_data, [asset_ctx, ...]]
    if not isinstance(data, list) or len(data) < 2:
        logger.warning("funding_unexpected_format")
        return

    asset_ctxs = data[1]
    meta = data[0]

    # Find BTC index
    universe = meta.get("universe", [])
    btc_idx = None
    for i, asset in enumerate(universe):
        if asset.get("name") == config.COIN:
            btc_idx = i
            break

    if btc_idx is None or btc_idx >= len(asset_ctxs):
        logger.warning("funding_btc_not_found")
        return

    btc_ctx = asset_ctxs[btc_idx]
    funding_str = btc_ctx.get("funding", "0")
    rate = float(funding_str)

    state.funding_rate = rate
    state.funding_tier = evaluate_funding_tier(rate)

    logger.debug(
        "funding_updated",
        rate=rate,
        tier=state.funding_tier,
    )


def evaluate_funding_tier(rate: float) -> str:
    """Classify funding rate into tier: safe, caution, or danger."""
    abs_rate = abs(rate)
    if abs_rate < config.FUNDING_TIER_SAFE:
        return "safe"
    elif abs_rate < config.FUNDING_TIER_CAUTION:
        return "caution"
    else:
        return "danger"


def evaluate_funding_filter(
    funding_rate: float,
    intended_side: str,
) -> tuple[str, float]:
    """Evaluate if funding rate blocks or modifies the trade.

    Returns (tier, size_multiplier).
    """
    against = (
        (funding_rate > 0 and intended_side == "long")
        or (funding_rate < 0 and intended_side == "short")
    )

    if not against:
        return "safe", 1.0

    abs_rate = abs(funding_rate)
    if abs_rate < config.FUNDING_TIER_SAFE:
        return "safe", 1.0
    elif abs_rate < config.FUNDING_TIER_CAUTION:
        return "caution", config.FUNDING_CAUTION_SIZE_MULT
    else:
        return "danger", 0.0  # Skip trade
