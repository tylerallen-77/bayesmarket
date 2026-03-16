"""BayesMarket MVP — Entry point, async orchestration.

Tasks:
- 3 data feeds (HL book, HL trades, Binance fallback)
- 4 signal engines (5m, 15m, 1h, 4h)
- 2 execution loops (merge+execute, position monitor)
- 3 background (funding, daily reset, snapshot recorder)
- 1 Telegram bot (control panel)
- 1 dashboard (terminal)
"""

import asyncio
import signal
import sys
import time

import structlog

from bayesmarket import config
from bayesmarket.data.recorder import snapshot_recorder
from bayesmarket.data.state import MarketState, TimeframeState
from bayesmarket.data.storage import Storage
from bayesmarket.dashboard.terminal import dashboard_loop
from bayesmarket.engine.executor import merge_and_execute_loop, position_monitor_loop
from bayesmarket.engine.timeframe import TimeframeEngine
from bayesmarket.feeds.binance import binance_kline_feed, bootstrap_klines, check_fallback_status
from bayesmarket.feeds.hyperliquid import hl_book_feed, hl_trade_feed
from bayesmarket.feeds.synthetic import synthetic_trade_router
from bayesmarket.risk.funding import funding_poller
from bayesmarket.risk.limits import check_daily_reset
from bayesmarket.runtime import RuntimeConfig
from bayesmarket.telegram_bot.bot import telegram_bot_loop

_use_colors = not config.IS_RAILWAY  # no color codes in Railway logs

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(colors=_use_colors),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()


def _init_state(rt: RuntimeConfig) -> MarketState:
    """Initialize MarketState dengan TimeframeState untuk 4 TF."""
    state = MarketState(capital=config.SIMULATED_CAPITAL)
    state.runtime = rt  # attach runtime config ke state
    for tf_name, tf_cfg in config.TIMEFRAMES.items():
        state.tf_states[tf_name] = TimeframeState(
            name=tf_name,
            role=tf_cfg["role"],
        )
    return state


async def _daily_reset_loop(state: MarketState) -> None:
    while True:
        try:
            check_daily_reset(state.risk)
        except Exception as exc:
            logger.error("daily_reset_error", error=str(exc))
        await asyncio.sleep(60)


async def main() -> None:
    """Main entry point."""
    # Init RuntimeConfig dari .env / defaults
    rt = RuntimeConfig(live_mode=config.LIVE_MODE)

    mode = "LIVE" if rt.live_mode else "SHADOW"
    logger.info(
        "system_starting",
        mode=mode,
        capital=config.SIMULATED_CAPITAL,
        coin=config.COIN,
        telegram_enabled=bool(config.TELEGRAM_BOT_TOKEN),
    )

    state = _init_state(rt)
    storage = Storage()

    storage.insert_event("startup", f"BayesMarket starting in {mode} mode")

    logger.info("bootstrapping_klines")
    await bootstrap_klines(state)

    engines = {
        tf_name: TimeframeEngine(tf_name, state, storage)
        for tf_name in config.TIMEFRAMES
    }

    tasks = [
        # Data feeds
        hl_book_feed(state),
        hl_trade_feed(state),
        binance_kline_feed(state),
        synthetic_trade_router(state),

        # Signal engines
        engines["5m"].run(),
        engines["15m"].run(),
        engines["1h"].run(),
        engines["4h"].run(),

        # Execution
        merge_and_execute_loop(state, storage),
        position_monitor_loop(state, storage),

        # Background
        funding_poller(state),
        _daily_reset_loop(state),
        snapshot_recorder(state, storage),

        # Telegram control panel
        telegram_bot_loop(state, rt),

        # Terminal dashboard — disabled on Railway (no TTY)
        # Monitoring via Telegram instead when IS_RAILWAY=True
        *([dashboard_loop(state)] if not config.IS_RAILWAY else []),
    ]

    logger.info("all_tasks_launching", count=len(tasks))

    loop = asyncio.get_event_loop()

    def _signal_handler() -> None:
        logger.info("shutdown_requested")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    try:
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
