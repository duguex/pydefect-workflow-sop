"""Tests for vasp_sop.defect.compute — run_vasp with CONTCAR restart loop."""

from pathlib import Path

import pytest

from vasp_sop.vasp.convergence import (
    ConvergenceVerdict,
    convergence_verdict as _real_verdict,
)


def _mk_verdict(predicate, p):
    """Verdict with a mocked converged decision but real force evidence.

    Mirrors legacy behaviour where the convergence *gate* was mocked while
    max|F| came from the real OUTCAR read.
    """
    return ConvergenceVerdict(
        predicate(p), "mock", max_f=_real_verdict(p).max_f
    )


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
            "vasp_sop.defect.compute.convergence_verdict",
            lambda p: _mk_verdict(lambda q: "perfect" in str(q), p),
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

    def test_stalled_with_correction_resubmits(self, tmp_path: Path, monkeypatch):
        """A stalled job with successful correction is resubmitted."""
        perfect = tmp_path / "perfect"
        perfect.mkdir()
        _make_minimal_outcar(perfect)

        defect = _make_defect_dir(tmp_path, "Va_X_0")
        _make_stalled_outcar(defect, max_force=0.5)

        submitted = []
        corrected = []
        converge_count = [0]
        monkeypatch.setattr("vasp_sop.defect.compute.input_ready", lambda p: True)
        monkeypatch.setattr("vasp_sop.defect.compute.convergence_verdict",
                           lambda p: _mk_verdict(lambda q: "perfect" in str(q) or converge_count[0] > 1, p))
        monkeypatch.setattr("vasp_sop.defect.compute.submit_vasp",
                           lambda p: (submitted.append(p), converge_count.__setitem__(0, converge_count[0] + 1), _mock_job(p))[-1])
        monkeypatch.setattr("vasp_sop.defect.compute.move_crisp_outputs", lambda p: None)
        monkeypatch.setattr("vasp_sop.defect.compute.restart_from_contcar", lambda p: None)
        monkeypatch.setattr("vasp_sop.defect.compute.diagnose_failure", lambda p: None)
        monkeypatch.setattr("vasp_sop.defect.compute.recommended_fix", lambda p: None)
        monkeypatch.setattr("vasp_sop.defect.compute.time.sleep", lambda s: None)
        monkeypatch.setattr("vasp_sop.defect.compute.apply_correction",
                           lambda d, f, a: (corrected.append(d.name) or True))

        from vasp_sop.defect.compute import run_vasp
        run_vasp(tmp_path)
        assert len(corrected) >= 1, "apply_correction should be called"
        assert 2 <= len(submitted) <= 3, (
            f"should resubmit once after correction then converge, got {len(submitted)}")

    def test_stalled_never_converges_exits_after_max_attempts(self, tmp_path: Path, monkeypatch):
        """Stalled job that never converges stops after max attempts."""
        perfect = tmp_path / "perfect"
        perfect.mkdir()
        _make_minimal_outcar(perfect)

        defect = _make_defect_dir(tmp_path, "Va_X_0")
        _make_stalled_outcar(defect, max_force=0.5)

        monkeypatch.setattr("vasp_sop.defect.compute.convergence_verdict",
                           lambda p: _mk_verdict(lambda q: "perfect" in str(q), p))
        submitted = []
        monkeypatch.setattr("vasp_sop.defect.compute.input_ready", lambda p: True)
        monkeypatch.setattr("vasp_sop.defect.compute.submit_vasp",
                           lambda p: submitted.append(p) or _mock_job(p))
        monkeypatch.setattr("vasp_sop.defect.compute.move_crisp_outputs", lambda p: None)
        monkeypatch.setattr("vasp_sop.defect.compute.restart_from_contcar", lambda p: None)
        monkeypatch.setattr("vasp_sop.defect.compute.diagnose_failure", lambda p: None)
        monkeypatch.setattr("vasp_sop.defect.compute.recommended_fix", lambda p: None)
        monkeypatch.setattr("vasp_sop.defect.compute.time.sleep", lambda s: None)
        monkeypatch.setattr("vasp_sop.defect.compute.apply_correction",
                           lambda d, f, a: True)

        from vasp_sop.defect.compute import run_vasp
        run_vasp(tmp_path)
        assert len(submitted) <= 20, "should stop at max attempts"
