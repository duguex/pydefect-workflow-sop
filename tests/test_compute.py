"""Tests for vasp_sop.defect.compute — run_vasp with CONTCAR restart loop."""

from pathlib import Path

import pytest


def _make_minimal_outcar(d: Path, max_force: float = 0.01) -> None:
    """Write a minimal OUTCAR with convergence marker."""
    text = (
        " General timing and accounting\n"
        " TOTAL-FORCE (eV/Angst)\n"
        " ---\n"
        "   0.000000   0.000000   0.000000  "
        f"{max_force:.6f} {max_force:.6f} {max_force:.6f}\n"
    )
    (d / "OUTCAR").write_text(text)


def _make_stalled_outcar(d: Path, max_force: float = 0.5) -> None:
    """Write an OUTCAR with unconverged forces (no timing marker)."""
    text = (
        " TOTAL-FORCE (eV/Angst)\n"
        " ---\n"
        "   0.000000   0.000000   0.000000  "
        f"{max_force:.6f} {max_force:.6f} {max_force:.6f}\n"
    )
    (d / "OUTCAR").write_text(text)


def _make_defect_dir(root: Path, name: str) -> Path:
    """Create a defect directory with minimal VASP input files."""
    d = root / name
    d.mkdir()
    (d / "INCAR").write_text("SYSTEM = test\n")
    (d / "CONTCAR").write_text("dummy\n")
    (d / "KPOINTS").write_text("k-points\n0\nGamma\n1 1 1\n0 0 0\n")
    (d / "POSCAR").write_text(
        "X\n1.0\n10 0 0\n0 10 0\n0 0 10\nX\n1\nDirect\n0 0 0\n"
    )
    return d


def _mock_job(work_dir: Path):
    """Return a mock VaspJob-like object."""
    return type("J", (), {
        "work_dir": work_dir,
        "poll": lambda self=None, *a: 0,
        "task_name": "t",
    })()


class TestRunVasp:
    """run_vasp — defect VASP submission with CONTCAR restart."""

    def test_raises_without_perfect_dir(self, tmp_path: Path):
        """Without a perfect/ subdirectory, run_vasp raises immediately."""
        from vasp_sop.defect.compute import run_vasp
        with pytest.raises(RuntimeError, match="Perfect supercell directory"):
            run_vasp(tmp_path)

    def test_breaks_when_all_converged(self, tmp_path: Path, monkeypatch):
        """When all jobs are already converged, run_vasp returns immediately."""
        perfect = tmp_path / "perfect"
        perfect.mkdir()
        _make_minimal_outcar(perfect)

        submitted = []
        monkeypatch.setattr(
            "vasp_sop.defect.compute.submit_vasp",
            lambda p: submitted.append(p) or _mock_job(p),
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.move_crisp_outputs", lambda p: None,
        )

        from vasp_sop.defect.compute import run_vasp
        run_vasp(tmp_path)
        assert len(submitted) == 0

    def test_submits_unconverged_defects(self, tmp_path: Path, monkeypatch):
        """Unconverged defect dirs with CONTCAR get restarted and submitted."""
        perfect = tmp_path / "perfect"
        perfect.mkdir()
        _make_minimal_outcar(perfect)
        defect_dir = _make_defect_dir(tmp_path, "Va_X_0")
        _make_stalled_outcar(defect_dir)

        submitted = []
        monkeypatch.setattr(
            "vasp_sop.defect.compute.check_converged",
            lambda p: "perfect" in str(p),
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.input_ready",
            lambda p: p.is_dir() and p.name != "perfect",
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.submit_vasp",
            lambda p: submitted.append(p) or _mock_job(p),
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.move_crisp_outputs", lambda p: None,
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.restart_from_contcar", lambda p: None,
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.diagnose_failure", lambda p: None,
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.recommended_fix", lambda p: None,
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.time.sleep", lambda s: None,
        )

        from vasp_sop.defect.compute import run_vasp
        run_vasp(tmp_path)
        assert len(submitted) >= 1
        assert any("Va_X_0" in str(s) for s in submitted)

    def test_stalled_detection_skips_submission(self, tmp_path: Path, monkeypatch):
        """A stalled job (max_f not decreasing) is not submitted in the
        current iteration."""
        perfect = tmp_path / "perfect"
        perfect.mkdir()
        _make_minimal_outcar(perfect, max_force=0.01)

        defect_dir = _make_defect_dir(tmp_path, "Va_X_0")
        _make_stalled_outcar(defect_dir, max_force=0.5)
        (defect_dir / "INCAR").write_text("POTIM = 0.5\nSYSTEM = test\n")

        submitted = []
        monkeypatch.setattr(
            "vasp_sop.defect.compute.check_converged",
            lambda p: "perfect" in str(p),
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.input_ready",
            lambda p: p.is_dir() and p.name != "perfect",
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.submit_vasp",
            lambda p: submitted.append(p) or _mock_job(p),
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.move_crisp_outputs", lambda p: None,
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.restart_from_contcar", lambda p: None,
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.diagnose_failure", lambda p: None,
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.recommended_fix", lambda p: None,
        )
        monkeypatch.setattr(
            "vasp_sop.defect.compute.time.sleep", lambda s: None,
        )

        from vasp_sop.defect.compute import run_vasp
        run_vasp(tmp_path)
        # The only defect dir is stalled — POTIM is increased but it
        # stays in the stalled set and is excluded from submission.
        # First attempt sees prev_forces={} → old_f=999, cur_f=0.5
        # → NOT stalled → submits once.
        # Second attempt sees prev_forces={"Va_X_0": 0.5} → stalled → skipped.
        assert len(submitted) == 1, (
            f"expected 1 submission (first cycle not stalled), got {len(submitted)}"
        )
