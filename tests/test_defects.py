"""Tests for VASP convergence check — ``check_converged`` from vasp.io."""

from pathlib import Path

import pytest

from vasp_sop.vasp.io import check_converged


def _make_outcar(dir_path: Path, nsw: int = 50, ediffg: float = -0.03,
                 last_ionic_step: int = 3, max_force: float = 0.01,
                 completed: bool = True) -> Path:
    """Write a synthetic OUTCAR with specified convergence behavior."""
    lines = [f"  NSW = {nsw}", f"  EDIFFG = {ediffg}"]
    # Add iteration markers for each ionic step
    for ionic in range(1, last_ionic_step + 1):
        for elec in range(1, 6):
            lines.append(f"--------------------------------------- Iteration {elec:4d}({ionic:4d})  ---")

    # TOTAL-FORCE block
    lines.append(" POSITION                                       TOTAL-FORCE (eV/Angst)")
    lines.append("-" * 80)
    # One atom at (0,0,0) with specified force
    lines.append(f"     0.00000      0.00000      0.00000      {max_force:.6f}      0.00000      0.00000")
    lines.append("")
    lines.append("")

    if completed:
        lines.append("\n General timing and accounting informations for this job:\n")

    outcar = dir_path / "OUTCAR"
    outcar.write_text("\n".join(lines))
    return outcar


class TestVaspJobDone:
    def test_converged(self, tmp_path: Path):
        """Normal convergence: max_f < |EDIFFG|."""
        _make_outcar(tmp_path, nsw=50, last_ionic_step=5, max_force=0.01)
        assert check_converged(tmp_path) is True

    def test_unconverged(self, tmp_path: Path):
        """Completed but forces too high: max_f >= |EDIFFG|."""
        _make_outcar(tmp_path, nsw=50, last_ionic_step=50, max_force=0.5)
        assert check_converged(tmp_path) is False

    def test_truncated(self, tmp_path: Path):
        """VASP did not finish — no 'General timing and accounting'."""
        _make_outcar(tmp_path, completed=False)
        assert check_converged(tmp_path) is False

    def test_no_outcar(self, tmp_path: Path):
        """No OUTCAR file at all."""
        assert check_converged(tmp_path) is False

    def test_empty_outcar(self, tmp_path: Path):
        """OUTCAR exists but is empty."""
        (tmp_path / "OUTCAR").write_text("")
        assert check_converged(tmp_path) is False

    def test_missing_force_block(self, tmp_path: Path):
        """OUTCAR has completion but no TOTAL-FORCE block."""
        lines = ["  NSW = 50", "  EDIFFG = -0.03",
                 " General timing and accounting informations for this job:"]
        (tmp_path / "OUTCAR").write_text("\n".join(lines))
        assert check_converged(tmp_path) is False

    def test_many_atoms_converged(self, tmp_path: Path):
        """Multiple atoms, all forces below threshold."""
        lines = ["  NSW = 50", "  EDIFFG = -0.03"]
        for i in range(1, 6):
            lines.append(f"--------------------------------------- Iteration {i:4d}(  1)  ---")
        lines.append(" POSITION                                       TOTAL-FORCE (eV/Angst)")
        lines.append("-" * 80)
        for _ in range(5):
            lines.append("     0.00000      0.00000      0.00000      0.02000      0.01500      0.02500")
        lines.append("")
        lines.append("\n General timing and accounting informations for this job:\n")
        (tmp_path / "OUTCAR").write_text("\n".join(lines))
        # max_f = 0.025 < 0.03 → converged
        assert check_converged(tmp_path) is True

    def test_converged_output_subdir(self, tmp_path: Path):
        """OUTCAR in output/ subdirectory (crisp style)."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        _make_outcar(output_dir, nsw=50, last_ionic_step=4, max_force=0.02)
        assert check_converged(tmp_path) is True
