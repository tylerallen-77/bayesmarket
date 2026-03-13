"""Allow running as: python -m bayesmarket"""

import asyncio
from bayesmarket.main import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
