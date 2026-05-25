from pathlib import Path

from ..utils import (
    logger, flag_write, flag_exists,
    vasp_input_check, vasp_done_check,
)

FLAG_DONE = ".stage4_done"


def run(project_root, info, auto=False):
    root = Path(project_root)
    def_dir = root / "defect"

    if flag_exists(FLAG_DONE, def_dir):
        logger.info("Stage 4 already complete")
        return True

    if not def_dir.is_dir():
        logger.error("defect/ not found. Run Stage 3 first.")
        return False

    # Find all defect subdirectories with VASP inputs that haven't been computed yet
    target_dirs = []
    for d in sorted(def_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if vasp_done_check(str(d)):
            logger.info("  %s: already done, skip", d.name)
            continue
        if vasp_input_check(str(d)):
            target_dirs.append(d)
            logger.info("  %s: needs VASP, will submit", d.name)

    if not target_dirs:
        logger.warning("No defect directories with VASP inputs found in %s", def_dir)
        return False

    try:
        from ..crisp_utils import submit_job, cleanup_duplicate_submissions
    except ImportError:
        logger.error("crisp not available. Install or submit manually.")
        return False

    for d in target_dirs:
        task_name = f"{d.name}_{uuid_short()}"
        submit_job(str(d.resolve()), task_name)

    cleaned = cleanup_duplicate_submissions(str(def_dir))
    if cleaned:
        logger.info("Cleaned up %d duplicate submissions", cleaned)

    logger.info("Submitted %d jobs to crisp", len(target_dirs))
    logger.info("Stage 4 complete. Monitor with: crisp jobs")
    # Stage 4 is "done" when submitted; no need for a persistent flag
    flag_write(FLAG_DONE, def_dir)
    return True


def uuid_short():
    import uuid
    return uuid.uuid4().hex[:8]
