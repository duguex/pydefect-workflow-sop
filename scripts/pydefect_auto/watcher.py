import time
from pathlib import Path

from .utils import logger, log_setup
from .config import load_plan
from .pipeline import single_run

WATCH_INTERVAL = 600


def loop_run(interval=None):
    interval = interval or WATCH_INTERVAL
    log_setup()
    logger.info("Watcher started (interval=%ds). Watching for plan.yaml in subdirs...", interval)

    while True:
        for entry in sorted(Path(".").iterdir()):
            if not entry.is_dir():
                continue
            plan_path = entry / "plan.yaml"
            if not plan_path.exists():
                continue

            logger.info("Found project: %s", entry.name)
            try:
                info, raw = load_plan(str(entry.resolve()))
                stages_cfg = raw.get("stages", {}) if raw else {}
                single_run(str(entry.resolve()), info, auto=True, stage_config=stages_cfg)
            except Exception as e:
                logger.error("Error processing %s: %s", entry.name, e)

        time.sleep(interval)
