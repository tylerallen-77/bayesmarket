"""Binance FUTURES WebSocket + REST feeds (FALLBACK only).

Provides bootstrap klines on startup and fallback kline stream
when Hyperliquid trades go stale.
"""

import asyncio
import json
import ssl
import time
from collections import deque

import aiohttp
import certifi
import structlog
import websockets

from bayesmarket import config
from bayesmarket.data.state import Candle, MarketState

logger = structlog.get_logger()

# Map TF name -> Binance kline interval string
TF_TO_BINANCE_INTERVAL = {
    "5m": "1m",
    "15m": "5m",
    "1h": "15m",
    "4h": "1h",
}


def _create_ssl_context() -> ssl.SSLContext:
    """Create SSL context using certifi CA bundle."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    return ctx


async def bootstrap_klines(state: MarketState) -> None:
    """Fetch initial kline history from Binance Futures REST for all TFs.

    Gracefully handles Binance being unreachable (e.g., corporate network).
    Synthetic klines from HL trades will fill in once trading data arrives.
    """
    ssl_ctx = _create_ssl_context()
    loaded = 0

    async with aiohttp.ClientSession() as session:
        for tf_name, tf_state in state.tf_states.items():
            tf_cfg = config.TIMEFRAMES[tf_name]
            interval = tf_cfg["kline_interval"]
            limit = tf_cfg["kline_bootstrap"]
            url = f"{config.BINANCE_FUTURES_REST_URL}/klines"
            params = {
                "symbol": config.BINANCE_SYMBOL,
                "interval": interval,
                "limit": limit,
            }

            for attempt in range(3):
                try:
                    async with session.get(url, params=params, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            logger.warning(
                                "bootstrap_klines_http_error",
                                tf=tf_name,
                                status=resp.status,
                                attempt=attempt + 1,
                            )
                            await asyncio.sleep(2)
                            continue

                        data = await resp.json()
                        candles = _parse_rest_klines(data)
                        tf_state.klines = deque(candles, maxlen=tf_cfg["kline_max"])
                        loaded += 1
                        logger.info(
                            "bootstrap_klines_loaded",
                            tf=tf_name,
                            interval=interval,
                            count=len(candles),
                        )
                        break

                except Exception as exc:
                    logger.error(
                        "bootstrap_klines_failed",
                        tf=tf_name,
                        error=str(exc),
                        attempt=attempt + 1,
                    )
                    if attempt < 2:
                        await asyncio.sleep(2)
                    else:
                        logger.warning(
                            "bootstrap_klines_skipped",
                            tf=tf_name,
                            reason="Binance unreachable — synthetic klines will populate from HL trades",
                        )

    if loaded == 0:
        logger.warning(
            "bootstrap_klines_all_failed",
            reason="Binance completely unreachable. System will start with empty klines. "
                   "Indicators will activate once synthetic klines build up from HL trade stream.",
        )


def _parse_rest_klines(data: list) -> list[Candle]:
    """Parse Binance REST kline response into Candle objects."""
    candles = []
    for item in data:
        candles.append(Candle(
            timestamp=item[0] / 1000.0,
            open=float(item[1]),
            high=float(item[2]),
            low=float(item[3]),
            close=float(item[4]),
            volume=float(item[5]),
            closed=True,
        ))
    return candles


async def binance_kline_feed(state: MarketState) -> None:
    """Single multiplexed Binance Futures WebSocket for fallback klines."""
    streams = "/".join(
        f"{config.BINANCE_SYMBOL.lower()}@kline_{interval}"
        for interval in ["1m", "5m", "15m", "1h"]
    )
    ws_url = f"{config.BINANCE_FUTURES_WS_URL}?streams={streams}"

    # Reverse map: interval string -> TF name
    interval_to_tf: dict[str, str] = {}
    for tf_name, tf_cfg in config.TIMEFRAMES.items():
        interval_to_tf[tf_cfg["kline_interval"]] = tf_name

    backoff = 1
    while True:
        try:
            async with websockets.connect(ws_url, ssl=_create_ssl_context()) as ws:
                logger.info("binance_kline_feed_connected")
                backoff = 1

                async for raw_msg in ws:
                    try:
                        msg = json.loads(raw_msg)
                        _process_binance_kline(msg, state, interval_to_tf)
                    except Exception as exc:
                        logger.error("binance_kline_parse_failed", error=str(exc))

        except (websockets.ConnectionClosed, ConnectionError, OSError) as exc:
            logger.warning(
                "binance_kline_feed_disconnected",
                error=str(exc),
                backoff=backoff,
            )
            asyncio.create_task(_ws_disconnect_alert("Binance Kline", str(exc)))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
        except Exception as exc:
            logger.error("binance_kline_feed_error", error=str(exc), backoff=backoff)
            asyncio.create_task(_ws_disconnect_alert("Binance Kline", str(exc)))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def _process_binance_kline(
    msg: dict,
    state: MarketState,
    interval_to_tf: dict[str, str],
) -> None:
    """Parse Binance kline WebSocket message and route to correct TF."""
    data = msg.get("data", {})
    kline_data = data.get("k", {})
    if not kline_data:
        return

    interval = kline_data.get("i", "")
    tf_name = interval_to_tf.get(interval)
    if not tf_name:
        return

    tf_state = state.tf_states.get(tf_name)
    if not tf_state:
        return

    candle = Candle(
        timestamp=kline_data["t"] / 1000.0,
        open=float(kline_data["o"]),
        high=float(kline_data["h"]),
        low=float(kline_data["l"]),
        close=float(kline_data["c"]),
        volume=float(kline_data["v"]),
        closed=kline_data.get("x", False),
    )

    # Only use Binance data when in fallback mode
    if tf_state.using_fallback:
        if candle.closed:
            tf_state.klines.append(candle)
            logger.debug("binance_fallback_kline_closed", tf=tf_name, close=candle.close)
        else:
            tf_state.current_kline = candle


def check_fallback_status(state: MarketState) -> None:
    """Check if synthetic klines should switch to/from Binance fallback."""
    if not config.KLINE_FALLBACK_ENABLED:
        return

    now = time.time()

    for tf_name, tf_state in state.tf_states.items():
        stale = (now - tf_state.last_hl_trade_time) > config.KLINE_FALLBACK_STALE_SECONDS

        if stale and not tf_state.using_fallback and tf_state.last_hl_trade_time > 0:
            tf_state.using_fallback = True
            state.kline_source = "binance_futures"
            logger.warning(
                "fallback_activated",
                tf=tf_name,
                stale_seconds=now - tf_state.last_hl_trade_time,
            )

        elif not stale and tf_state.using_fallback:
            tf_state.using_fallback = False
            state.kline_source = "synthetic"
            logger.info("fallback_recovered", tf=tf_name)


async def _ws_disconnect_alert(feed_name: str, error: str) -> None:
    """Send Telegram alert on WebSocket disconnect (MOD-3)."""
    try:
        from bayesmarket.telegram_bot.alerts import send_alert
        msg = f"⚠️ *WS DISCONNECTED — {feed_name}*\n`{error[:100]}`\nReconnecting..."
        await send_alert(msg)
    except Exception:
        pass
