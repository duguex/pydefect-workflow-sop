"""System model — data holder + phase detection for a single material system.

Introduced in issue #103.  This is intentionally a thin data holder with
filesystem-based phase inference; it is **not** a god class.  The batch
orchestrator in ``cli/main.py`` still uses its own ``_phase()`` for now
(migration is tracked in issue #95).

State markers
-------------
``System.phase()`` checks ``{root}/state.json`` first and falls back to
filesystem inference when the file is absent or unreadable.
``System.save_phase(phase)`` writes the phase key into ``state.json``
without disturbing any other keys that may already be present.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Phase constants ────────────────────────────────────────────────────────
STRUCTURE_OPT = "STRUCTURE_OPT"
COMPETING = "COMPETING"
CHEM_POT_DIAGRAM = "CHEM_POT_DIAGRAM"
UNITCELL_DEFECT = "UNITCELL_DEFECT"
COMPLETE = "COMPLETE"
NO_TARGET = "NO_TARGET"

_STATE_FILE = "state.json"


class System:
    """A single material system in the batch pipeline.

    Parameters
    ----------
    root:
        Filesystem root of the system (the directory that contains
        ``plan.yaml``, ``cpd/``, ``unitcell/``, ``defect/``).
    config:
        A :class:`~vasp_sop.core.config.PipelineConfig` (or compatible
        object with at least ``poscar_src`` and ``formula`` attributes).
    """

    def __init__(self, root: Path, config: Any) -> None:
        self.root = Path(root)
        self.config = config
        self.name: str = self.root.name

    # ── Directory properties ───────────────────────────────────────────

    @property
    def cpd_dir(self) -> Path:
        """Competing-phase directory (``{root}/cpd``)."""
        return self.root / "cpd"

    @property
    def uc_dir(self) -> Path:
        """Unitcell directory (``{root}/unitcell``)."""
        return self.root / "unitcell"

    @property
    def defect_dir(self) -> Path:
        """Defect directory (``{root}/defect``)."""
        return self.root / "defect"

    @property
    def target_dir(self) -> Path | None:
        """Target phase directory inside ``cpd/``, or ``None``.

        Identified by matching the MP-ID suffix extracted from
        ``config.poscar_src`` (e.g. ``"MP mp-830"`` → ``mp-830``).
        """
        mpid = self._mpid
        if not mpid:
            return None
        cpd = self.cpd_dir
        if not cpd.is_dir():
            return None
        pattern = re.compile(re.escape(mpid) + r"\Z")
        for pd in sorted(cpd.iterdir()):
            if pd.is_dir() and pattern.search(pd.name):
                return pd
        return None

    @property
    def _mpid(self) -> str | None:
        src: str = getattr(self.config, "poscar_src", "") or ""
        if src.startswith("MP mp-"):
            return "mp-" + src.split("mp-", 1)[1]
        return None

    # ── Defect directories ─────────────────────────────────────────────

    def defect_dirs(self) -> list[Path]:
        """Return valid defect calculation directories.

        A valid defect directory:
        - is a direct child of ``{root}/defect/``
        - is not ``perfect``
        - is not ``defect_new``
        - contains ``"_"`` in its name (``Name_Charge`` convention)
        """
        df = self.defect_dir
        if not df.is_dir():
            return []
        return sorted(
            d
            for d in df.iterdir()
            if d.is_dir()
            and d.name != "perfect"
            and d.name != "defect_new"
            and "_" in d.name
        )

    # ── Phase detection ────────────────────────────────────────────────

    def phase(self) -> str:
        """Return the current pipeline phase for this system.

        Resolution order:
        1. ``{root}/state.json`` ``"phase"`` key (explicit state marker).
        2. Filesystem inference (mirrors ``_phase()`` in ``cli/main.py``).
        """
        state = self._read_state()
        if state is not None:
            phase_val = state.get("phase")
            if phase_val:
                return str(phase_val)
        return self._infer_phase()

    def save_phase(self, phase: str) -> None:
        """Persist *phase* into ``{root}/state.json``.

        Existing keys in ``state.json`` are preserved.  Write failures
        are logged but do not raise.
        """
        state_path = self.root / _STATE_FILE
        existing: dict[str, Any] = {}
        if state_path.is_file():
            try:
                existing = json.loads(state_path.read_text())
                if not isinstance(existing, dict):
                    existing = {}
            except (OSError, json.JSONDecodeError):
                existing = {}
        existing["phase"] = phase
        try:
            state_path.write_text(json.dumps(existing, indent=2) + "\n")
        except OSError as exc:
            logger.warning("Could not write %s: %s", _STATE_FILE, exc)

    # ── Private helpers ────────────────────────────────────────────────

    def _read_state(self) -> dict[str, Any] | None:
        state_path = self.root / _STATE_FILE
        if not state_path.is_file():
            return None
        try:
            data = json.loads(state_path.read_text())
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _infer_phase(self) -> str:
        """Filesystem-based phase inference.

        Mirrors ``_phase()`` in ``cli/main.py`` (issue #103 — the
        canonical implementation will move here; ``cli/main.py`` migration
        is tracked in issue #95).
        """
        from vasp_sop.vasp.io import input_ready
        from vasp_sop.core.job_store import JobStore

        _js = JobStore()
        td = self.target_dir
        if td is None:
            return NO_TARGET

        cpd_root = self.cpd_dir
        target_vertices = cpd_root / "target_vertices.yaml"

        # ── Phase-persistence gate ─────────────────────────────────────
        # Once target_vertices.yaml exists the system is irrevocably past
        # COMPETING.  Downstream UC/DF can still cycle but we never return
        # COMPETING again for this system.
        if target_vertices.is_file():
            uc_root = self.uc_dir
            uc_tasks = ("band", "dos", "dielectric")
            uc_has_inputs = any((uc_root / t / "INCAR").is_file() for t in uc_tasks)
            if not uc_has_inputs:
                return UNITCELL_DEFECT

            if not (uc_root / "unitcell.yaml").is_file():
                return UNITCELL_DEFECT
            if not (cpd_root / "composition_energies.yaml").is_file():
                return UNITCELL_DEFECT
            if not (cpd_root / "standard_energies.yaml").is_file():
                return UNITCELL_DEFECT
            if not (cpd_root / "chem_pot_diag.json").is_file():
                return UNITCELL_DEFECT

            df_root = self.defect_dir
            if not df_root.is_dir():
                return UNITCELL_DEFECT

            for d in df_root.iterdir():
                if not d.is_dir() or d.name == "perfect":
                    continue
                # Skip non-calculation subdirs (no VASP inputs / OUTCAR).
                if (
                    not input_ready(d)
                    and not (d / "OUTCAR").is_file()
                    and not (d / "output" / "OUTCAR").is_file()
                ):
                    continue
                # Failed / unconverged defects do not block COMPLETE.
                latest_st = _js.latest(str(d.resolve()))
                if latest_st in ("failed", "unconverged"):
                    continue
                if not (d / "calc_results.json").is_file():
                    return UNITCELL_DEFECT
                if not (d / "correction.json").is_file():
                    return UNITCELL_DEFECT
                if not (d / "defect_structure_info.json").is_file():
                    return UNITCELL_DEFECT

            perfect = df_root / "perfect"
            if perfect.is_dir() and not (perfect / "perfect_band_edge_state.json").is_file():
                return UNITCELL_DEFECT

            return COMPLETE

        # ── Normal upstream progression (CPD not yet complete) ──────────
        if _js.latest(str(td.resolve())) != "converged":
            return STRUCTURE_OPT
        if self._competing_dirs(_js) or self._competing_blockers(_js):
            return COMPETING
        return CHEM_POT_DIAGRAM

    def _competing_dirs(self, store: Any) -> list[Path]:
        """Competing phases that still need VASP submission or retry."""
        from vasp_sop.vasp.io import check_converged, input_ready
        from vasp_sop.core.jobs import crisp_terminal_status

        td = self.target_dir
        cpd_dir = self.cpd_dir
        if not cpd_dir.is_dir():
            return []
        target_name = td.name if td else ""
        result: list[Path] = []
        for pd in sorted(cpd_dir.iterdir()):
            if not pd.is_dir() or pd.name in (target_name, "combos"):
                continue
            current = store.latest(str(pd.resolve()))
            if current == "submitted":
                continue
            marker = crisp_terminal_status(pd)
            if marker == "failed":
                if input_ready(pd) and current != "submitted":
                    result.append(pd)
                continue
            if marker == "completed":
                continue
            if not input_ready(pd):
                continue
            if check_converged(pd):
                continue
            if current not in ("converged", "submitted"):
                result.append(pd)
        return result

    def _competing_blockers(self, store: Any) -> list[Path]:
        """Lifecycle states that block entering CPD post-processing."""
        from vasp_sop.vasp.io import input_ready
        from vasp_sop.core.jobs import crisp_terminal_status

        td = self.target_dir
        cpd_dir = self.cpd_dir
        if not cpd_dir.is_dir():
            return []
        target_name = td.name if td else ""
        blockers: list[Path] = []
        for pd in sorted(cpd_dir.iterdir()):
            if not pd.is_dir() or pd.name in (target_name, "combos"):
                continue
            marker = crisp_terminal_status(pd)
            state = store.latest(str(pd.resolve()))
            if marker == "failed" or state in ("failed", "unconverged"):
                blockers.append(pd)
                continue
            if state not in ("converged", "submitted") and (pd / "POSCAR").is_file():
                if not input_ready(pd):
                    blockers.append(pd)
        return blockers

    def __repr__(self) -> str:  # pragma: no cover
        return f"System({self.name!r}, root={str(self.root)!r})"
