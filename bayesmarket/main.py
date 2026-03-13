"""BayesMarket MVP — Entry point, async orchestration.

13 concurrent tasks via asyncio.gather:
- 3 data feeds (HL book, HL trades, Binance fallback)
- 4 signal loops (5m, 15m, 1h, 4h)
- 2 execution loops (merge+execute, position monitor)
- 3 background (funding, daily reset, snapshot recorder)
- 1 dashboard
"""

import asyncio
import signal
import sys
import time

import structlog

from bayesmarket import config
from bayesmarket.data.recorder import snapshot_recorder
from bayesmarket.data.state import MarketState, TimeframeState, TradeEvent
from bayesmarket.data.storage import Storage
from bayesmarket.dashboard.terminal import dashboard_loop
from bayesmarket.engine.executor import merge_and_execute_loop, position_monitor_loop
from bayesmarket.engine.timeframe import TimeframeEngine
from bayesmarket.feeds.binance import binance_kline_feed, bootstrap_klines, check_fallback_status
from bayesmarket.feeds.hyperliquid import hl_book_feed, hl_trade_feed
from bayesmarket.feeds.synthetic import create_builders, feed_trade_to_builders
from bayesmarket.risk.funding import funding_poller
from bayesmarket.risk.limits import check_daily_reset

# Configure structlog
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(colors=True),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()


def _init_state() -> MarketState:
    """Initialize MarketState with 4 TimeframeState objects."""
    state = MarketState(capital=config.SIMULATED_CAPITAL)
    for tf_name, tf_cfg in config.TIMEFRAMES.items():
        state.tf_states[tf_name] = TimeframeState(
            name=tf_name,
            role=tf_cfg["role"],
        )
    return state


async def _synthetic_trade_router(state: MarketState) -> None:
    """Route HL trades to synthetic kline builders.

    Runs every 0.1s, checking for new trades and feeding them to builders.
    Also checks fallback status.
    """
    builders = create_builders(state)
    last_processed = 0

    while True:
        try:
            # Process new trades
            current_len = len(state.trades)
            if current_len > last_processed:
                # Process only new trades
                new_trades = list(state.trades)[last_processed:]
                for trade in new_trades:
                    feed_trade_to_builders(trade, builders, state)
                last_processed = current_len

            # Check fallback status
            check_fallback_status(state)

        except Exception as exc:
            logger.error("synthetic_router_error", error=str(exc))

        await asyncio.sleep(0.1)


async def _daily_reset_loop(state: MarketState) -> None:
    """Check for daily reset every 60 seconds."""
    while True:
        try:
            check_daily_reset(state.risk)
        except Exception as exc:
            logger.error("daily_reset_error", error=str(exc))

        await asyncio.sleep(60)


async def main() -> None:
    """Main entry point — async orchestration."""
    mode = "LIVE" if config.LIVE_MODE else "SHADOW"
    logger.info(
        "system_starting",
        mode=mode,
        capital=config.SIMULATED_CAPITAL,
        coin=config.COIN,
        kline_source=config.KLINE_SOURCE,
    )

    # Initialize
    state = _init_state()
    storage = Storage()

    storage.insert_event("startup", f"BayesMarket starting in {mode} mode")

    # Bootstrap klines from Binance Futures REST
    logger.info("bootstrapping_klines")
    await bootstrap_klines(state)

    # Create TF engines
    engines = {
        tf_name: TimeframeEngine(tf_name, state, storage)
        for tf_name in config.TIMEFRAMES
    }

    # Build task list
    tasks = [
        # Data feeds
        hl_book_feed(state),
        hl_trade_feed(state),
        binance_kline_feed(state),

        # Synthetic kline routing
        _synthetic_trade_router(state),

        # Signal computation per TF
        engines["5m"].run(),
        engines["15m"].run(),
        engines["1h"].run(),
        engines["4h"].run(),

        # Execution
        merge_and_execute_loop(state, storage),
        position_monitor_loop(state, storage),

        # Risk & data
        funding_poller(state),
        _daily_reset_loop(state),
        snapshot_recorder(state, storage),

        # Dashboard
        dashboard_loop(state),
    ]

    logger.info("all_tasks_launching", count=len(tasks))

    # Handle shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("shutdown_requested")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        # Run all tasks with exception handling
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("task_failed", task_index=i, error=str(result))

    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
    finally:
        storage.insert_event("shutdown", "BayesMarket shutting down")
        storage.close()
        logger.info("system_stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
