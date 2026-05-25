import os
import sys
import uuid
from collections import defaultdict

CRISP_ROOT = os.path.expanduser("~/crisp")
sys.path.insert(0, CRISP_ROOT)
sys.path.insert(0, os.path.join(CRISP_ROOT, "scripts"))

from utils.db import get_job_db

import logging
logger = logging.getLogger("pydefect_auto")


def submit_job(local_dir, task_name=None):
    task_name = task_name or uuid.uuid4().hex[:8]
    manager = get_job_db()
    result = manager.register_job(
        task_name=task_name,
        local_dir=os.path.abspath(local_dir),
        status="submit",
    )
    logger.info("Submitted %s -> %s", local_dir, task_name)
    return result


def submit_defect_dirs(base_dir, pattern="*_0"):
    from pathlib import Path
    dirs = sorted(d for d in Path(base_dir).iterdir()
                  if d.is_dir() and d.name.endswith("_0"))
    results = []
    for d in dirs:
        task_name = uuid.uuid4().hex[:8]
        result = submit_job(str(d.resolve()), task_name)
        results.append((d.name, result))
    return results


def list_jobs(show_all=False):
    manager = get_job_db()
    return manager.list_jobs(show_all=show_all)


def job_status(task_name):
    manager = get_job_db()
    job = manager.get_job(task_name)
    return job.get("status") if job else None


def cleanup_duplicate_submissions(base_dir):
    manager = get_job_db()
    jobs = manager.list_jobs(show_all=True)
    by_dir = defaultdict(list)
    abspath = os.path.abspath(base_dir)
    for j in jobs:
        ld = j.get("local_dir", "")
        if abspath in ld:
            by_dir[ld].append(j)
    priority = {"completed": 5, "ready_fetch": 4, "running": 3,
                "submitted": 2, "submit": 1}
    removed = 0
    for ld, entries in by_dir.items():
        if len(entries) <= 1:
            continue
        entries.sort(key=lambda e: priority.get(e.get("status"), 0), reverse=True)
        for j in entries[1:]:
            manager.delete_job(j["task_name"])
            removed += 1
    return removed
