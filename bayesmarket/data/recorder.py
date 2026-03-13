"""Market snapshot recorder (every 10s to SQLite)."""

import asyncio

import structlog

from bayesmarket.data.state import MarketState
from bayesmarket.data.storage import Storage

logger = structlog.get_logger()


async def snapshot_recorder(state: MarketState, storage: Storage) -> None:
    """Record market state snapshot to SQLite every 10 seconds."""
    logger.info("snapshot_recorder_started")

    while True:
        try:
            if state.mid_price > 0:
                storage.insert_snapshot(state)
        except Exception as exc:
            logger.error("snapshot_record_failed", error=str(exc))

        await asyncio.sleep(10.0)
