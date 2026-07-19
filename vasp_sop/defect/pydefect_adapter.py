"""Pydefect CLI adapter — centralized interface for pydefect subprocess calls.

Introduced in issue #103.  These functions wrap the pydefect / pydefect_vasp
CLI invocations used during defect post-processing.  The current
implementation still shells out via :func:`~vasp_sop.core.jobs.run_local`;
a future ``libs/`` integration may replace the subprocess calls with direct
library calls without changing the public interface here.

Public API
----------
``calc_results(dirs, cwd)``
    Run ``pydefect_vasp cr`` and return per-directory result dicts.

``efnv(dirs, cwd, perfect_calc_results, unitcell_yaml)``
    Run ``pydefect efnv`` and return per-directory correction dicts.

``defect_energy_summary(cwd, dirs, unitcell_yaml, perfect_band_edge_state, target_vertices)``
    Run ``pydefect des`` and return the summary dict (or ``None``).
"""

from __future__ import annotations

import json
import logging
import shlex
from pathlib import Path
from typing import Any

from vasp_sop.core.jobs import run_local

logger = logging.getLogger(__name__)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def calc_results(dirs: list[Path], cwd: Path) -> list[dict[str, Any]]:
    """Run ``pydefect_vasp cr`` for each directory and collect result dicts.

    Directories that already contain ``calc_results.json`` are read directly
    without re-running the command.  Returns one dict per successfully
    processed directory (order matches *dirs*; failures are skipped with a
    warning).

    Parameters
    ----------
    dirs:
        Defect calculation directories (absolute or relative to *cwd*).
    cwd:
        Working directory for the subprocess (typically the ``defect/`` root).
    """
    results: list[dict[str, Any]] = []
    for d in dirs:
        cr_file = d / "calc_results.json"
        if cr_file.is_file():
            data = _read_json(cr_file)
            if data is not None:
                results.append(data)
                continue
            logger.warning("calc_results: unreadable %s, re-running cr", cr_file)
        try:
            run_local(
                f"pydefect_vasp cr -d {shlex.quote(d.name)}",
                cwd=cwd,
            )
        except Exception as exc:
            logger.warning("pydefect_vasp cr failed for %s: %s", d.name, exc)
            continue
        data = _read_json(cr_file)
        if data is not None:
            results.append(data)
        else:
            logger.warning("calc_results: no output for %s after cr", d.name)
    return results


def efnv(
    dirs: list[Path],
    cwd: Path,
    perfect_calc_results: Path,
    unitcell_yaml: Path,
) -> list[dict[str, Any]]:
    """Run ``pydefect efnv`` for each directory and collect correction dicts.

    Directories that already contain ``correction.json`` are read directly.
    Returns one dict per successfully corrected directory.

    Parameters
    ----------
    dirs:
        Defect calculation directories that have ``calc_results.json``.
    cwd:
        Working directory for the subprocess (typically the ``defect/`` root).
    perfect_calc_results:
        Path to the perfect-cell ``calc_results.json``.
    unitcell_yaml:
        Path to ``unitcell.yaml``.
    """
    pcr_q = shlex.quote(str(perfect_calc_results))
    u_q = shlex.quote(str(unitcell_yaml))
    results: list[dict[str, Any]] = []
    for d in dirs:
        corr_file = d / "correction.json"
        if corr_file.is_file():
            data = _read_json(corr_file)
            if data is not None:
                results.append(data)
                continue
            logger.warning("efnv: unreadable %s, re-running", corr_file)
        try:
            run_local(
                f"pydefect efnv -d {shlex.quote(d.name)} -pcr {pcr_q} -u {u_q}",
                cwd=cwd,
            )
        except Exception as exc:
            logger.warning("pydefect efnv failed for %s: %s", d.name, exc)
            continue
        data = _read_json(corr_file)
        if data is not None:
            results.append(data)
        else:
            logger.warning("efnv: no correction.json for %s after run", d.name)
    return results


def defect_energy_summary(
    cwd: Path,
    dirs: list[Path],
    unitcell_yaml: Path,
    perfect_band_edge_state: Path,
    target_vertices: Path,
) -> dict[str, Any] | None:
    """Run ``pydefect des`` and return the defect energy summary dict.

    If ``defect_energy_summary.json`` already exists in *cwd* it is read
    and returned without re-running.  Returns ``None`` when the summary
    file is absent after the command (e.g. the run failed).

    Parameters
    ----------
    cwd:
        Working directory (typically the ``defect/`` root).
    dirs:
        Defect directories to include in the summary.
    unitcell_yaml:
        Path to ``unitcell.yaml``.
    perfect_band_edge_state:
        Path to ``perfect_band_edge_state.json``.
    target_vertices:
        Path to ``target_vertices.yaml``.
    """
    summary_file = cwd / "defect_energy_summary.json"
    if summary_file.is_file():
        data = _read_json(summary_file)
        if data is not None:
            return data
        logger.warning("defect_energy_summary: unreadable existing summary, re-running des")

    if not dirs:
        logger.warning("defect_energy_summary: no dirs provided, skipping des")
        return None

    dir_names = " ".join(shlex.quote(d.name) for d in dirs)
    cmd = (
        f"pydefect des -d {dir_names}"
        f" -u {shlex.quote(str(unitcell_yaml))}"
        f" -pbes {shlex.quote(str(perfect_band_edge_state))}"
        f" -t {shlex.quote(str(target_vertices))}"
    )
    try:
        run_local(cmd, cwd=cwd)
    except Exception as exc:
        logger.warning("pydefect des failed: %s", exc)
        return None

    data = _read_json(summary_file)
    if data is None:
        logger.warning("defect_energy_summary: no summary file after des")
    return data
