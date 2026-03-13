"""Synthetic kline builder from Hyperliquid trade stream (errata Patch #1).

One SyntheticKlineBuilder instance per timeframe interval.
Each receives the same trade stream and aggregates into candles.
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
        """Process a trade and return a completed candle if one just closed.

        Returns the closed candle or None.
        """
        bucket_start = math.floor(trade.timestamp / self.interval) * self.interval

        closed_candle: Optional[Candle] = None

        if self.current_candle is None or bucket_start != self.candle_start_time:
            # New candle period
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
            # Update current candle
            self.current_candle.high = max(self.current_candle.high, trade.price)
            self.current_candle.low = min(self.current_candle.low, trade.price)
            self.current_candle.close = trade.price
            self.current_candle.volume += trade.size

        return closed_candle


# Mapping from TF name to kline interval in seconds
TF_TO_INTERVAL = {
    "5m": 60,      # 1m candles for 5m TF
    "15m": 300,    # 5m candles for 15m TF
    "1h": 900,     # 15m candles for 1h TF
    "4h": 3600,    # 1h candles for 4h TF
}


def create_builders(state: MarketState) -> dict[str, SyntheticKlineBuilder]:
    """Create one SyntheticKlineBuilder per timeframe."""
    builders = {}
    for tf_name, tf_cfg in state.tf_states.items():
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
    """Feed a single trade to all synthetic builders and update TF klines."""
    for tf_name, builder in builders.items():
        closed = builder.on_trade(trade)
        if closed is not None:
            tf_state = state.tf_states.get(tf_name)
            if tf_state and not tf_state.using_fallback:
                tf_state.klines.append(closed)
                logger.debug(
                    "synthetic_kline_closed",
                    tf=tf_name,
                    ts=closed.timestamp,
                    close=closed.close,
                    volume=closed.volume,
                )
