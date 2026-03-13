"""Hyperliquid WebSocket feeds: l2Book + trades (shared for all TFs).

Includes wall tracker with $10 price binning (errata Patch #2).
"""

import asyncio
import json
import math
import time
from typing import Optional

import structlog
import websockets

from bayesmarket import config
from bayesmarket.data.state import BookLevel, MarketState, TradeEvent, WallInfo

logger = structlog.get_logger()


async def hl_book_feed(state: MarketState) -> None:
    """Subscribe to Hyperliquid l2Book and update state + wall tracker."""
    backoff = 1
    while True:
        try:
            async with websockets.connect(config.HL_WS_URL) as ws:
                sub_msg = json.dumps({
                    "method": "subscribe",
                    "subscription": {
                        "type": "l2Book",
                        "coin": config.COIN,
                        "nSigFigs": config.HL_L2_SIG_FIGS,
                    },
                })
                await ws.send(sub_msg)
                logger.info("hl_book_feed_connected")
                backoff = 1

                async for raw_msg in ws:
                    try:
                        msg = json.loads(raw_msg)
                        _process_l2book(msg, state)
                    except Exception as exc:
                        logger.error("l2book_parse_failed", error=str(exc))

        except (websockets.ConnectionClosed, ConnectionError, OSError) as exc:
            logger.warning("hl_book_feed_disconnected", error=str(exc), backoff=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
        except Exception as exc:
            logger.error("hl_book_feed_error", error=str(exc), backoff=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def _process_l2book(msg: dict, state: MarketState) -> None:
    """Parse l2Book message and update state.bids/asks/mid_price + wall tracker."""
    if msg.get("channel") != "l2Book":
        return

    data = msg.get("data", {})
    levels = data.get("levels", [])
    if len(levels) < 2:
        return

    raw_bids = levels[0]
    raw_asks = levels[1]

    state.bids = [
        BookLevel(price=float(lvl["px"]), size=float(lvl["sz"]), num_orders=int(lvl.get("n", 0)))
        for lvl in raw_bids
    ]
    state.asks = [
        BookLevel(price=float(lvl["px"]), size=float(lvl["sz"]), num_orders=int(lvl.get("n", 0)))
        for lvl in raw_asks
    ]

    if state.bids and state.asks:
        state.mid_price = (state.bids[0].price + state.asks[0].price) / 2.0

    state.book_update_time = time.time()

    _update_wall_tracker(state)


def _update_wall_tracker(state: MarketState) -> None:
    """Wall detection with $10 price binning (errata Patch #2)."""
    now = time.time()
    bin_size = config.WALL_BIN_SIZE

    # Step 1: Aggregate levels into price bins
    bins: dict[str, dict] = {}  # key = "bid_84000" or "ask_84000"

    for lvl in state.bids:
        bin_low = math.floor(lvl.price / bin_size) * bin_size
        bin_center = bin_low + bin_size / 2
        key = f"bid_{bin_low}"
        if key not in bins:
            bins[key] = {
                "bin_center": bin_center,
                "bin_low": bin_low,
                "bin_high": bin_low + bin_size,
                "total_size": 0.0,
                "side": "bid",
            }
        bins[key]["total_size"] += lvl.size

    for lvl in state.asks:
        bin_low = math.floor(lvl.price / bin_size) * bin_size
        bin_center = bin_low + bin_size / 2
        key = f"ask_{bin_low}"
        if key not in bins:
            bins[key] = {
                "bin_center": bin_center,
                "bin_low": bin_low,
                "bin_high": bin_low + bin_size,
                "total_size": 0.0,
                "side": "ask",
            }
        bins[key]["total_size"] += lvl.size

    # Step 2: Compute threshold
    non_zero_totals = [b["total_size"] for b in bins.values() if b["total_size"] > 0]
    if not non_zero_totals:
        return
    avg_bin_size = sum(non_zero_totals) / len(non_zero_totals)
    threshold = avg_bin_size * config.WALL_MIN_SIZE_MULTIPLIER

    # Step 3: Update tracked walls
    existing_walls: dict[str, WallInfo] = {}
    for wall in state.tracked_walls:
        key = f"{wall.side}_{int(wall.bin_low)}"
        existing_walls[key] = wall

    new_tracked: list[WallInfo] = []

    for key, bin_data in bins.items():
        if bin_data["total_size"] >= threshold:
            if key in existing_walls:
                wall = existing_walls[key]
                wall.last_seen = now
                wall.total_size = bin_data["total_size"]
                wall.peak_size = max(wall.peak_size, bin_data["total_size"])
                new_tracked.append(wall)
            else:
                new_tracked.append(WallInfo(
                    bin_center=bin_data["bin_center"],
                    bin_low=bin_data["bin_low"],
                    bin_high=bin_data["bin_high"],
                    total_size=bin_data["total_size"],
                    side=bin_data["side"],
                    first_seen=now,
                    last_seen=now,
                    initial_size=bin_data["total_size"],
                    peak_size=bin_data["total_size"],
                ))

    # Step 4: Prune walls not updated in last 3 seconds
    state.tracked_walls = [w for w in new_tracked if now - w.last_seen < 3.0]


async def hl_trade_feed(state: MarketState) -> None:
    """Subscribe to Hyperliquid trades and update state.trades + synthetic builders."""
    backoff = 1
    while True:
        try:
            async with websockets.connect(config.HL_WS_URL) as ws:
                sub_msg = json.dumps({
                    "method": "subscribe",
                    "subscription": {"type": "trades", "coin": config.COIN},
                })
                await ws.send(sub_msg)
                logger.info("hl_trade_feed_connected")
                backoff = 1

                async for raw_msg in ws:
                    try:
                        msg = json.loads(raw_msg)
                        _process_trades(msg, state)
                    except Exception as exc:
                        logger.error("trades_parse_failed", error=str(exc))

        except (websockets.ConnectionClosed, ConnectionError, OSError) as exc:
            logger.warning("hl_trade_feed_disconnected", error=str(exc), backoff=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
        except Exception as exc:
            logger.error("hl_trade_feed_error", error=str(exc), backoff=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def _process_trades(msg: dict, state: MarketState) -> None:
    """Parse trades message and update state."""
    if msg.get("channel") != "trades":
        return

    data = msg.get("data", [])
    now = time.time()

    for trade_data in data:
        price = float(trade_data["px"])
        size = float(trade_data["sz"])
        is_buy = trade_data["side"] == "B"

        trade = TradeEvent(
            timestamp=now,
            price=price,
            size=size,
            is_buy=is_buy,
            notional=price * size,
        )
        state.trades.append(trade)

        # Update last HL trade time for fallback detection
        for tf_state in state.tf_states.values():
            tf_state.last_hl_trade_time = now

    # Prune old trades
    cutoff = now - config.TRADE_TTL_SECONDS
    while state.trades and state.trades[0].timestamp < cutoff:
        state.trades.popleft()
