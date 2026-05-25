import json
import os

DEFAULT_INFO = {
    "obj": "",
    "dopant_element": [],
    "interstitial": False,
    "iindex": [],
    "complex_defect": 1,
    "remote": 5,
    "pp": [],
    "encut": None,
    "supercell": {"max_atoms": 600, "min_atoms": 200},
    "hubbard_u": False,
    "gas_corrections": {"O2": 1.374, "Cl2": 1.228, "F2": 0.924},
}

SCHEMA = {
    "obj": {"type": str, "required": True, "desc": "Target chemical formula, e.g. GaN"},
    "dopant_element": {"type": list, "required": False, "desc": "Dopant elements for defect_set"},
    "interstitial": {"type": bool, "required": False, "desc": "Enable interstitial analysis"},
    "iindex": {"type": list, "required": False, "desc": "Interstitial site indices"},
    "complex_defect": {"type": int, "required": False, "desc": "Max N for complex defects (1=single only)"},
    "remote": {"type": (int, float), "required": False, "desc": "Remote cutoff distance (Å)"},
    "pp": {"type": list, "required": False, "desc": "Extra POTCAR options, e.g. ['Cr_sv_GW']"},
    "encut": {"type": (int, float), "required": False, "desc": "ENCUT override (auto if None)"},
    "supercell": {"type": dict, "required": False, "desc": "Supercell constraints"},
    "hubbard_u": {"type": bool, "required": False, "desc": "Enable DFT+U via --options set_hubbard_u True"},
    "gas_corrections": {"type": dict, "required": False, "desc": "Gas molecule energy corrections (eV/molecule)"},
}


def validate(info):
    for key, spec in SCHEMA.items():
        if spec.get("required") and key not in info:
            raise ValueError(f"Missing required field: {key} ({spec['desc']})")
        val = info.get(key, spec.get("default"))
        if val is not None and not isinstance(val, spec["type"]):
            raise TypeError(f"Field '{key}' expects {spec['type']}, got {type(val)}")


def load_info(path):
    with open(path) as f:
        info = json.load(f)
    validate(info)
    for k, v in DEFAULT_INFO.items():
        info.setdefault(k, v)
    return info


def init_info(path, obj, **overrides):
    info = dict(DEFAULT_INFO)
    info["obj"] = obj
    info.update(overrides)
    validate(info)
    with open(path, "w") as f:
        json.dump(info, f, indent=2)
    return info
