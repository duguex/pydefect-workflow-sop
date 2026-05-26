"""plan.yaml 读取 → stage 兼容的 flat dict"""

import os
from pathlib import Path

import yaml

PLAN_FILENAME = "plan.yaml"


def load_plan(project_dir):
    """读取 plan.yaml，返回 (flat_dict, raw_dict)

    flat_dict 兼容旧 info.json 接口，stage 模块直接使用。
    """
    path = Path(project_dir) / PLAN_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"{PLAN_FILENAME} not found in {project_dir}")
    with open(path) as f:
        raw = yaml.safe_load(f)

    flat = _flatten(raw)
    return flat, raw


def _flatten(raw):
    p = raw.get("project", {})
    prm = raw.get("parameters", {})
    sc = raw.get("supercell", {})
    d = raw.get("defects", {})
    cpd = raw.get("cpd", {})
    crp = raw.get("crisp", {})

    flat = {
        "obj": p.get("obj", ""),
        "dopant_element": p.get("dopant_elements", []),
        "encut": prm.get("encut"),
        "hubbard_u": prm.get("hubbard_u", False),
        "pp": prm.get("pp", []),
        "functional": prm.get("functional", "pbesol"),
        "supercell": {
            "max_atoms": sc.get("max_atoms", 600),
            "min_atoms": sc.get("min_atoms", 200),
        },
        "interstitial": d.get("interstitials", False),
        "complex_defect": d.get("complex_n", 1),
        "max_distance": d.get("max_distance", 3.0),
        "min_distance": d.get("min_distance", 0.3),
        "gas_corrections": cpd.get("gas_corrections", {}),
        "cluster": crp.get("cluster"),
        "stages": raw.get("stages", {}),
        "_raw": raw,
    }
    return flat
