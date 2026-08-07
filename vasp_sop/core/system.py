"""System model — data holder + phase detection for a single material system.

Introduced in issue #103.  This is intentionally a thin data holder with
filesystem-based phase inference; it is **not** a god class.  It is the
canonical phase machine: the former ``cli/main.py::_phase`` clone is
deleted, and every phase decision (status, advance, batch loop) reads
``System.phase()``.

State markers
-------------
``System.phase()`` checks ``{root}/state.json`` first and falls back to
filesystem inference when the file is absent or unreadable.
``System.save_phase(phase)`` writes the phase key into ``state.json``
without disturbing any other keys that may already be present. Per
``docs/adr/0001``, the persisted phase is authoritative: the advance path
persists every post-cycle phase, so polls resume from memory.
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
    def is_chemical_environment(self) -> bool:
        """True when the system's scope excludes unit-cell and defect work.

        A chemical-environment system runs competing phases and the
        chemical-potential diagram only; COMPLETE is reached when the CPD
        is done (ADR 0005).
        """
        return getattr(self.config, "scope", "defects") == "chemical-environment"

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
        mpid = self.mpid
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
    def mpid(self) -> str | None:
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

    def phase(self, js: Any = None) -> str:
        """Return the current pipeline phase for this system.

        Resolution order:
        1. ``{root}/state.json`` ``"phase"`` key (explicit state marker).
        2. Filesystem inference.

        *js* is an optional :class:`~vasp_sop.core.job_store.JobStore`;
        when omitted a fresh store is opened for the inference (and closed
        again).  Pass one in to share a connection across many queries.
        """
        state = self._read_state()
        if state is not None:
            phase_val = state.get("phase")
            if phase_val:
                return str(phase_val)
        return self._infer_phase(js=js)

    def derive_phase(self, js: Any = None) -> str:
        """Fresh filesystem inference, ignoring persisted memory.

        Used after a phase's work completes to compute the *next* phase (the
        entry query uses :meth:`phase`, which honours persisted memory).  The
        caller persists the result via :meth:`save_phase` (ADR 0001).
        """
        return self._infer_phase(js=js)

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

    def _infer_phase(self, js: Any = None) -> str:
        """Filesystem-based phase inference.

        The canonical phase machine (the former ``cli/main.py::_phase``
        clone is deleted; see ``docs/adr/0001`` for the persistence rule).
        *js* is an optional shared JobStore connection.
        """
        from vasp_sop.vasp.io import input_ready
        from vasp_sop.core.job_store import JobStore

        own_store = js is None
        _js = js if not own_store else JobStore()
        try:
            return self._infer_phase_locked(_js, input_ready=input_ready)
        finally:
            if own_store:
                close = getattr(_js, "close", None)
                if close is not None:
                    close()

    def _infer_phase_locked(self, _js: Any, *, input_ready) -> str:
        from vasp_sop.vasp.convergence import convergence_verdict

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
            # ── Phase gate audit (issue #93) ────────────────────────────
            # Verify CPD artifacts are valid before allowing UNITCELL_DEFECT.
            tv_size = target_vertices.stat().st_size
            if tv_size == 0:
                logger.error(
                    "%s: PHASE GATE FAILED — target_vertices.yaml exists but is "
                    "empty (0 bytes). Cannot advance to UNITCELL_DEFECT.",
                    self.name,
                )
                return CHEM_POT_DIAGRAM
            se_path = cpd_root / "standard_energies.yaml"
            if not se_path.is_file():
                logger.error(
                    "%s: PHASE GATE FAILED — standard_energies.yaml missing. "
                    "Cannot advance to UNITCELL_DEFECT.",
                    self.name,
                )
                return CHEM_POT_DIAGRAM

            # Chemical-environment scope (ADR 0005): COMPLETE is reached
            # when the CPD is done — target_vertices + standard_energies
            # (checked above) plus composition_energies, chem_pot_diag and
            # every competing phase converged. No unit-cell/defect legs.
            if self.is_chemical_environment:
                if not (cpd_root / "composition_energies.yaml").is_file():
                    return CHEM_POT_DIAGRAM
                if not (cpd_root / "chem_pot_diag.json").is_file():
                    return CHEM_POT_DIAGRAM
                for pd in sorted(cpd_root.iterdir()):
                    if not pd.is_dir() or pd.name == "combos":
                        continue
                    if self._is_excluded_phase(pd):
                        continue
                    if not convergence_verdict(pd).converged:
                        return CHEM_POT_DIAGRAM
                return COMPLETE

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

            # COMPLETE means every calculation on disk has actually
            # converged (ADR 0004): a dir that ran and failed, or was
            # never prepared, keeps the system in UNITCELL_DEFECT.
            # ``structure_opt`` is a staging copy, not a calculation;
            # ``combos`` is the MP combo cache, not a phase.
            for pd in sorted(cpd_root.iterdir()):
                if not pd.is_dir() or pd.name == "combos":
                    continue
                if self._is_excluded_phase(pd):
                    continue
                if not convergence_verdict(pd).converged:
                    return UNITCELL_DEFECT

            uc_root = self.uc_dir
            uc_tasks = ("band", "dos", "dielectric")
            for task in uc_tasks:
                task_dir = uc_root / task
                if task_dir.is_dir() and not convergence_verdict(
                    task_dir, task_type=task
                ).converged:
                    return UNITCELL_DEFECT

            df_root = self.defect_dir
            if not df_root.is_dir():
                return UNITCELL_DEFECT

            for d in sorted(df_root.iterdir()):
                if not d.is_dir():
                    continue
                if not convergence_verdict(d).converged:
                    return UNITCELL_DEFECT

            # Post-processing artifacts per defect dir (all converged dirs
            # must carry them).
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
                if not (d / "calc_results.json").is_file():
                    return UNITCELL_DEFECT
                if not (d / "correction.json").is_file():
                    return UNITCELL_DEFECT
                if not (d / "defect_structure_info.json").is_file():
                    return UNITCELL_DEFECT

            perfect = df_root / "perfect"
            if perfect.is_dir() and not (perfect / "perfect_band_edge_state.json").is_file():
                return UNITCELL_DEFECT

            # Full analysis summary required — a partial one is not complete.
            if not (df_root / "defect_energy_summary.json").is_file():
                return UNITCELL_DEFECT

            return COMPLETE

        # ── Normal upstream progression (CPD not yet complete) ──────────
        if _js.latest(str(td.resolve())) != "converged":
            return STRUCTURE_OPT
        if self.competing_dirs(_js) or self.competing_blockers(_js):
            return COMPETING
        return CHEM_POT_DIAGRAM

    def _excluded_phases(self) -> set[str]:
        """Read ``cpd_excluded_phases.yaml`` from the system root (issue #93).

        Returns a set of phase directory names to skip during competing
        phase submission.  The file format is a YAML list of directory
        names (or substrings to match against directory names).

        Exclusions are a project-scope decision — phases that are not
        worth computing at all.  Never use this to hide a phase whose
        calculation ran and failed to converge: COMPLETE (ADR 0004)
        requires every engaged calculation to have converged, and an
        exclusion is not a failure bucket.
        """
        import yaml as _yaml

        excl_path = self.root / "cpd_excluded_phases.yaml"
        if not excl_path.is_file():
            return set()
        try:
            data = _yaml.safe_load(excl_path.read_text())
        except (OSError, _yaml.YAMLError):
            return set()
        if not isinstance(data, list):
            return set()
        return {str(entry) for entry in data}

    def _is_excluded_phase(self, phase_dir: Path) -> bool:
        """Check if *phase_dir* should be skipped per cpd_excluded_phases.yaml."""
        excluded = self._excluded_phases()
        if not excluded:
            return False
        name = phase_dir.name
        for pattern in excluded:
            if pattern == name or pattern in name:
                return True
        return False

    def competing_dirs(self, store: Any) -> list[Path]:
        """Competing phases that still need VASP submission or retry."""
        from vasp_sop.vasp.convergence import convergence_verdict
        from vasp_sop.vasp.io import input_ready
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
            if self._is_excluded_phase(pd):
                logger.info("%s: skipping excluded phase %s", self.name, pd.name)
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
            if convergence_verdict(pd).converged:
                continue
            if current not in ("converged", "submitted"):
                result.append(pd)
        return result

    def competing_blockers(self, store: Any) -> list[Path]:
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
            if self._is_excluded_phase(pd):
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
