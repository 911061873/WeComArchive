"""Development entry point; see examples/basic.py for configuration."""

import asyncio
import logging

from examples.basic import main

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
