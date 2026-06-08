"""Shared test fixtures.

Path setup is done in pyproject.toml / pytest.ini (rootdir = repo root,
sys.path includes ``scripts``). Fixtures here provide reusable:
- a small primitive POSCAR (Si) on disk for any test that needs one
- a minimal valid plan.yaml as a Python dict
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

# Ensure scripts/ is importable even when pytest is invoked from repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from pymatgen.core import Lattice, Structure  # noqa: E402


# ---------- POSCAR fixtures ----------

@pytest.fixture
def tmp_project(tmp_path):
    """An empty project directory."""
    (tmp_path / "unitcell" / "structure_opt").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def si_poscar(tmp_project) -> Path:
    """A real primitive Si POSCAR (2 atoms, diamond)."""
    s = Structure(Lattice.cubic(5.43), ["Si", "Si"],
                  [[0.0, 0.0, 0.0], [0.25, 0.25, 0.25]])
    p = tmp_project / "unitcell" / "structure_opt" / "POSCAR"
    s.to_file(str(p))
    return p


@pytest.fixture
def fe_o_poscar(tmp_project) -> Path:
    """FeO POSCAR (2 atoms, cubic) — should trigger DFT+U fallback detection."""
    s = Structure(Lattice.cubic(3.0), ["Fe", "O"],
                  [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]])
    p = tmp_project / "POSCAR_feo"
    s.to_file(str(p))
    return p


@pytest.fixture
def sic_poscar(tmp_project) -> Path:
    """SiC primitive POSCAR (2 atoms, zincblende)."""
    s = Structure(Lattice.cubic(4.36), ["Si", "C"],
                  [[0.0, 0.0, 0.0], [0.25, 0.25, 0.25]])
    p = tmp_project / "unitcell" / "structure_opt" / "POSCAR"
    s.to_file(str(p))
    return p


# ---------- plan.yaml fixtures ----------

@pytest.fixture
def valid_plan_dict() -> dict:
    """A fully-populated, schema-valid plan dict (post-generate state)."""
    return {
        "project": {
            "obj": "SiC",
            "dopant_elements": ["O"],
            "poscar_src": "MP mp-1234",
        },
        "parameters": {
            "functional": "pbesol",
            "encut": 520,
            "hubbard_u": False,
            "pp": ["Si", "C"],
        },
        "supercell": {"max_atoms": 600, "min_atoms": 200},
        "defects": {
            "vacancies": ["Si", "C"],
            "substitutionals": [{"impurity": "O", "site": "Si"}],
            "interstitials": False,
            "iindex": [],
            "charges": [0],
            "complex_n": 1,
            "max_distance": 3.0,
            "min_distance": 0.3,
        },
        "cpd": {"gas_corrections": {"O2": 1.374}},
        "crisp": {"cluster": None},
        "stages": {
            "unitcell": True, "cpd": True, "defect_gen": True,
            "submit": True, "postproc": True, "doping": False, "complex": False,
        },
    }


@pytest.fixture
def plan_yaml_path(tmp_project, valid_plan_dict) -> Path:
    """A plan.yaml file written to disk, valid against the JSON schema."""
    import yaml
    p = tmp_project / "plan.yaml"
    with open(p, "w") as f:
        yaml.safe_dump(valid_plan_dict, f, sort_keys=False)
    return p
