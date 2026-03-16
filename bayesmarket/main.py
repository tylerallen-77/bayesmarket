"""BayesMarket MVP — Entry point, async orchestration.

Cascade MTF Architecture:
  4h (BIAS) -> 1h (CONTEXT) -> 15m (TIMING zone) -> 5m (TRIGGER)
  Only 5m generates trade entries. Higher TFs filter and confirm.

Tasks:
- 3 data feeds (HL book, HL trades, Binance fallback) + synthetic router
- 4 signal engines: 4h bias, 1h context, 15m timing, 5m trigger
- 2 execution loops (cascade entry, position monitor)
- 3 background (funding, daily reset, snapshot recorder)
- 1 Telegram bot (control panel + push dashboard)
- 1 terminal dashboard (disabled on Railway)
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
from bayesmarket.startup import StartupConfig, apply_startup_config, run_startup_wizard
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


async def main(startup_cfg: StartupConfig | None = None) -> None:
    """Main entry point."""
    # Apply startup wizard config if provided
    if startup_cfg and not startup_cfg.skip_wizard:
        apply_startup_config(startup_cfg)

    # Init RuntimeConfig dari config (possibly overridden by wizard)
    rt = RuntimeConfig(live_mode=config.LIVE_MODE)

    # Apply wizard overrides to runtime hot-reload params
    if startup_cfg and not startup_cfg.skip_wizard:
        rt.scoring_threshold_5m = startup_cfg.scoring_threshold
        rt.bias_threshold = startup_cfg.bias_threshold
        rt.vwap_sensitivity = startup_cfg.vwap_sensitivity
        rt.poc_sensitivity = startup_cfg.poc_sensitivity

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

        # Cascade signal engines (4h BIAS -> 1h CTX -> 15m TIMING -> 5m TRIGGER)
        engines["4h"].run(),      # bias: sets allowed direction
        engines["1h"].run(),      # context: confirms bias
        engines["15m"].run(),     # timing: establishes entry zone
        engines["5m"].run(),      # trigger: executes within zone

        # Execution (5m trigger only, no merge needed)
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
        # Skip wizard on Railway (no TTY, use Telegram /setup)
        if config.IS_RAILWAY:
            asyncio.run(main())
        else:
            sc = run_startup_wizard()
            asyncio.run(main(sc))
    except KeyboardInterrupt:
        pass
