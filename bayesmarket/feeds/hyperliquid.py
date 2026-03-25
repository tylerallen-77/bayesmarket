"""Hyperliquid WebSocket feeds: l2Book + trades.

Uses aiohttp WebSocket client for better compatibility with Railway proxy.

Fixes:
  - Wall pruning window uses config.WALL_PRUNE_SECONDS (> WALL_PERSISTENCE_SECONDS)
  - HL_L2_BOOK_LEVELS increased to 50 in config for better wall detection.
"""

import asyncio
import json
import math
import ssl
import time

import aiohttp
import certifi
import structlog

from bayesmarket import config
from bayesmarket.data.state import BookLevel, MarketState, TradeEvent, WallInfo

logger = structlog.get_logger()


def _create_ssl_context() -> ssl.SSLContext:
    """Create SSL context using certifi CA bundle."""
    return ssl.create_default_context(cafile=certifi.where())


async def hl_book_feed(state: MarketState) -> None:
    """Subscribe to Hyperliquid l2Book and update state + wall tracker."""
    backoff = 1
    ssl_ctx = _create_ssl_context()

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    config.HL_WS_URL,
                    ssl=ssl_ctx,
                    heartbeat=20,
                    timeout=aiohttp.ClientWSTimeout(ws_close=5),
                ) as ws:
                    sub_msg = json.dumps({
                        "method": "subscribe",
                        "subscription": {
                            "type": "l2Book",
                            "coin": config.COIN,
                            "nSigFigs": config.HL_L2_SIG_FIGS,
                            "nLevels": config.HL_L2_BOOK_LEVELS,
                        },
                    })
                    await ws.send_str(sub_msg)
                    logger.info("hl_book_feed_connected", levels=config.HL_L2_BOOK_LEVELS, sig_figs=config.HL_L2_SIG_FIGS)
                    backoff = 1

                    msg_count = 0
                    async for raw_msg in ws:
                        if raw_msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                msg = json.loads(raw_msg.data)
                                _process_l2book(msg, state)
                                msg_count += 1
                            except Exception as exc:
                                logger.error("l2book_parse_failed", error=str(exc))
                        elif raw_msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            logger.warning("hl_book_ws_closed", type=str(raw_msg.type), msgs_received=msg_count)
                            break

                    # Loop exited normally (server closed connection)
                    if msg_count == 0:
                        logger.warning("hl_book_feed_zero_messages", hint="subscription may be rejected")

        except (aiohttp.WSServerHandshakeError, aiohttp.ClientError, OSError) as exc:
            logger.warning("hl_book_feed_disconnected", error=str(exc), backoff=backoff)
            asyncio.create_task(_ws_disconnect_alert("HL Book", str(exc)))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
        except Exception as exc:
            logger.error("hl_book_feed_error", error=str(exc), backoff=backoff)
            asyncio.create_task(_ws_disconnect_alert("HL Book", str(exc)))
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

    # DEBUG LOG — validate book data after subscription fix
    if raw_bids or raw_asks:
        logger.debug(
            "l2book_update",
            bid_levels=len(raw_bids),
            ask_levels=len(raw_asks),
            best_bid=raw_bids[0]["px"] if raw_bids else None,
            best_ask=raw_asks[0]["px"] if raw_asks else None,
        )

    state.bids = [
        BookLevel(
            price=float(lvl["px"]),
            size=float(lvl["sz"]),
            num_orders=int(lvl.get("n", 0)),
        )
        for lvl in raw_bids
    ]
    state.asks = [
        BookLevel(
            price=float(lvl["px"]),
            size=float(lvl["sz"]),
            num_orders=int(lvl.get("n", 0)),
        )
        for lvl in raw_asks
    ]

    if state.bids and state.asks:
        state.mid_price = (state.bids[0].price + state.asks[0].price) / 2.0

    state.book_update_time = time.time()
    _update_wall_tracker(state)


def _update_wall_tracker(state: MarketState) -> None:
    """Wall detection with price binning.

    Fixes:
      - WALL_BIN_SIZE increased to 20 (better aggregation at BTC prices)
      - WALL_MIN_SIZE_MULTIPLIER lowered to 2.0 (more sensitive)
      - Pruning at WALL_PRUNE_SECONDS (> WALL_PERSISTENCE_SECONDS)
    """
    now = time.time()
    bin_size = config.WALL_BIN_SIZE

    # Step 1: Aggregate levels into price bins
    bins: dict[str, dict] = {}

    for lvl in state.bids:
        bin_low = math.floor(lvl.price / bin_size) * bin_size
        key = f"bid_{int(bin_low)}"
        if key not in bins:
            bins[key] = {
                "bin_center": bin_low + bin_size / 2,
                "bin_low": bin_low,
                "bin_high": bin_low + bin_size,
                "total_size": 0.0,
                "side": "bid",
            }
        bins[key]["total_size"] += lvl.size

    for lvl in state.asks:
        bin_low = math.floor(lvl.price / bin_size) * bin_size
        key = f"ask_{int(bin_low)}"
        if key not in bins:
            bins[key] = {
                "bin_center": bin_low + bin_size / 2,
                "bin_low": bin_low,
                "bin_high": bin_low + bin_size,
                "total_size": 0.0,
                "side": "ask",
            }
        bins[key]["total_size"] += lvl.size

    # Step 2: Compute threshold
    non_zero = [b["total_size"] for b in bins.values() if b["total_size"] > 0]
    if not non_zero:
        return
    avg_bin_size = sum(non_zero) / len(non_zero)
    threshold = avg_bin_size * config.WALL_MIN_SIZE_MULTIPLIER

    # Step 3: Update tracked walls
    existing: dict[str, WallInfo] = {}
    for wall in state.tracked_walls:
        key = f"{wall.side}_{int(wall.bin_low)}"
        existing[key] = wall

    new_tracked: list[WallInfo] = []

    for key, bin_data in bins.items():
        if bin_data["total_size"] >= threshold:
            if key in existing:
                wall = existing[key]
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

    # Step 4: Prune walls not seen recently
    prune_window = getattr(config, "WALL_PRUNE_SECONDS", config.WALL_PERSISTENCE_SECONDS + 2.0)
    state.tracked_walls = [w for w in new_tracked if now - w.last_seen < prune_window]

    # DEBUG LOG — newly detected walls
    for wall in state.tracked_walls:
        if wall.age_seconds < 2.0:
            logger.info(
                "wall_detected",
                side=wall.side,
                price=wall.bin_center,
                size=wall.total_size,
                age=round(wall.age_seconds, 1),
            )


async def hl_trade_feed(state: MarketState) -> None:
    """Subscribe to Hyperliquid trades and update state.trades + synthetic builders."""
    backoff = 1
    ssl_ctx = _create_ssl_context()

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    config.HL_WS_URL,
                    ssl=ssl_ctx,
                    heartbeat=20,
                    timeout=aiohttp.ClientWSTimeout(ws_close=5),
                ) as ws:
                    sub_msg = json.dumps({
                        "method": "subscribe",
                        "subscription": {"type": "trades", "coin": config.COIN},
                    })
                    await ws.send_str(sub_msg)
                    logger.info("hl_trade_feed_connected")
                    backoff = 1

                    async for raw_msg in ws:
                        if raw_msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                msg = json.loads(raw_msg.data)
                                _process_trades(msg, state)
                            except Exception as exc:
                                logger.error("trades_parse_failed", error=str(exc))
                        elif raw_msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break

        except (aiohttp.WSServerHandshakeError, aiohttp.ClientError, OSError) as exc:
            logger.warning("hl_trade_feed_disconnected", error=str(exc), backoff=backoff)
            asyncio.create_task(_ws_disconnect_alert("HL Trades", str(exc)))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
        except Exception as exc:
            logger.error("hl_trade_feed_error", error=str(exc), backoff=backoff)
            asyncio.create_task(_ws_disconnect_alert("HL Trades", str(exc)))
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

        for tf_state in state.tf_states.values():
            tf_state.last_hl_trade_time = now

    # Prune old trades
    cutoff = now - config.TRADE_TTL_SECONDS
    while state.trades and state.trades[0].timestamp < cutoff:
        state.trades.popleft()


_ws_alert_last: dict[str, float] = {}
_WS_ALERT_COOLDOWN = 300  # max 1 alert per feed per 5 minutes


async def _ws_disconnect_alert(feed_name: str, error: str) -> None:
    """Send Telegram alert on WebSocket disconnect (rate-limited)."""
    now = time.time()
    last = _ws_alert_last.get(feed_name, 0)
    if now - last < _WS_ALERT_COOLDOWN:
        return
    _ws_alert_last[feed_name] = now
    try:
        from bayesmarket.telegram_bot.alerts import send_alert
        msg = f"\u26a0\ufe0f *WS DISCONNECTED \u2014 {feed_name}*\n`{error[:100]}`\nReconnecting..."
        await send_alert(msg)
    except Exception:
        pass
