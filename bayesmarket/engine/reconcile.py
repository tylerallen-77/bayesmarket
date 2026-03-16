"""Position reconciliation on startup — query HL for orphaned positions.

FIX CRITICAL-7: If bot restarts while a position is open on Hyperliquid,
the in-memory state is lost but the exchange position persists. Without
reconciliation, the bot could open a second position or leave the first
unmonitored (no SL).

This module queries the HL REST API on startup and restores position state
if one exists. Shadow mode skips reconciliation (no real positions).
"""

import time
from typing import Optional

import aiohttp
import structlog

from bayesmarket import config
from bayesmarket.data.state import MarketState, Position

logger = structlog.get_logger()


async def reconcile_positions(state: MarketState) -> None:
    """Query Hyperliquid for existing positions on startup.

    - If live/testnet mode and a BTC position exists on exchange,
      restore it into state.position with conservative SL.
    - If shadow mode, skip (no real positions to reconcile).
    """
    rt = state.runtime
    if not rt or not rt.live_mode:
        logger.info("reconcile_skipped", reason="shadow_mode")
        return

    if not config.HL_ACCOUNT_ADDRESS:
        logger.warning("reconcile_skipped", reason="no_account_address")
        return

    try:
        position_data = await _fetch_hl_positions()
        if position_data is None:
            return

        btc_pos = _find_btc_position(position_data)
        if btc_pos is None:
            logger.info("reconcile_no_open_position")
            return

        # Restore into state
        restored = _restore_position(btc_pos, state)
        if restored:
            state.position = restored
            logger.warning(
                "reconcile_position_restored",
                side=restored.side,
                entry_price=restored.entry_price,
                size=restored.size,
                sl_price=restored.sl_price,
            )

            # Alert via Telegram
            try:
                from bayesmarket.telegram_bot.alerts import send_alert
                msg = (
                    "⚠️ *POSITION RECONCILED ON STARTUP*\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"Side: `{restored.side.upper()}`\n"
                    f"Entry: `${restored.entry_price:,.1f}`\n"
                    f"Size: `{restored.size:.5f} BTC`\n"
                    f"SL: `${restored.sl_price:,.1f}` `[emergency]`\n\n"
                    "Position was found on exchange after restart.\n"
                    "SL set to emergency 3%. Monitor closely."
                )
                import asyncio
                asyncio.create_task(send_alert(msg))
            except Exception:
                pass

    except Exception as exc:
        logger.error("reconcile_failed", error=str(exc))


async def _fetch_hl_positions() -> Optional[list]:
    """Fetch user state from Hyperliquid REST API."""
    url = f"{config.HL_REST_URL}/info"
    payload = {
        "type": "clearinghouseState",
        "user": config.HL_ACCOUNT_ADDRESS,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, ssl=False, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.error("reconcile_api_error", status=resp.status)
                    return None
                data = await resp.json()
                return data.get("assetPositions", [])
    except Exception as exc:
        logger.error("reconcile_fetch_failed", error=str(exc))
        return None


def _find_btc_position(positions: list) -> Optional[dict]:
    """Find BTC position in HL position list."""
    for pos_wrapper in positions:
        pos = pos_wrapper.get("position", {})
        coin = pos.get("coin", "")
        szi = float(pos.get("szi", "0"))
        if coin == config.COIN and abs(szi) > 0:
            return pos
    return None


def _restore_position(hl_pos: dict, state: MarketState) -> Optional[Position]:
    """Build Position object from HL API data with emergency SL."""
    try:
        szi = float(hl_pos.get("szi", "0"))
        entry_price = float(hl_pos.get("entryPx", "0"))

        if abs(szi) == 0 or entry_price <= 0:
            return None

        side = "long" if szi > 0 else "short"
        size = abs(szi)

        # Emergency SL: 3% from entry
        emergency_dist = entry_price * config.EMERGENCY_SL_PCT / 100
        if side == "long":
            sl_price = entry_price - emergency_dist
        else:
            sl_price = entry_price + emergency_dist

        # Conservative TP: 1% from entry
        if side == "long":
            tp1_price = entry_price * 1.005
            tp2_price = entry_price * 1.01
        else:
            tp1_price = entry_price * 0.995
            tp2_price = entry_price * 0.99

        pos = Position(
            side=side,
            entry_price=entry_price,
            size=size,
            remaining_size=size,
            source_tfs=["reconciled"],
            sl_price=sl_price,
            sl_basis="emergency",
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            tp1_size=size * config.TP1_SIZE_PCT,
            tp2_size=size * config.TP2_SIZE_PCT,
            entry_time=time.time(),
            entry_score_5m=0.0,
            entry_score_15m=0.0,
        )
        return pos

    except Exception as exc:
        logger.error("reconcile_restore_failed", error=str(exc))
        return None
