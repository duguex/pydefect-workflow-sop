import os
from pathlib import Path

from .utils import logger

STAGES = [
    ("1", "stage1_unitcell", "unitcell", "Unitcell (perfect cell)"),
    ("2", "stage2_cpd", "cpd", "Competing phases / CPD"),
    ("3", "stage3_defect_gen", "defect_gen", "Defect generation"),
    ("4", "stage4_submit", "submit", "VASP batch submit (crisp)"),
    ("5", "stage5_postproc", "postproc", "Post-processing"),
    ("6", "stage6_doping", "doping", "Incremental doping"),
    ("7", "stage7_complex", "complex", "Complex defects"),
]


def single_run(project_root, info, auto=False, stage_config=None):
    root = Path(project_root)
    stage_config = stage_config or {}
    logger.info("Starting pipeline for %s at %s", info["obj"], root)

    for sn, module_name, stage_key, label in STAGES:
        if not stage_config.get(stage_key, True):
            logger.info("Stage %s (%s): disabled in plan.yaml, skipping", sn, label)
            continue
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
            logger.info("Stage %s (%s): pending. Rerun later.", sn, label)
            return False

    logger.info("Pipeline complete for %s", info["obj"])
    return True
