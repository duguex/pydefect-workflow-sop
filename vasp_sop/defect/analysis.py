"""Defect post-processing — energy analysis, corrections, summaries.

Each pydefect step has a guard that skips it if its output already
exists, so re-running post-processing picks up where it left off.

Return value (issue #0007):
    ``"full"``    — every ionically converged eligible dir has correction +
                    final summary; zero unconverged OUTCAR dirs
    ``"partial"`` — some corrections / incomplete summary (not production-complete)
    ``"failed"``  — nothing usable (no ready dirs, zero corrections, etc.)

Publishable notes (#0010–#0013):
    ``pydefect_vasp cr`` requires ``vasprun.xml``. OUTCAR-only defects are
    tracked as ``missing_vasprun`` and cannot receive eFNV until vasprun is
    restored or the job is re-fetched.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

import yaml

from vasp_sop.core.config import PipelineConfig
from vasp_sop.defect import pydefect_adapter as _pdad

logger = logging.getLogger(__name__)

AnalyzeStatus = Literal["full", "partial", "failed"]

_SUMMARY = "defect_energy_summary.json"
_SUMMARY_PARTIAL = "defect_energy_summary.partial.json"
_STATUS_JSON = "analyze_status.json"


def _defect_dirs(defect_root: Path, *, include_defect_new: bool = False) -> list[Path]:
    from vasp_sop.defect import is_valid_defect_dir

    return sorted(
        d for d in defect_root.iterdir()
        if d.is_dir() and d.name != "perfect"
        and is_valid_defect_dir(d, include_defect_new=include_defect_new)
    )


def _has_outcar(d: Path) -> bool:
    return (d / "OUTCAR").is_file() or (d / "output" / "OUTCAR").is_file()


def _has_vasprun(d: Path) -> bool:
    return (d / "vasprun.xml").is_file() or (d / "output" / "vasprun.xml").is_file()


def _eligible_dirs(defect_dirs: list[Path]) -> list[Path]:
    """Dirs that look like real VASP calcs (have OUTCAR or calc_results)."""
    out: list[Path] = []
    for d in defect_dirs:
        if _has_outcar(d) or (d / "calc_results.json").is_file():
            out.append(d)
    return out


def _converged_dirs(dirs: list[Path]) -> list[Path]:
    """Subset of *dirs* whose convergence verdict passes."""
    from vasp_sop.vasp.convergence import convergence_verdict

    return [d for d in dirs if convergence_verdict(d).converged]


def _cr_ready_dirs(dirs: list[Path]) -> list[Path]:
    """Dirs that can be parsed by ``pydefect_vasp cr`` (need vasprun or existing cr)."""
    ready: list[Path] = []
    for d in dirs:
        if (d / "calc_results.json").is_file() or _has_vasprun(d):
            ready.append(d)
    return ready



def _inventory(defect_root: Path) -> dict[str, Any]:
    """Collect lists used by classify / status / analyze."""
    dirs = _defect_dirs(defect_root)
    eligible = _eligible_dirs(dirs)
    converged = _converged_dirs(eligible)
    unconverged = [d for d in eligible if d not in set(converged)]
    corrected = [d for d in converged if (d / "correction.json").is_file()]
    missing_vasprun = [
        d for d in converged
        if not _has_vasprun(d) and not (d / "calc_results.json").is_file()
    ]
    missing_calc_results = [
        d for d in converged if not (d / "calc_results.json").is_file()
    ]
    with_dei = [
        d for d in converged if (d / "defect_energy_info.json").is_file()
    ]
    missing_outcar = [d for d in dirs if not _has_outcar(d) and not (d / "calc_results.json").is_file()]
    return {
        "dirs": dirs,
        "eligible": eligible,
        "converged": converged,
        "unconverged": unconverged,
        "corrected": corrected,
        "missing_vasprun": missing_vasprun,
        "missing_calc_results": missing_calc_results,
        "with_dei": with_dei,
        "missing_outcar": missing_outcar,
    }


def classify_analyze_status(defect_root: Path) -> AnalyzeStatus:
    """Classify post-process completeness from on-disk artifacts.

    ``full`` requires every *ionically converged* eligible dir to have
    correction.json, a final summary, and **zero** unconverged OUTCAR dirs.
    Unconverged dirs never count as successful corrections.
    """
    if not defect_root.is_dir():
        return "failed"
    inv = _inventory(defect_root)
    eligible = inv["eligible"]
    converged = inv["converged"]
    unconverged = inv["unconverged"]
    corrected = inv["corrected"]
    summary = (defect_root / _SUMMARY).is_file()
    partial_summary = (defect_root / _SUMMARY_PARTIAL).is_file()

    if not eligible and not summary and not partial_summary:
        return "failed"
    if not corrected and not summary and not partial_summary:
        return "failed"
    if (
        summary
        and converged
        and len(corrected) == len(converged)
        and not unconverged
    ):
        return "full"
    if summary or corrected or partial_summary or unconverged:
        return "partial" if (corrected or summary or partial_summary) else "failed"
    return "failed"


def _write_status(
    defect_root: Path,
    status: AnalyzeStatus,
    inv: dict[str, Any] | None = None,
    **extra: Any,
) -> None:
    """Write actionable analyze_status.json (#0013)."""
    if inv is None:
        inv = _inventory(defect_root)
    eligible = inv["eligible"]
    converged = inv["converged"]
    unconverged = inv["unconverged"]
    corrected = inv["corrected"]
    payload: dict[str, Any] = {
        "status": status,
        "n_eligible": len(eligible),
        "n_converged": len(converged),
        "n_corrected": len(corrected),
        "n_dei": len(inv["with_dei"]),
        "n_unconverged": len(unconverged),
        "n_missing_vasprun": len(inv["missing_vasprun"]),
        "n_missing_calc_results": len(inv["missing_calc_results"]),
        "n_missing_outcar": len(inv["missing_outcar"]),
        "missing_correction": sorted(
            d.name for d in converged if d not in set(corrected)
        ),
        "missing_vasprun": sorted(d.name for d in inv["missing_vasprun"]),
        "missing_calc_results": sorted(d.name for d in inv["missing_calc_results"]),
        "missing_outcar": sorted(d.name for d in inv["missing_outcar"]),
        "unconverged": sorted(d.name for d in unconverged),
    }
    payload.update(extra)
    try:
        (defect_root / _STATUS_JSON).write_text(
            json.dumps(payload, indent=2) + "\n"
        )
    except OSError as exc:
        logger.warning("Could not write %s: %s", _STATUS_JSON, exc)


def _demote_incomplete_summary(defect_root: Path, status: AnalyzeStatus) -> None:
    """Keep final summary only for full; demote partial to .partial.json."""
    summary = defect_root / _SUMMARY
    if not summary.is_file():
        return
    if status == "full":
        partial = defect_root / _SUMMARY_PARTIAL
        if partial.is_file():
            try:
                partial.unlink()
            except OSError:
                pass
        return
    dest = defect_root / _SUMMARY_PARTIAL
    try:
        if dest.is_file():
            dest.unlink()
        summary.rename(dest)
        logger.warning(
            "Demoted incomplete %s → %s (status=%s)",
            _SUMMARY, _SUMMARY_PARTIAL, status,
        )
    except OSError as exc:
        logger.warning("Could not demote summary: %s", exc)


def reconcile_defect_summaries(project_or_defect_root: Path) -> dict[str, AnalyzeStatus]:
    """Demote incomplete final summaries and write analyze_status.json."""
    root = Path(project_or_defect_root)
    if (root / "defect").is_dir() and (root / "plan.yaml").is_file():
        defect_root = root / "defect"
        key = root.name
    elif root.name == "defect" or (root / _SUMMARY).is_file() or list(root.glob("Va_*")):
        defect_root = root
        key = root.parent.name if root.name == "defect" else root.name
    else:
        defect_root = root / "defect" if (root / "defect").is_dir() else root
        key = root.name

    if not defect_root.is_dir():
        return {key: "failed"}

    status = classify_analyze_status(defect_root)
    if status != "full":
        _demote_incomplete_summary(defect_root, status)
        status = classify_analyze_status(defect_root)
    inv = _inventory(defect_root)
    _write_status(defect_root, status, inv)
    return {key: status}


def reconcile_project_tree(project_root: Path) -> dict[str, AnalyzeStatus]:
    """Run :func:`reconcile_defect_summaries` on every system with plan.yaml."""
    root = Path(project_root)
    out: dict[str, AnalyzeStatus] = {}
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not (d / "plan.yaml").is_file():
            continue
        df = d / "defect"
        if not df.is_dir():
            continue
        if not any(
            (df / name).is_file()
            for name in (_SUMMARY, _SUMMARY_PARTIAL, _STATUS_JSON)
        ) and not any(
            (c / "correction.json").is_file()
            for c in df.iterdir() if c.is_dir()
        ):
            continue
        out.update(reconcile_defect_summaries(d))
    return out


def analyze(
    defect_root: Path,
    project_root: Path,
    config: PipelineConfig,
    unitcell_yaml: Path,
    standard_energies: Path,
    target_vertices: Path,
) -> AnalyzeStatus:
    """Run the defect energetics post-processing pipeline.

    Returns
    -------
    AnalyzeStatus
        ``full`` / ``partial`` / ``failed`` — see module docstring.
    """
    summary_json = defect_root / _SUMMARY
    if summary_json.is_file():
        status = classify_analyze_status(defect_root)
        if status != "full":
            _demote_incomplete_summary(defect_root, status)
            status = classify_analyze_status(defect_root)
        inv = _inventory(defect_root)
        _write_status(defect_root, status, inv)
        logger.info(
            "Defect energy summary already exists, skipping post-processing "
            "(status=%s, corrected=%d/%d).",
            status, len(inv["corrected"]), len(inv["eligible"]),
        )
        return status

    perfect_dir = defect_root / "perfect"

    # ── OUTCAR recovery (do not hard-fail whole system — #0011) ─────
    from vasp_sop.core.cache import restore_from_cache
    from vasp_sop.core.jobs import move_crisp_outputs
    from vasp_sop.defect import is_valid_defect_dir, iter_defect_dirs

    include_dn = getattr(config, "include_defect_new", False)
    valid_dirs = iter_defect_dirs(
        defect_root, include_perfect=True, include_defect_new=include_dn,
    )

    missing_outcars: list[str] = []
    for d in valid_dirs:
        if _has_outcar(d):
            continue
        move_crisp_outputs(d)
        if _has_outcar(d):
            continue
        if not (d / "POSCAR").is_file():
            logger.debug("Skipping cache restore for %s: POSCAR missing", d)
            if d.name != "perfect" and "_" in d.name:
                missing_outcars.append(d.name)
            continue
        try:
            restored = restore_from_cache(d)
        except Exception as exc:
            logger.debug("Cache restore skipped for %s: %s", d.name, exc)
            restored = False
        if restored:
            logger.info("Restored outputs for %s from cache", d.name)
        elif d.name != "perfect" and "_" in d.name:
            missing_outcars.append(d.name)
    if missing_outcars:
        logger.warning(
            "Skipping %d defect dir(s) missing OUTCAR (partial path): %s",
            len(missing_outcars),
            ", ".join(missing_outcars[:20]),
        )

    # perfect is required for efnv/pbes
    if not perfect_dir.is_dir() or (
        not _has_outcar(perfect_dir) and not (perfect_dir / "calc_results.json").is_file()
    ):
        logger.error("perfect/ missing or has no OUTCAR/calc_results; cannot analyze.")
        inv = _inventory(defect_root)
        _write_status(defect_root, "failed", inv, skip_reason="perfect_missing")
        return "failed"

    defect_dirs_all = _defect_dirs(defect_root, include_defect_new=include_dn)
    inv0 = _inventory(defect_root)
    converged_now = inv0["converged"]
    if not converged_now and not inv0["corrected"]:
        logger.error("No ionically converged defect dirs; cannot produce corrections.")
        _write_status(defect_root, "failed", inv0, skip_reason="no_converged_defects")
        return "failed"

    # ── cr: only dirs that still need calc_results and have vasprun (#0010/#0012)
    need_cr = [
        d for d in converged_now
        if not (d / "calc_results.json").is_file() and _has_vasprun(d)
    ]
    perfect_cr = perfect_dir / "calc_results.json"
    if not perfect_cr.is_file():
        if _has_vasprun(perfect_dir):
            try:
                _pdad.perfect_calc_results(defect_root)
            except Exception as exc:
                logger.warning("pydefect_vasp cr perfect failed: %s", exc)
        else:
            logger.warning(
                "perfect/ missing vasprun.xml — cr cannot run (#0010). "
                "Restore vasprun or re-fetch crisp outputs."
            )
    if need_cr:
        try:
            _pdad.calc_results(need_cr, defect_root)
        except Exception as exc:
            logger.warning("pydefect_vasp cr (subset) failed: %s", exc)
    else:
        missing_vr = inv0["missing_vasprun"]
        if missing_vr:
            logger.warning(
                "Skipping cr for %d converged dir(s) missing vasprun.xml (#0010): %s",
                len(missing_vr),
                ", ".join(d.name for d in missing_vr[:20]),
            )
        if all((d / "calc_results.json").is_file() for d in converged_now) and perfect_cr.is_file():
            logger.info("calc_results.json present for converged dirs + perfect; skip cr.")

    # refresh inventory after cr
    inv1 = _inventory(defect_root)
    converged_now = inv1["converged"]
    unconverged_now = inv1["unconverged"]
    cr_present = [d for d in converged_now if (d / "calc_results.json").is_file()]

    # ── normalize calc_results: override ionic_conv from OUTCAR evidence ──
    # Single reconciliation point — lives in the adapter.
    _cr_norm = _pdad.normalize_ionic_convergence(cr_present, defect_root)
    if _cr_norm:
        logger.info("Normalized ionic_conv for %d calc_results.json", _cr_norm)

    # ── efnv: converged + calc_results only
    efnv_targets = cr_present
    if unconverged_now:
        logger.warning(
            "Skipping efnv for %d unconverged dir(s): %s",
            len(unconverged_now),
            ", ".join(d.name for d in unconverged_now[:20]),
        )
    if inv1["missing_vasprun"]:
        logger.warning(
            "Skipping efnv for %d dir(s) without calc_results/vasprun: %s",
            len(inv1["missing_vasprun"]),
            ", ".join(d.name for d in inv1["missing_vasprun"][:20]),
        )
    if perfect_cr.is_file() and unitcell_yaml.is_file() and efnv_targets:
        try:
            _pdad.efnv(
                efnv_targets, defect_root,
                perfect_calc_results=perfect_cr,
                unitcell_yaml=unitcell_yaml,
                force=True,
            )
        except Exception as exc:
            logger.warning("pydefect efnv failed (partial corrections): %s", exc)
    elif not unitcell_yaml.is_file():
        logger.error("unitcell.yaml missing; skip efnv.")
    elif not efnv_targets:
        logger.warning("No efnv targets (need converged + calc_results).")

    # ── dsi / dvf on converged only (#0012, #0024)
    dsi_targets = [d for d in converged_now if not (d / "defect_structure_info.json").is_file()]
    if dsi_targets:
        try:
            _pdad.defect_structure_info(dsi_targets, defect_root)
        except Exception as exc:
            logger.warning("pydefect dsi failed: %s", exc)
    else:
        logger.info("defect_structure_info.json present for converged dirs; skip dsi.")

    dvf_targets = [
        d for d in converged_now if not (d / "defect_volume_fraction.json").is_file()
    ]
    if dvf_targets:
        try:
            _pdad.defect_volume_fraction(dvf_targets, defect_root)
        except Exception:
            logger.warning(
                "pydefect_util dvf failed (may be slow on NFS or missing inputs), "
                "skipping defect volume fraction."
            )

    # ── pbes
    pbes_json = perfect_dir / "perfect_band_edge_state.json"
    if pbes_json.is_file():
        logger.info("perfect_band_edge_state.json exists, skipping pydefect_vasp pbes.")
    else:
        try:
            _pdad.pbes(defect_root)
        except Exception as exc:
            logger.warning("pydefect_vasp pbes failed (vasprun?): %s", exc)

    # ── beoi + bes on converged only, batched (#0024)
    if pbes_json.is_file() and converged_now:
        try:
            _pdad.band_edge_occupation(converged_now, defect_root, pbes_json)
        except Exception as exc:
            logger.warning("pydefect_vasp beoi failed: %s", exc)
        try:
            _pdad.band_edge_states(converged_now, defect_root, pbes_json)
        except Exception as exc:
            logger.warning("pydefect bes failed: %s", exc)


    # ── dei: corrected dirs
    if perfect_cr.is_file() and unitcell_yaml.is_file() and standard_energies.is_file():
        corrected = [
            d for d in converged_now if (d / "correction.json").is_file()
        ]
        not_corrected = [
            d for d in converged_now if not (d / "correction.json").is_file()
        ]
        if not_corrected:
            logger.warning(
                "Skipping %d converged defect(s) missing correction.json: %s",
                len(not_corrected),
                ", ".join(d.name for d in not_corrected[:20]),
            )
        if corrected:
            _pdad.defect_energy_info(
                corrected, defect_root,
                perfect_calc_results=perfect_cr,
                unitcell_yaml=unitcell_yaml,
                standard_energies=standard_energies,
            )

    # ── des / cs
    inv2 = _inventory(defect_root)
    defect_dirs_all = inv2["dirs"]
    ready_for_des = [
        d for d in defect_dirs_all
        if (d / "defect_energy_info.json").is_file()
        or ((d / "correction.json").is_file() and (d / "calc_results.json").is_file())
    ]
    converged_final = inv2["converged"]
    unconverged_final = inv2["unconverged"]
    corrected_final = inv2["corrected"]
    allow_final_summary = (
        bool(converged_final)
        and len(corrected_final) == len(converged_final)
        and not unconverged_final
        and not inv2["missing_vasprun"]
        and pbes_json.is_file()
        and unitcell_yaml.is_file()
        and target_vertices.is_file()
        and bool(ready_for_des)
    )
    if unitcell_yaml.is_file() and pbes_json.is_file() and target_vertices.is_file():
        if not ready_for_des:
            logger.error(
                "No defect dirs ready for des (need defect_energy_info or "
                "correction+calc_results); skip summary."
            )
        elif allow_final_summary:
            # Real CLI failures abort the final-summary path (the adapter
            # re-raises with raise_on_error); a missing summary file is not
            # an error here — the closing classify/demote handles it.
            _pdad.defect_energy_summary(
                defect_root, ready_for_des,
                unitcell_yaml, pbes_json, target_vertices,
                raise_on_error=True,
            )
        else:
            summary = _pdad.defect_energy_summary(
                defect_root, ready_for_des,
                unitcell_yaml, pbes_json, target_vertices,
            )
            if summary is None:
                logger.warning("partial des failed: no summary produced")
            status_tmp = classify_analyze_status(defect_root)
            if status_tmp != "full":
                _demote_incomplete_summary(defect_root, "partial")

    if perfect_cr.is_file() and ready_for_des and allow_final_summary:
        _pdad.correction_summary(
            ready_for_des, defect_root, perfect_calc_results=perfect_cr,
        )

    # ── pe
    if target_vertices.is_file() and (defect_root / _SUMMARY).is_file():
        with open(target_vertices) as f:
            tv_data = yaml.safe_load(f) or {}
        vertices = [k for k in tv_data if k != "target"]
        if len(vertices) == 1:
            logger.info("Single-element system: skipping pydefect pe plot.")
        else:
            for vertex in vertices:
                try:
                    _pdad.plot_energy_vertex(defect_root, vertex)
                except Exception as exc:
                    logger.warning(
                        "pydefect pe failed for vertex %s (often empty "
                        "defect energies): %s",
                        vertex, exc,
                    )

    # ── interactive HTML report (best-effort) ──
    try:
        if (defect_root / _SUMMARY).is_file():
            cpd_json = project_root / "cpd" / "chem_pot_diag.json"
            tv_yaml = project_root / "cpd" / "target_vertices.yaml"
            if cpd_json.is_file() and tv_yaml.is_file():
                from vasp_sop.report.interactive import generate_interactive_html
                generate_interactive_html(project_root)
                logger.info("Interactive formation-energy report generated.")
    except Exception as exc:
        logger.warning("Interactive HTML generation failed: %s", exc)

    status = classify_analyze_status(defect_root)
    if status != "full":
        _demote_incomplete_summary(defect_root, status)
        status = classify_analyze_status(defect_root)
    inv_final = _inventory(defect_root)
    _write_status(defect_root, status, inv_final)
    logger.info(
        "analyze finished status=%s corrected=%d/%d unconverged=%d "
        "missing_vasprun=%d",
        status,
        len(inv_final["corrected"]),
        len(inv_final["converged"]),
        len(inv_final["unconverged"]),
        len(inv_final["missing_vasprun"]),
    )
    return status
