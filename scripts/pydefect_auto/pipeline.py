import os
from pathlib import Path

from .utils import logger

STAGES = [
    ("1", "stage1_unitcell", "Unitcell (perfect cell)"),
    ("2", "stage2_cpd", "Competing phases / CPD"),
    ("3", "stage3_defect_gen", "Defect generation"),
    ("4", "stage4_submit", "VASP batch submit (crisp)"),
    ("5", "stage5_postproc", "Post-processing"),
    ("6", "stage6_doping", "Incremental doping"),
    ("7", "stage7_complex", "Complex defects"),
]


def single_run(project_root, info, auto=False):
    root = Path(project_root)
    logger.info("Starting full pipeline for %s at %s", info["obj"], root)

    for sn, module_name, label in STAGES:
        try:
            mod = __import__(
                f"pydefect_auto.stages.{module_name}", fromlist=["run"]
            )
        except ImportError:
            logger.warning("Stage %s (%s) not implemented, skipping", sn, label)
            continue

        result = mod.run(str(root), info, auto=auto)
        if result:
            logger.info("Stage %s (%s): done", sn, label)
        else:
            if auto:
                logger.error("Stage %s (%s) failed. Aborting pipeline.", sn, label)
                return False
            logger.info("Stage %s (%s): pending. Rerun to continue.", sn, label)
            return False

    logger.info("Full pipeline complete for %s", info["obj"])
    return True
