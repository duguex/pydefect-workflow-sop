import asyncio
import json
import os
from pathlib import Path

from .utils import logger, log_setup


async def loop_run(interval=600):
    log_setup()
    logger.info("Watcher started (interval=%ds). Watching for info.json in subdirectories...", interval)

    while True:
        for entry in sorted(Path(".").iterdir()):
            if not entry.is_dir():
                continue
            info_path = entry / "info.json"
            if not info_path.exists():
                continue

            logger.info("Found project: %s", entry.name)
            try:
                from .pipeline import single_run
                from .config import load_info
                info = load_info(str(info_path))
                await asyncio.to_thread(single_run, str(entry.resolve()), info, auto=True)
            except Exception as e:
                logger.error("Error processing %s: %s", entry.name, e)

        await asyncio.sleep(interval)
