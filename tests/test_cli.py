"""Tests for vasp_sop.cli.main -- batch run, advance system, dry-run behavior.

These tests verify that the batch pipeline correctly handles dry-run vs real
submission, and that cached submission logic isn't silently skipped.
"""

from pathlib import Path
import yaml
import pytest


@pytest.fixture
def competing_system(tmp_path: Path) -> Path:
    """Create a minimal system in COMPETING phase."""
    formula = "NaCl"
    mpid = "12345"
    root = tmp_path / "system"
    root.mkdir(parents=True)
    cpd = root / "cpd"

    plan = {
        "project": {"formula": formula, "dopant_elements": [],
                     "poscar_src": f"MP mp-{mpid}"},
        "parameters": {"functional": "pbesol"},
        "supercell": {"tool": "doped", "min_distance": 10.0},
    }
    (root / "plan.yaml").write_text(yaml.dump(plan))

    target_dir = cpd / f"{formula}_mp-{mpid}"
    target_dir.mkdir(parents=True)
    _write_poscar(target_dir, 4)
    _write_incar(target_dir)
    _write_potcar(target_dir)
    _write_kpoints(target_dir)
    _write_converged_outcar(target_dir)

    comp_dir = cpd / "Other_mp-99999"
    comp_dir.mkdir(parents=True)
    _write_poscar(comp_dir, 2)
    _write_incar(comp_dir)
    _write_potcar(comp_dir)
    _write_kpoints(comp_dir)
    _write_truncated_outcar(comp_dir)

    return root


def _write_poscar(d: Path, n_atoms: int) -> None:
    """Write a minimal valid POSCAR."""
    lines = [
        "Test POSCAR",
        "1.0",
        "10.0 0.0 0.0",
        "0.0 10.0 0.0",
        "0.0 0.0 10.0",
        "X",
        str(n_atoms),
        "Direct",
    ]
    for i in range(n_atoms):
        lines.append(f"{i/n_atoms:.6f} {i/n_atoms:.6f} {i/n_atoms:.6f}")
    (d / "POSCAR").write_text("\n".join(lines) + "\n")


def _write_incar(d: Path) -> None:
    (d / "INCAR").write_text("SYSTEM = test\n")


def _write_potcar(d: Path) -> None:
    (d / "POTCAR").write_text("dummy POTCAR\n")


def _write_kpoints(d: Path) -> None:
    text = "k-points\n0\nGamma\n1 1 1\n0 0 0\n"
    (d / "KPOINTS").write_text(text)


def _write_converged_outcar(d: Path) -> None:
    text = (" some header\n"
            "  reached required accuracy - convergence\n"
            "  reached required accuracy - convergence\n")
    (d / "OUTCAR").write_text(text)


def _write_truncated_outcar(d: Path) -> None:
    (d / "OUTCAR").write_text("some header\n  reached required\n")


def _make_system_dict(root: Path) -> dict:
    """Build the system dict that _advance_one_system expects."""
    from vasp_sop.core.config import PipelineConfig
    plan = yaml.safe_load((root / "plan.yaml").read_text())
    config = PipelineConfig.from_plan(plan, root=root)
    src = config.poscar_src
    mpid = src.split("mp-", 1)[1] if src.startswith("MP mp-") else None
    return {
        "name": config.formula or root.name,
        "root": root,
        "config": config,
        "formula": config.formula,
        "mpid": mpid,
    }


# Tests


class TestAdvanceOneSystem:
    """_advance_one_system -- dry-run vs real submission."""

    @pytest.fixture(autouse=True)
    def _patch_heavy(self, monkeypatch):
        monkeypatch.setattr("vasp_sop.defect.builder.build_all", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.vasp.io.check_converged",
                            lambda p: "NaCl_mp-12345" in str(p))
        monkeypatch.setattr("vasp_sop.defect.cpd.compute_chemical_potentials",
                            lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.cpd._get_target_composition",
                            lambda *a: {})

    def test_dry_run_does_not_submit(self, competing_system, monkeypatch):
        calls = []
        monkeypatch.setattr("vasp_sop.core.jobs.submit_vasp",
                            lambda p: (calls.append(p) or
                                       type("J", (), {"task_name": "t"})()))
        from vasp_sop.cli.main import _advance_one_system
        s = _make_system_dict(competing_system)
        _advance_one_system(s, {}, dry_run=True)
        assert len(calls) == 0

    def test_non_dry_submits_competing(self, competing_system, monkeypatch):
        calls = []
        monkeypatch.setattr("vasp_sop.core.jobs.submit_vasp",
                            lambda p: (calls.append(p) or
                                       type("J", (), {"task_name": "t"})()))
        from vasp_sop.cli.main import _advance_one_system
        s = _make_system_dict(competing_system)
        _advance_one_system(s, {}, dry_run=False)
        assert len(calls) >= 1
        comp_dir = str(competing_system / "cpd" / "Other_mp-99999")
        assert comp_dir in {str(p) for p in calls}


class TestCachePutGet:
    _cr: Path | None = None

    @pytest.fixture(autouse=True)
    def _isolate_cache(self, tmp_path: Path) -> None:
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")

    def test_roundtrip(self, tmp_path: Path):
        from vasp_sop.core.cache import calc_results_put, calc_results_get
        src = tmp_path / "src"
        src.mkdir()
        (src / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -10.0 eV\n"
            " General timing and accounting\n"
        )
        (src / "CONTCAR").write_text(
            "H\n1.0\n10 0 0\n0 10 0\n0 0 10\nH\n1\nDirect\n0 0 0\n"
        )
        calc_results_put("TestMe", "42", src)
        cached = calc_results_get("TestMe", "42")
        assert cached is not None
        assert cached["total_energy"] == -10.0
        assert cached["converged"] == 1

    def test_get_missing_returns_none(self):
        from vasp_sop.core.cache import calc_results_get
        assert calc_results_get("Never", "cached") is None

    def test_put_does_not_delete_others(self, tmp_path: Path):
        from vasp_sop.core.cache import calc_results_put, calc_results_get
        src1 = tmp_path / "src1"
        src1.mkdir()
        (src1 / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -10.0 eV\n"
            " General timing and accounting\n"
        )
        calc_results_put("First", "1", src1)
        src2 = tmp_path / "src2"
        src2.mkdir()
        (src2 / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -10.0 eV\n"
            " General timing and accounting\n"
        )
        calc_results_put("Second", "2", src2)
        assert calc_results_get("First", "1") is not None
        assert calc_results_get("Second", "2") is not None
