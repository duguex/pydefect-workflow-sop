"""Workflow state machine.

Replaces the legacy sentinel-file pattern (``mp_flag``, ``complex_flag``,
``remove [X] to reset``) with a structured, persistent record that enables
resumable pipelines.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


class StepStatus(enum.Enum):
    """Status of a single pipeline stage."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# ── Stage results ────────────────────────────────────────────────────────


@dataclass
class CpdResult:
    """Output of the CPD (chemical-potential diagram) stage."""

    unitcell_path: Path  # directory of the target compound structure
    chem_pot_path: Path  # target_vertices.yaml
    standard_energies_path: Path  # standard_energies.yaml


@dataclass
class UnitcellResult:
    """Output of the unitcell stage."""

    unitcell_yaml_path: Path  # unitcell.yaml
    band_path: Path
    dos_path: Path
    dielectric_path: Path


@dataclass
class DefectResult:
    """Output of the defect stage."""

    defect_energy_summary_path: Path  # defect_energy_summary.json
    calc_summary_path: Path  # calc_summary.json


# ── Pipeline state ───────────────────────────────────────────────────────


@dataclass
class PipelineState:
    """Complete serialisable state of a point-defect pipeline run.

    Persisted to ``{root}/.pipeline_state.json`` after each stage.
    """

    root: Path

    # Stage statuses
    cpd_status: StepStatus = StepStatus.PENDING
    unitcell_status: StepStatus = StepStatus.PENDING
    defect_status: StepStatus = StepStatus.PENDING

    # Stage results (populated after completion)
    cpd_result: Optional[CpdResult] = None
    unitcell_result: Optional[UnitcellResult] = None
    defect_result: Optional[DefectResult] = None

    # Active job tracking: work-dir -> task_name
    active_jobs: dict[str, str] = field(default_factory=dict)

    def is_terminal(self) -> bool:
        """Return True when all three stages are done."""
        return (
            self.cpd_status == StepStatus.DONE
            and self.unitcell_status == StepStatus.DONE
            and self.defect_status == StepStatus.DONE
        )


# ── Serialisation helpers ────────────────────────────────────────────────

_FILE_NAME = ".pipeline_state.json"


def _to_dict(state: PipelineState) -> dict:
    data = asdict(state)
    # Convert Path objects
    data["root"] = str(data["root"])
    for key in ("cpd_result", "unitcell_result", "defect_result"):
        if data[key] is not None:
            for k, v in data[key].items():
                if isinstance(v, Path):
                    data[key][k] = str(v)
    # Convert enums
    for key in ("cpd_status", "unitcell_status", "defect_status"):
        if key in data:
            data[key] = data[key].value
    return data


def _from_dict(data: dict) -> PipelineState:
    root = Path(data["root"])
    for key in ("cpd_status", "unitcell_status", "defect_status"):
        if key in data and isinstance(data[key], str):
            data[key] = StepStatus(data[key])

    cpd_result = None
    if data.get("cpd_result"):
        cr = data["cpd_result"]
        cpd_result = CpdResult(
            unitcell_path=Path(cr["unitcell_path"]),
            chem_pot_path=Path(cr["chem_pot_path"]),
            standard_energies_path=Path(cr["standard_energies_path"]),
        )

    unitcell_result = None
    if data.get("unitcell_result"):
        ur = data["unitcell_result"]
        unitcell_result = UnitcellResult(
            unitcell_yaml_path=Path(ur["unitcell_yaml_path"]),
            band_path=Path(ur["band_path"]),
            dos_path=Path(ur["dos_path"]),
            dielectric_path=Path(ur["dielectric_path"]),
        )

    defect_result = None
    if data.get("defect_result"):
        dr = data["defect_result"]
        defect_result = DefectResult(
            defect_energy_summary_path=Path(dr["defect_energy_summary_path"]),
            calc_summary_path=Path(dr["calc_summary_path"]),
        )

    return PipelineState(
        root=root,
        cpd_status=data.get("cpd_status", StepStatus.PENDING),
        unitcell_status=data.get("unitcell_status", StepStatus.PENDING),
        defect_status=data.get("defect_status", StepStatus.PENDING),
        cpd_result=cpd_result,
        unitcell_result=unitcell_result,
        defect_result=defect_result,
        active_jobs=data.get("active_jobs", {}),
    )


# ── StateStore ───────────────────────────────────────────────────────────


class StateStore:
    """Persistent store for ``PipelineState``.

    State is written to ``{root}/.pipeline_state.json`` after each stage
    completes, enabling resume from the last finished stage.
    """

    @staticmethod
    def state_path(root: Path) -> Path:
        return root / _FILE_NAME

    @staticmethod
    def load(root: Path) -> PipelineState:
        """Load state from ``.pipeline_state.json`` under *root*.

        Returns a fresh ``PipelineState`` if no persisted file exists.
        """
        path = StateStore.state_path(root)
        if not path.is_file():
            return PipelineState(root=root)
        with open(path) as f:
            data = json.load(f)
        return _from_dict(data)

    @staticmethod
    def save(state: PipelineState) -> None:
        """Persist *state* to ``.pipeline_state.json``."""
        path = StateStore.state_path(state.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(_to_dict(state), f, indent=2)
