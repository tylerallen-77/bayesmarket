"""Telegram bot integration — main bot setup dan polling loop."""

import asyncio
from typing import TYPE_CHECKING

import structlog
from telegram.ext import Application

from bayesmarket import config
from bayesmarket.telegram_bot.alerts import init_alerts
from bayesmarket.telegram_bot.handlers import build_handlers

if TYPE_CHECKING:
    from bayesmarket.data.state import MarketState
    from bayesmarket.runtime import RuntimeConfig

logger = structlog.get_logger()


async def telegram_bot_loop(state: "MarketState", rt: "RuntimeConfig") -> None:
    """Initialize dan jalankan Telegram bot sebagai asyncio task."""
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.warning(
            "telegram_disabled",
            reason="TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in .env"
        )
        return

    logger.info("telegram_bot_starting", chat_id=chat_id)

    app = (
        Application.builder()
        .token(token)
        .build()
    )

    # Register all command/callback handlers
    for handler in build_handlers(state, rt):
        app.add_handler(handler)

    # Init alerts module dengan reference ke app
    init_alerts(app, chat_id)

    try:
        await app.initialize()
        await app.start()

        # Send startup notification
        mode_label = "🔴 LIVE" if rt.live_mode else "🟡 SHADOW"
        await app.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🤖 *BayesMarket Online*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Mode: {mode_label}\n"
                f"Capital: `${state.capital:,.2f}`\n"
                f"Coin: `{config.COIN}`\n\n"
                f"Gunakan /help untuk daftar commands."
            ),
            parse_mode="Markdown",
        )

        # Start polling
        await app.updater.start_polling(
            poll_interval=1.0,
            timeout=10,
            drop_pending_updates=True,
        )

        logger.info("telegram_bot_polling_started")

        # Init and start dashboard push loop
        from bayesmarket.telegram_bot.dashboard_push import (
            dashboard_push_loop,
            init_push_dashboard,
        )
        init_push_dashboard(app, chat_id)
        asyncio.create_task(dashboard_push_loop(state, rt, app, chat_id))

        # Keep alive — task berjalan bersamaan dengan asyncio.gather
        while True:
            await asyncio.sleep(60)

    except asyncio.CancelledError:
        logger.info("telegram_bot_stopping")
    except Exception as exc:
        logger.error("telegram_bot_error", error=str(exc))
    finally:
        try:
            # Send shutdown notification
            await app.bot.send_message(
                chat_id=chat_id,
                text="🔌 *BayesMarket Offline*\nBot dihentikan.",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
