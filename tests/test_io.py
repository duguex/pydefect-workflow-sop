"""Tests for vasp_sop.vasp.io — check_converged, check_task_complete."""

from pathlib import Path
import pytest


def _write_converged_outcar(d: Path) -> None:
    """OUTCAR that satisfies check_converged."""
    text = (
        " General timing and accounting\n"
        "   100.00% CPU utilisation\n"
        " TOTAL-FORCE (eV/Angst)\n"
        " ---\n"
        " 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000\n"
    )
    (d / "OUTCAR").write_text(text)


def _write_incar(d: Path) -> None:
    (d / "INCAR").write_text("SYSTEM = test\n")


class TestCheckTaskComplete:
    """check_task_complete: output-completeness per task type."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path):
        self.dir = tmp_path / "task"
        self.dir.mkdir()
        _write_incar(self.dir)

    def test_band_with_vasprxml(self):
        """band: converged OUTCAR + vasprun.xml → True."""
        _write_converged_outcar(self.dir)
        (self.dir / "vasprun.xml").write_text("<vasprun></vasprun>")
        from vasp_sop.vasp.io import check_task_complete
        assert check_task_complete(self.dir, "band")

    def test_band_without_vasprxml(self):
        """band: converged OUTCAR only → False (missing vasprun.xml)."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert not check_task_complete(self.dir, "band")

    def test_band_unconverged(self):
        """band: unconverged OUTCAR → False regardless of vasprun.xml."""
        (self.dir / "OUTCAR").write_text("some header\n")
        (self.dir / "vasprun.xml").write_text("<vasprun></vasprun>")
        from vasp_sop.vasp.io import check_task_complete
        assert not check_task_complete(self.dir, "band")

    def test_band_no_output(self):
        """band: no OUTCAR at all → False."""
        from vasp_sop.vasp.io import check_task_complete
        assert not check_task_complete(self.dir, "band")

    def test_dos_missing_vasprxml(self):
        """dos: converged OUTCAR only → False."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert not check_task_complete(self.dir, "dos")

    def test_dielectric_without_vasprxml(self):
        """dielectric: converged OUTCAR only → True (no vasprun.xml needed)."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert check_task_complete(self.dir, "dielectric")

    def test_default_task_type(self):
        """default (task_type=""): delegates to check_converged — converged → True."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert check_task_complete(self.dir)

    def test_unknown_task_type(self):
        """unknown task_type: delegates to check_converged."""
        _write_converged_outcar(self.dir)
        from vasp_sop.vasp.io import check_task_complete
        assert check_task_complete(self.dir, "phonon")
