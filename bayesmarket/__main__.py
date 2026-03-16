"""Allow running as: python -m bayesmarket"""

import asyncio
from bayesmarket import config
from bayesmarket.main import main
from bayesmarket.startup import run_startup_wizard

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
