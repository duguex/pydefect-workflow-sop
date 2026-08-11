"""Tests for vasp_sop.core.dir_status — one authoritative state per dir."""

from pathlib import Path

from vasp_sop.core.dir_status import dir_status


def _inputs(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "INCAR").write_text("NSW = 50\nIBRION = 2\nEDIFFG = -0.01\n")
    (d / "POSCAR").write_text("scale\n1.0\nNa Cl\n1 1\nDirect\n0 0 0\n0.5 0.5 0.5\n")
    (d / "POTCAR").write_text("POTCAR\n")
    (d / "KPOINTS").write_text("k\n")


def _converged(d: Path) -> None:
    (d / "OUTCAR").write_text(
        "NSW = 50\nIBRION = 2\nEDIFFG = -0.01\n"
        " General timing and accounting informations for this job:\n"
        " TOTAL-FORCE (eV/Angst)\n"
        " ---\n"
        " 0.001 0.001 0.001 0.002 0.001 0.001\n"
    )


def _log(d: Path, name: str, tail: str) -> None:
    (d / name).write_text(tail + "\n")


class TestDirStatus:
    def test_converged(self, tmp_path: Path):
        _inputs(tmp_path)
        _converged(tmp_path)
        assert dir_status(tmp_path).state == "converged"

    def test_never_ran(self, tmp_path: Path):
        _inputs(tmp_path)
        assert dir_status(tmp_path).state == "never_ran"

    def test_missing_inputs(self, tmp_path: Path):
        (tmp_path / "INCAR").write_text("x\n")
        assert dir_status(tmp_path).state == "missing_inputs"

    def test_history_incomplete_warns(self, tmp_path: Path):
        """Disk ran + CRISP_COMPLETED, but agent.db never saw it — the
        2026 Gd cpd incident class."""
        _inputs(tmp_path)
        _log(tmp_path, "206555.log", "reached required accuracy\nCRISP_COMPLETED\n")
        s = dir_status(tmp_path)
        assert s.state == "history_incomplete"
        assert any("agent.db has no record" in w for w in s.warnings)

    def test_failed_from_db(self, tmp_path: Path, monkeypatch):
        _inputs(tmp_path)
        _log(tmp_path, "1.log", "CRISP_FAILED\nEXIT_CODE: 1\n")
        monkeypatch.setattr(
            "vasp_sop.core.dir_status._db_history",
            lambda d: ([("failed", "2026-08-08T12:00", "EXIT_CODE: 1")], "failed"),
        )
        assert dir_status(tmp_path).state == "failed"

    def test_running_with_fresh_log(self, tmp_path: Path, monkeypatch):
        _inputs(tmp_path)
        _log(tmp_path, "1.log", "")
        monkeypatch.setattr(
            "vasp_sop.core.dir_status._db_history",
            lambda d: ([("running", "2026-08-11T16:00", "")], "running"),
        )
        s = dir_status(tmp_path, now=tmp_path.stat().st_mtime + 3600)
        assert s.state == "running"

    def test_stalled_when_db_running_but_log_stale(self, tmp_path, monkeypatch):
        _inputs(tmp_path)
        _log(tmp_path, "1.log", "")
        monkeypatch.setattr(
            "vasp_sop.core.dir_status._db_history",
            lambda d: ([("running", "2026-08-11T16:00", "")], "running"),
        )
        s = dir_status(tmp_path, now=tmp_path.stat().st_mtime + 12 * 3600)
        assert s.state == "stalled"

    def test_excluded_defect_antisite(self, tmp_path: Path):
        d = tmp_path / "defect" / "O_Na1_0"
        _inputs(d)
        assert dir_status(d).state == "excluded"

    def test_excluded_cpd_phase(self, tmp_path: Path):
        sys = tmp_path
        d = sys / "cpd" / "Big_mp-999"
        _inputs(d)
        (sys / "cpd_excluded_phases.yaml").write_text("- Big_mp-999\n")
        assert dir_status(d).state == "excluded"

    def test_drift_regen_pending(self, tmp_path: Path):
        _inputs(tmp_path)
        _converged(tmp_path)
        import os
        os.utime(tmp_path / "INCAR", (tmp_path.stat().st_mtime + 100,) * 2)
        s = dir_status(tmp_path)
        assert any("DRIFT: INCAR newer" in e for e in s.evidence)
