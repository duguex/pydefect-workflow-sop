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
# Phases are ANALYSIS GATES, not submission gates (ADR 0026): submission is
# unconditional per cycle (any dir whose inputs are ready gets submitted);
# a phase says which downstream analysis is blocked and why.
#   RUNNING         — some calculation is not converged yet (submit active)
#   CPD_READY       — all cpd phases converged; chem-pot diagram not computed
#   ANALYZE_READY   — CPD done + all legs converged; defect analysis not run
#   COMPLETE        — defect analysis done
#   NO_TARGET       — no host phase (chemical-environment absent target)
RUNNING = "RUNNING"
CPD_READY = "CPD_READY"
ANALYZE_READY = "ANALYZE_READY"
COMPLETE = "COMPLETE"
NO_TARGET = "NO_TARGET"

# Legacy phase names (pre-ADR 0026) — kept for log/compat greps only.
STRUCTURE_OPT = "STRUCTURE_OPT"
COMPETING = "COMPETING"
CHEM_POT_DIAGRAM = "CHEM_POT_DIAGRAM"
UNITCELL_DEFECT = "UNITCELL_DEFECT"

_STATE_FILE = "state.json"  # legacy ADR 0001 marker; no longer read or written


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

        Disk-derived on every call (ADR 0011): persisted ``state.json``
        memory was removed — the orchestrator already inferred from disk
        everywhere, and the phase-gate audits (empty ``target_vertices``,
        missing ``standard_energies``) make the inference unambiguous.

        *js* is an optional :class:`~vasp_sop.core.job_store.JobStore`;
        when omitted a fresh store is opened for the inference (and closed
        again).  Pass one in to share a connection across many queries.
        """
        return self._infer_phase(js=js)

    def derive_phase(self, js: Any = None) -> str:
        """Fresh filesystem inference of the pipeline phase (ADR 0011)."""
        return self._infer_phase(js=js)

    # ── Private helpers ────────────────────────────────────────────────

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

        # ── Gate 1: every cpd phase converged (target + competitors) ────
        # RUNNING means "submit stays active"; the chem-pot diagram needs
        # all cpd energies, so anything unconverged blocks it.
        if not convergence_verdict(td).converged:
            return RUNNING
        for pd in sorted(cpd_root.iterdir()):
            if not pd.is_dir() or pd.name == "combos":
                continue
            if self._is_excluded_phase(pd):
                continue
            if not convergence_verdict(pd).converged:
                return RUNNING
        # A `.failed` marker blocks CPD computation: a failed latest
        # attempt cannot validate an older converged OUTCAR.  Once the
        # diagram exists the system does not regress past it (parity with
        # the pre-ADR 0026 persistence gate).
        if not target_vertices.is_file() and self.competing_blockers(_js):
            return RUNNING

        # ── Gate 2: CPD artifacts present (CPD_READY = diagram to run) ──
        if not target_vertices.is_file():
            return CPD_READY
        if target_vertices.stat().st_size == 0:
            logger.error(
                "%s: PHASE GATE FAILED — target_vertices.yaml exists but is "
                "empty (0 bytes). Re-run CPD post-processing.",
                self.name,
            )
            return CPD_READY
        for art in ("standard_energies.yaml", "composition_energies.yaml",
                    "chem_pot_diag.json"):
            if not (cpd_root / art).is_file():
                return CPD_READY

        # Chemical-environment scope (ADR 0005): no unit-cell/defect legs;
        # COMPLETE is reached when the CPD is done.
        if self.is_chemical_environment:
            return COMPLETE

        # ── Gate 3: every relaxation leg converged ──────────────────────
        # unitcell single-points + perfect + defect chain (ADR 0013
        # excludes anion-cation antisites from the blocking set).
        uc_root = self.uc_dir
        for task in ("band", "dos", "dielectric"):
            task_dir = uc_root / task
            if task_dir.is_dir() and not convergence_verdict(
                task_dir, task_type=task
            ).converged:
                return RUNNING
        df_root = self.defect_dir
        if not df_root.is_dir():
            return RUNNING
        from vasp_sop.defect import is_anion_cation_antisite

        for d in sorted(df_root.iterdir()):
            if not d.is_dir() or is_anion_cation_antisite(d.name):
                continue
            if not convergence_verdict(d).converged:
                return RUNNING

        # ── Gate 4: defect-analysis artifacts present ───────────────────
        # ANALYZE_READY = all legs done, analysis not run yet.
        if not (uc_root / "unitcell.yaml").is_file():
            return ANALYZE_READY
        for d in df_root.iterdir():
            if (
                not d.is_dir()
                or d.name == "perfect"
                or is_anion_cation_antisite(d.name)
            ):
                continue
            # Skip non-calculation subdirs (no VASP inputs / OUTCAR).
            if (
                not input_ready(d)
                and not (d / "OUTCAR").is_file()
                and not (d / "output" / "OUTCAR").is_file()
            ):
                continue
            for art in ("calc_results.json", "correction.json",
                        "defect_structure_info.json"):
                if not (d / art).is_file():
                    return ANALYZE_READY

        perfect = df_root / "perfect"
        if perfect.is_dir() and not (
            perfect / "perfect_band_edge_state.json"
        ).is_file():
            return ANALYZE_READY

        # Full analysis summary required — a partial one is not complete.
        if not (df_root / "defect_energy_summary.json").is_file():
            return ANALYZE_READY

        return COMPLETE

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
        from vasp_sop.defect.cpd import excluded_phases

        return excluded_phases(self.root)

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

    @staticmethod
    def _failed_newer_than_output(pd: Path) -> bool:
        """True when a ``.failed`` marker is newer than every VASP output.

        The latest attempt failed *after* the last converged output was
        written (e.g. a submit-stage crash), so the older output must not
        validate the dir. A marker older than the outputs is stale (a later
        run succeeded) and must not block — verdict-first.
        """
        marker = pd / ".failed"
        if not marker.is_file():
            return False
        mt = marker.stat().st_mtime
        for cand in ("OUTCAR", "vasprun.xml", "OSZICAR"):
            f = pd / cand
            if f.is_file() and f.stat().st_mtime > mt:
                return False
        return True

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
            verdict = convergence_verdict(pd)
            failed_newer = self._failed_newer_than_output(pd)
            if verdict.converged and not failed_newer:
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
            if current == "converged":
                logger.info(
                    "%s: stale JobStore 'converged' for cpd phase %s but disk "
                    "verdict=%s — resubmitting (ADR 0016 parity)",
                    self.name, pd.name, convergence_verdict(pd).reason,
                )
            result.append(pd)
        return result

    def competing_blockers(self, store: Any) -> list[Path]:
        """Lifecycle states that block entering CPD post-processing."""
        from vasp_sop.vasp.convergence import convergence_verdict
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
            failed_newer = self._failed_newer_than_output(pd)
            if convergence_verdict(pd).converged and not failed_newer:
                if (pd / "POSCAR").is_file() and not input_ready(pd):
                    # Converged-looking output with incomplete inputs: the
                    # output is stale from a different setup — must block.
                    blockers.append(pd)
                continue
            marker = crisp_terminal_status(pd)
            state = store.latest(str(pd.resolve()))
            if marker == "failed" or state in ("failed", "unconverged"):
                blockers.append(pd)
                continue
            if state == "converged" and (pd / "POSCAR").is_file():
                if not convergence_verdict(pd).converged:
                    blockers.append(pd)
                    continue
            if state not in ("converged", "submitted") and (pd / "POSCAR").is_file():
                if not input_ready(pd):
                    blockers.append(pd)
        return blockers

    def __repr__(self) -> str:  # pragma: no cover
        return f"System({self.name!r}, root={str(self.root)!r})"
