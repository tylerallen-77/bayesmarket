"""Synthetic kline builder from Hyperliquid trade stream.

Fix: _synthetic_trade_router race condition.
  Old approach: track by list index (last_processed counter).
  Problem: TTL pruning via popleft() shifts indices → missed trades.
  Fix: track by last processed trade timestamp, not index.
"""

import math
import time
from collections import deque
from typing import Optional

import structlog

from bayesmarket.data.state import Candle, MarketState, TradeEvent

logger = structlog.get_logger()


class SyntheticKlineBuilder:
    """Build OHLCV candles from a stream of trades."""

    def __init__(self, interval_seconds: int, max_candles: int = 200) -> None:
        self.interval = interval_seconds
        self.max_candles = max_candles
        self.current_candle: Optional[Candle] = None
        self.candle_start_time: Optional[float] = None
        self.completed_candles: deque[Candle] = deque(maxlen=max_candles)

    def on_trade(self, trade: TradeEvent) -> Optional[Candle]:
        """Process a trade, return closed candle if one just completed."""
        bucket_start = math.floor(trade.timestamp / self.interval) * self.interval
        closed_candle: Optional[Candle] = None

        if self.current_candle is None or bucket_start != self.candle_start_time:
            if self.current_candle is not None:
                self.current_candle.closed = True
                closed_candle = self.current_candle
                self.completed_candles.append(closed_candle)

            self.candle_start_time = bucket_start
            self.current_candle = Candle(
                timestamp=bucket_start,
                open=trade.price,
                high=trade.price,
                low=trade.price,
                close=trade.price,
                volume=trade.size,
                closed=False,
            )
        else:
            self.current_candle.high = max(self.current_candle.high, trade.price)
            self.current_candle.low = min(self.current_candle.low, trade.price)
            self.current_candle.close = trade.price
            self.current_candle.volume += trade.size

        return closed_candle


TF_TO_INTERVAL = {
    "5m": 60,
    "15m": 300,
    "1h": 900,
    "4h": 3600,
}


def create_builders(state: MarketState) -> dict[str, SyntheticKlineBuilder]:
    builders = {}
    for tf_name in state.tf_states:
        interval = TF_TO_INTERVAL.get(tf_name)
        if interval:
            max_candles = 200 if tf_name in ("5m", "15m") else 150
            builders[tf_name] = SyntheticKlineBuilder(interval, max_candles)
            logger.info("synthetic_builder_created", tf=tf_name, interval=interval)
    return builders


def feed_trade_to_builders(
    trade: TradeEvent,
    builders: dict[str, SyntheticKlineBuilder],
    state: MarketState,
) -> None:
    for tf_name, builder in builders.items():
        closed = builder.on_trade(trade)
        # Binance WS klines are now the primary source
        # Synthetic builder runs for internal tracking only
        if closed is not None:
            logger.debug(
                "synthetic_kline_closed_internal",
                tf=tf_name,
                close=closed.close,
                volume=closed.volume,
            )


# ── Trade router — standalone function called from main.py ────────────────────

async def synthetic_trade_router(state: MarketState) -> None:
    """Route HL trades to synthetic builders.

    FIX: Track by timestamp instead of index to avoid race with TTL pruning.
    Uses a small lookback window (1s) to catch new trades each cycle.
    """
    import asyncio
    from bayesmarket.feeds.binance import check_fallback_status

    builders = create_builders(state)
    last_processed_ts: float = 0.0

    while True:
        try:
            now = time.time()

            if state.trades:
                # Process trades strictly newer than last processed timestamp
                # FIX CRITICAL-6: removed 50ms overlap that caused duplicate processing
                new_trades = [t for t in state.trades if t.timestamp > last_processed_ts]

                for trade in new_trades:
                    feed_trade_to_builders(trade, builders, state)

                if new_trades:
                    last_processed_ts = max(t.timestamp for t in new_trades)

            check_fallback_status(state)

        except Exception as exc:
            logger.error("synthetic_router_error", error=str(exc))

        await asyncio.sleep(0.1)
