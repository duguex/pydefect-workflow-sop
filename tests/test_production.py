"""Production data integrity checks for vasp-sop pipelines.

This test scans a real production directory (e.g. 2025_undergo_spin_defect)
and verifies that each system's on-disk state is self-consistent:
  - Phase-level artifact inventory (expected files present/absent)
  - defect_energy_summary.json structure + correction coverage
  - Cross-file invariants (e.g. target_vertices → unitcell.yaml chain)
  - Anomaly detection (artifacts out of phase, stale lock files)

Run with:
    VASP_SOP_PROD_DIR=/path/to/project python3 -m pytest tests/test_production.py -v

Skips automatically if VASP_SOP_PROD_DIR is not set (safe for CI).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml


# ── Helpers ──────────────────────────────────────────────────────────────────

def _systems(root: Path) -> list[Path]:
    """Return all system directories under *root* that have plan.yaml."""
    result: list[Path] = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and (d / "plan.yaml").is_file():
            result.append(d)
    return result


def _plan(root: Path) -> dict:
    return yaml.safe_load((root / "plan.yaml").read_text())


def _phase_of(root: Path) -> str:
    """Filesystem-only phase estimate aligned with current phase names.

    Prefer the real ``_phase()`` when JobStore is available; fall back to
    marker files so CI without a job DB still gets a coarse signal.
    """
    try:
        from vasp_sop.core.config import PipelineConfig
        from vasp_sop.cli.main import _phase

        cfg = PipelineConfig.from_yaml(root / "plan.yaml", root=root)
        src = cfg.poscar_src
        mpid = src.split("mp-", 1)[1] if src.startswith("MP mp-") else None
        return _phase({
            "name": root.name,
            "root": root,
            "config": cfg,
            "formula": cfg.formula,
            "mpid": mpid,
        })
    except Exception:
        pass

    cpd = root / "cpd"
    tv = cpd / "target_vertices.yaml"
    ce = cpd / "composition_energies.yaml"
    uc = root / "unitcell"
    df = root / "defect"
    es = df / "defect_energy_summary.json" if df.is_dir() else None

    if tv.is_file():
        has_uc_inputs = any(
            (uc / t / "INCAR").is_file()
            for t in ("band", "dos", "dielectric")
        )
        if not has_uc_inputs:
            return "UNITCELL_DEFECT"
        if es and es.is_file() and (uc / "unitcell.yaml").is_file():
            return "COMPLETE"
        return "UNITCELL_DEFECT"

    if not cpd.is_dir():
        return "STRUCTURE_OPT"

    for sub in cpd.iterdir():
        if sub.is_dir():
            try:
                if any(f.startswith("OUTCAR") for f in os.listdir(str(sub))):
                    return "COMPETING"
            except (PermissionError, FileNotFoundError):
                continue

    if ce.is_file():
        return "CHEM_POT_DIAGRAM"
    return "COMPETING"


def _check_summary_invariants(path: Path) -> list[str]:
    """Return list of violation descriptions, empty if valid."""
    errors: list[str] = []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        errors.append(f"not valid JSON: {exc}")
        return errors

    for key in ("@module", "@class", "title", "defect_energies"):
        if key not in data:
            errors.append(f"missing key {key!r}")

    if "defect_energies" in data and not isinstance(data["defect_energies"], dict):
        errors.append("defect_energies is not a dict")

    return errors


def _eligible_defect_dirs(df: Path) -> list[Path]:
    out: list[Path] = []
    if not df.is_dir():
        return out
    for child in df.iterdir():
        if not child.is_dir() or child.name == "perfect":
            continue
        if "_" not in child.name:
            continue
        if (child / "OUTCAR").is_file() or (child / "calc_results.json").is_file():
            out.append(child)
    return out


@pytest.fixture(scope="module")
def prod_dir() -> Path | None:
    d = os.environ.get("VASP_SOP_PROD_DIR")
    if not d:
        pytest.skip("VASP_SOP_PROD_DIR not set (safe skip for CI)")
    p = Path(d).resolve()
    if not p.is_dir():
        pytest.skip(f"VASP_SOP_PROD_DIR={d} is not a directory")
    return p


# ── Phase-level tests ────────────────────────────────────────────────────────

def test_all_systems_have_plan_yaml(prod_dir: Path):
    """Every system directory must have a valid plan.yaml."""
    for d in _systems(prod_dir):
        try:
            p = _plan(d)
            assert "project" in p, f"{d.name}: plan.yaml missing 'project'"
            assert "formula" in p["project"], f"{d.name}: plan.yaml missing formula"
        except (yaml.YAMLError, FileNotFoundError) as exc:
            pytest.fail(f"{d.name}: plan.yaml invalid — {exc}")


def test_phase_artifact_inventory(prod_dir: Path):
    """Each system's on-disk artifacts must be consistent with its phase."""
    anomalies: list[str] = []
    for d in _systems(prod_dir):
        phase = _phase_of(d)
        cpd = d / "cpd"
        tv = cpd / "target_vertices.yaml"
        ce = cpd / "composition_energies.yaml"
        se = cpd / "standard_energies.yaml"
        uc = d / "unitcell"
        df = d / "defect"
        es = df / "defect_energy_summary.json" if df.is_dir() else None
        partial = (
            df / "defect_energy_summary.partial.json" if df.is_dir() else None
        )

        if phase in ("COMPLETE", "UNITCELL_DEFECT"):
            if not tv.is_file():
                anomalies.append(
                    f"{d.name}: {phase} but missing target_vertices.yaml"
                )
            if not se.is_file():
                anomalies.append(
                    f"{d.name}: {phase} but missing standard_energies.yaml"
                )
            if not uc.is_dir():
                anomalies.append(f"{d.name}: {phase} but no unitcell/")
            if not df.is_dir():
                anomalies.append(f"{d.name}: {phase} but no defect/")
        elif phase == "COMPETING":
            if es and es.is_file():
                anomalies.append(
                    f"{d.name}: COMPETING but defect_energy_summary.json exists"
                )
            if partial and partial.is_file():
                anomalies.append(
                    f"{d.name}: COMPETING but defect_energy_summary.partial.json exists"
                )
        elif phase == "CHEM_POT_DIAGRAM":
            if not ce.is_file():
                anomalies.append(
                    f"{d.name}: CHEM_POT_DIAGRAM but no composition_energies.yaml"
                )
            if df.is_dir() and es and es.is_file():
                anomalies.append(
                    f"{d.name}: CHEM_POT_DIAGRAM but final defect_energy_summary.json exists"
                )

    if anomalies:
        pytest.fail("Artifact anomalies found:\n  " + "\n  ".join(anomalies))


def test_defect_summary_integrity(prod_dir: Path):
    """Every *final* defect_energy_summary.json must be valid JSON."""
    errors: list[str] = []
    for d in _systems(prod_dir):
        es = d / "defect" / "defect_energy_summary.json"
        if not es.is_file():
            continue
        errs = _check_summary_invariants(es)
        for e in errs:
            errors.append(f"{d.name}: {e}")

    if errors:
        pytest.fail("Summary integrity violations:\n  " + "\n  ".join(errors))


def test_final_summary_has_correction_coverage_or_status(prod_dir: Path):
    """Final summary implies full correction coverage or analyze_status=full.

    Exposes incomplete post-process left as a final summary (issue #0007).
    Partial work must live in defect_energy_summary.partial.json instead.
    """
    errors: list[str] = []
    for d in _systems(prod_dir):
        df = d / "defect"
        es = df / "defect_energy_summary.json"
        if not es.is_file():
            continue
        eligible = _eligible_defect_dirs(df)
        if not eligible:
            continue
        missing = [
            c.name for c in eligible if not (c / "correction.json").is_file()
        ]
        status_path = df / "analyze_status.json"
        status = None
        if status_path.is_file():
            try:
                status = json.loads(status_path.read_text()).get("status")
            except (json.JSONDecodeError, OSError):
                status = None
        if missing and status != "full":
            # Allow analyze_status full only when no missing; otherwise error
            errors.append(
                f"{d.name}: final summary but {len(missing)}/{len(eligible)} "
                f"eligible defects lack correction.json "
                f"(analyze_status={status!r}); sample missing={missing[:5]}"
            )
        if status is not None and status not in ("full", "partial", "failed"):
            errors.append(f"{d.name}: bad analyze_status {status!r}")

    if errors:
        pytest.fail(
            "Final summary coverage violations:\n  " + "\n  ".join(errors)
        )


def test_unitcell_yaml_structure(prod_dir: Path):
    """unitcell/unitcell.yaml must exist and parse for COMPLETE systems."""
    errors: list[str] = []
    for d in _systems(prod_dir):
        phase = _phase_of(d)
        if phase != "COMPLETE":
            continue
        uy = d / "unitcell" / "unitcell.yaml"
        if not uy.is_file():
            errors.append(f"{d.name}: COMPLETE but missing unitcell/unitcell.yaml")
            continue
        try:
            data = yaml.safe_load(uy.read_text())
            if not isinstance(data, dict):
                errors.append(f"{d.name}: unitcell.yaml is not a dict")
        except yaml.YAMLError as exc:
            errors.append(f"{d.name}: unitcell.yaml parse error — {exc}")

    if errors:
        pytest.fail("Unitcell yaml issues:\n  " + "\n  ".join(errors))


def test_no_orphan_submission_files(prod_dir: Path):
    """No stale .target_submit.json or .submitted files outside expected locations."""
    orphans: list[str] = []
    for d in _systems(prod_dir):
        target_submit = d / "cpd" / ".target_submit.json"
        if target_submit.is_file():
            try:
                data = json.loads(target_submit.read_text())
                if data.get("task_name") == "cached":
                    pass
            except (json.JSONDecodeError, Exception):
                orphans.append(str(target_submit))

        for root_dir, _dirs, files in os.walk(str(d)):
            for f in files:
                if f == ".submitted":
                    orphans.append(os.path.join(root_dir, f))

    if orphans:
        pytest.fail("Orphaned submission files:\n  " + "\n  ".join(orphans))


def test_plan_yaml_round_trip(prod_dir: Path):
    """Each plan.yaml must be loadable by PipelineConfig (no config corruption)."""
    from vasp_sop.core.config import PipelineConfig
    errors: list[str] = []
    for d in _systems(prod_dir):
        try:
            PipelineConfig.from_yaml(d / "plan.yaml", root=d)
        except Exception as exc:
            errors.append(f"{d.name}: {exc}")
    if errors:
        pytest.fail("Config load failures:\n  " + "\n  ".join(errors))
