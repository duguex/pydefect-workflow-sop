"""JSON Schema for plan.yaml.

Schema is embedded as a module-level constant so it can be:
- imported by ``validate(plan)`` for full structural checks
- serialized with ``json.dumps(SCHEMA, indent=2)`` for documentation / IDE
  completion
- extended by callers (e.g. custom stages) without touching this file

The schema covers the full contract: required keys, type checks, and the
shape of nested structures (substitutionals, defects, cpd).
"""

import json
from typing import Any, Dict, List, Optional

import jsonschema
from jsonschema import Draft202012Validator


SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/duguex/pydefect-workflow-sop/plan.schema.json",
    "title": "pydefect-workflow plan.yaml",
    "description": (
        "Configuration file for the 7-stage point-defect VASP workflow. "
        "Stage modules read this via config._flatten(); the schema is the "
        "single source of truth for what fields are required and what types "
        "they must have."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": ["project", "parameters", "supercell", "defects", "stages"],
    "properties": {
        "project": {
            "type": "object",
            "additionalProperties": False,
            "required": ["obj"],
            "properties": {
                "obj": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Target chemical formula, e.g. 'SiC', 'SrTiO3'.",
                },
                "dopant_elements": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "uniqueItems": True,
                    "default": [],
                },
                "poscar_src": {
                    "type": "string",
                    "description": (
                        "POSCAR source. 'MP mp-XXX' for Materials Project; "
                        "'local: /path/to/POSCAR' for a local file. "
                        "Written by `pydefect-run plan`, may be empty until generated."
                    ),
                },
            },
        },
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["functional", "encut"],
            "properties": {
                "functional": {
                    "type": "string",
                    "enum": ["pbe", "pbesol", "lda", "scan", "pbe0", "hse"],
                    "default": "pbesol",
                },
                "encut": {
                    "oneOf": [
                        {"type": "null"},
                        {"type": "integer", "minimum": 100, "maximum": 2000},
                    ],
                    "description": "Plane-wave cutoff (eV). null = auto-detect.",
                },
                "hubbard_u": {"type": "boolean", "default": False},
                "pp": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit POTCAR variants, one per element.",
                },
            },
        },
        "supercell": {
            "type": "object",
            "additionalProperties": False,
            "required": ["max_atoms", "min_atoms"],
            "properties": {
                "max_atoms": {"type": "integer", "minimum": 1, "maximum": 10000},
                "min_atoms": {"type": "integer", "minimum": 1, "maximum": 10000},
            },
        },
        "defects": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "vacancies", "substitutionals", "interstitials",
                "iindex", "charges", "complex_n",
            ],
            "properties": {
                "vacancies": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "description": "Elements to form vacancies for (intrinsic).",
                },
                "substitutionals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["impurity", "site"],
                        "properties": {
                            "impurity": {"type": "string"},
                            "site": {"type": "string"},
                        },
                    },
                },
                "interstitials": {"type": "boolean", "default": False},
                "iindex": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0},
                    "default": [],
                    "description": (
                        "Indices into volumetric_data_local_extrema.json for "
                        "interstitial sites consumed by stage 3."
                    ),
                },
                "charges": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "default": [0],
                    "description": "Charge states for defect entries.",
                },
                "complex_n": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 1,
                    "description": (
                        "N_max for complex defects. 1 disables stage 7; >1 "
                        "enables ComplexDefectMaker N=2..N."
                    ),
                },
                "max_distance": {"type": "number", "minimum": 0.1, "default": 3.0},
                "min_distance": {"type": "number", "minimum": 0.1, "default": 0.3},
            },
        },
        "cpd": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "gas_corrections": {
                    "type": "object",
                    "additionalProperties": {"type": "number"},
                    "description": "Per-formula energy corrections for gases (eV).",
                },
            },
        },
        "crisp": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "cluster": {
                    "oneOf": [{"type": "string"}, {"type": "null"}],
                    "description": "SLURM cluster name; null = default.",
                },
            },
        },
        "stages": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "unitcell", "cpd", "defect_gen", "submit",
                "postproc", "doping", "complex",
            ],
            "properties": {
                "unitcell": {"type": "boolean"},
                "cpd": {"type": "boolean"},
                "defect_gen": {"type": "boolean"},
                "submit": {"type": "boolean"},
                "postproc": {"type": "boolean"},
                "doping": {"type": "boolean"},
                "complex": {"type": "boolean"},
            },
        },
    },
}


def validate(plan: Dict[str, Any]) -> List[str]:
    """Validate plan against the JSON Schema. Returns a list of human-readable errors.

    Empty list means valid. Errors include JSON paths to help users locate them.
    """
    validator = Draft202012Validator(SCHEMA)
    errors = []
    for err in sorted(validator.iter_errors(plan), key=lambda e: list(e.absolute_path)):
        path = "$" if not err.absolute_path else ".".join(str(p) for p in err.absolute_path)
        errors.append(f"{path}: {err.message}")
    return errors


def to_json(indent: int = 2) -> str:
    """Serialize SCHEMA to JSON for documentation / tooling."""
    return json.dumps(SCHEMA, indent=indent, sort_keys=False)
