"""Tests for vasp_sop.core.blockers — block-reason classification (ADR 0007)."""

from pathlib import Path

import pytest

from vasp_sop.core.blockers import Block, classify_dir, scan_system


def _minimal(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "INCAR").write_text("ENCUT = 520\nNSW = 50\nIBRION = 2\nEDIFFG = -0.005\n")
    (d / "POSCAR").write_text("scale\n1.0\nNa Cl\n1 1\nDirect\n0 0 0\n0.5 0.5 0.5\n")
    (d / "POTCAR").write_text("POTCAR\n")
    (d / "KPOINTS").write_text("k\n")


def _converged_outcar(d: Path) -> None:
    (d / "OUTCAR").write_text(
        "NSW = 50\nIBRION = 2\nEDIFFG = -0.005\n"
        " General timing and accounting informations for this job:\n"
        " TOTAL-FORCE (eV/Angst)\n"
        " ---\n"
        " 0.001 0.001 0.001 0.002 0.001 0.001\n"
    )


def _unconverged_outcar(d: Path) -> None:
    (d / "OUTCAR").write_text(
        "NSW = 50\nIBRION = 2\nEDIFFG = -0.005\n"
        " General timing and accounting informations for this job:\n"
        " TOTAL-FORCE (eV/Angst)\n"
        " ---\n"
        " 0.5 0.5 0.5 0.2 0.2 0.2\n"
    )


class TestClassifyDir:
    def test_converged_is_done(self, tmp_path: Path):
        _minimal(tmp_path)
        _converged_outcar(tmp_path)
        assert classify_dir(tmp_path) == Block("done")

    def test_crashed(self, tmp_path: Path):
        _minimal(tmp_path)
        (tmp_path / "OUTCAR").write_text("some header\nscf loop\n")
        b = classify_dir(tmp_path)
        assert b.reason == "crashed"

    def test_unconverged(self, tmp_path: Path):
        _minimal(tmp_path)
        _unconverged_outcar(tmp_path)
        b = classify_dir(tmp_path)
        assert b.reason == "unconverged"
        assert "force_gate_fail" in b.detail

    def test_never_ran(self, tmp_path: Path):
        _minimal(tmp_path)
        assert classify_dir(tmp_path) == Block("never_ran")

    def test_missing_inputs(self, tmp_path: Path):
        _minimal(tmp_path)
        (tmp_path / "POTCAR").unlink()
        b = classify_dir(tmp_path)
        assert b.reason == "missing_inputs"
        assert "POTCAR" in b.detail

    def test_missing_outcar_no_inputs_prefers_missing_inputs(self, tmp_path):
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / "INCAR").write_text("x\n")
        b = classify_dir(tmp_path)
        assert b.reason == "missing_inputs"


class TestScanSystem:
    def test_scan_system_lists_only_blocked(self, tmp_path: Path):
        sys = tmp_path / "NaCl"
        done = sys / "cpd" / "NaCl_mp-1"
        crashed = sys / "defect" / "Va_Na_0"
        _minimal(done)
        _converged_outcar(done)
        _minimal(crashed)
        (crashed / "OUTCAR").write_text("boom\n")
        (sys / "plan.yaml").write_text("x\n")

        blocks = scan_system(sys)
        assert "cpd/NaCl_mp-1" not in blocks  # done → not reported
        assert blocks["defect/Va_Na_0"].reason == "crashed"
        assert blocks["defect/Va_Na_0"].path == str(crashed)

    def test_scan_system_skips_adr0013_excluded_dirs(self, tmp_path: Path):
        """Anion-cation antisites / defect_new are never audited (ADR 0013)."""
        sys = tmp_path / "NaCl"
        antisite = sys / "defect" / "O_Na1_0"  # anion on cation site
        defect_new = sys / "defect" / "defect_new"
        _minimal(antisite)
        _minimal(defect_new)
        (sys / "plan.yaml").write_text("x\n")

        blocks = scan_system(sys)
        assert not blocks  # excluded dirs are not blockers

    def test_scan_system_keeps_valid_dopant_substitutions(self, tmp_path):
        """Metal-site substitutions (Fe_Al, Bi_Sb) stay auditable."""
        sys = tmp_path / "NaCl"
        sub = sys / "defect" / "Fe_Na1_0"
        _minimal(sub)
        (sys / "plan.yaml").write_text("x\n")

        blocks = scan_system(sys)
        assert blocks["defect/Fe_Na1_0"].reason == "never_ran"