"""Tests for vasp_sop.vasp.errors — VASP error diagnosis."""

from pathlib import Path

import pytest

from vasp_sop.vasp.errors import diagnose_failure, recommended_fix


class TestDiagnoseFailure:
    """Tests for VASP error pattern matching on OUTCAR text."""

    def test_frozen_job_edddav(self, tmp_path: Path):
        outcar = tmp_path / "OUTCAR"
        outcar.write_text("EDDDAV: call to DAV failed")
        assert diagnose_failure(outcar) == "frozen_job"
        fix = recommended_fix("frozen_job")
        assert fix is not None
        assert "shaking" in fix.lower() or "potim" in fix.lower()
        assert recommended_fix("nonexistent") is None

    def test_frozen_job_zpotrf(self, tmp_path: Path):
        outcar = tmp_path / "OUTCAR"
        outcar.write_text("ZPOTRF: routine failed")
        assert diagnose_failure(outcar) == "frozen_job"

    def test_positive_energy(self, tmp_path: Path):
        outcar = tmp_path / "OUTCAR"
        outcar.write_text("the total energy is positive")
        assert diagnose_failure(outcar) == "positive_energy"
        fix = recommended_fix("positive_energy")
        assert "SIGMA" in fix

    def test_scf_no_converge(self, tmp_path: Path):
        outcar = tmp_path / "OUTCAR"
        outcar.write_text("WARNING: Sub-Space-Matrix is not hermitian in DAV")
        assert diagnose_failure(outcar) == "scf_no_converge"

    def test_brion_error(self, tmp_path: Path):
        outcar = tmp_path / "OUTCAR"
        outcar.write_text("BRION: computational error")
        assert diagnose_failure(outcar) == "brion_error"

    def test_no_match(self, tmp_path: Path):
        outcar = tmp_path / "OUTCAR"
        outcar.write_text("General timing and accounting\nreached required accuracy")
        assert diagnose_failure(outcar) is None

    def test_missing_file(self, tmp_path: Path):
        assert diagnose_failure(tmp_path / "nonexistent") is None

    def test_tail_search(self, tmp_path: Path):
        """Pattern in the last 16KB is detected (prioritized over full text)."""
        outcar = tmp_path / "OUTCAR"
        header = "normal output\n" * 5000  # large header
        body = ("normal output\n" * 300)
        tail = "EDDRMM: call to DAV failed\n" + ("normal\n" * 100)
        outcar.write_text(header + body + tail)
        # Should find EDDRMM in tail (last 16KB)
        assert diagnose_failure(outcar) == "frozen_job"