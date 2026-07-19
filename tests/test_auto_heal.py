"""Tests for vasp_sop.vasp.auto_heal — VASP error auto-healing (#51)."""

from pathlib import Path

import pytest

from vasp_sop.vasp.auto_heal import (
    CORRECTION_REGISTRY,
    apply_correction,
)
from vasp_sop.vasp.io import patch_incar, read_incar, write_incar


class TestIncarHelpers:
    """Tests for centralized INCAR read/write/patch utilities."""

    def test_read_incar_basic(self, tmp_path: Path):
        """Read standard TAG = value format."""
        incar = tmp_path / "INCAR"
        incar.write_text("ENCUT = 520\nISMEAR = 0\nSIGMA = 0.05\n")
        params = read_incar(tmp_path)
        assert params["ENCUT"] == "520"
        assert params["ISMEAR"] == "0"
        assert params["SIGMA"] == "0.05"

    def test_read_incar_comments(self, tmp_path: Path):
        """Comments and blank lines are skipped."""
        incar = tmp_path / "INCAR"
        incar.write_text("# comment\nENCUT = 400\n\n! another\nNSW = 100\n")
        params = read_incar(tmp_path)
        assert params == {"ENCUT": "400", "NSW": "100"}

    def test_read_incar_missing(self, tmp_path: Path):
        """Missing INCAR returns empty dict."""
        assert read_incar(tmp_path) == {}

    def test_write_incar(self, tmp_path: Path):
        """Write dict to INCAR file."""
        write_incar(tmp_path, {"ENCUT": "520", "ALGO": "Normal"})
        text = (tmp_path / "INCAR").read_text()
        assert "ENCUT = 520" in text
        assert "ALGO = Normal" in text

    def test_patch_incar_updates_existing(self, tmp_path: Path):
        """patch_incar modifies only specified tags."""
        (tmp_path / "INCAR").write_text("ENCUT = 400\nNSW = 50\nISMEAR = 1\n")
        patch_incar(tmp_path, NSW=200, SIGMA=0.1)
        params = read_incar(tmp_path)
        assert params["ENCUT"] == "400"
        assert params["NSW"] == "200"
        assert params["SIGMA"] == "0.1"
        assert params["ISMEAR"] == "1"

    def test_patch_incar_creates_file(self, tmp_path: Path):
        """patch_incar works even if INCAR doesn't exist yet."""
        patch_incar(tmp_path, ALGO="Fast")
        params = read_incar(tmp_path)
        assert params["ALGO"] == "Fast"


class TestCorrectionRegistry:
    """Tests for the correction registry completeness."""

    def test_registry_has_required_keys(self):
        """Registry covers all 5 required error types."""
        required = {"positive_energy", "frozen_job", "scf_no_converge", "edwav", "brion_error"}
        assert required.issubset(set(CORRECTION_REGISTRY.keys()))

    def test_registry_values_callable(self):
        """All registry entries are callable."""
        for name, fn in CORRECTION_REGISTRY.items():
            assert callable(fn), f"{name} is not callable"


class TestApplyCorrection:
    """Tests for apply_correction with synthetic scenarios."""

    def _setup_dir(self, tmp_path: Path) -> Path:
        """Create a minimal VASP work directory."""
        (tmp_path / "INCAR").write_text("ENCUT = 520\nNSW = 100\nPOTIM = 0.5\nIBRION = 2\n")
        (tmp_path / "CONTCAR").write_text("fake contcar\n")
        (tmp_path / "POSCAR").write_text("fake poscar\n")
        return tmp_path

    def test_positive_energy_stage1(self, tmp_path: Path):
        """Stage 1 positive_energy sets ISMEAR=0, SIGMA=0.1."""
        self._setup_dir(tmp_path)
        result = apply_correction(tmp_path, "positive_energy", 1)
        assert result is True
        params = read_incar(tmp_path)
        assert params["ISMEAR"] == "0"
        assert params["SIGMA"] == "0.1"

    def test_positive_energy_stage3(self, tmp_path: Path):
        """Stage 3 positive_energy adds ALGO=Normal."""
        self._setup_dir(tmp_path)
        apply_correction(tmp_path, "positive_energy", 3)
        params = read_incar(tmp_path)
        assert params["ALGO"] == "Normal"
        assert params["SIGMA"] == "0.2"

    def test_frozen_job_stage1(self, tmp_path: Path):
        """Stage 1 frozen_job increases POTIM by 1.5x."""
        self._setup_dir(tmp_path)
        apply_correction(tmp_path, "frozen_job", 1)
        params = read_incar(tmp_path)
        assert float(params["POTIM"]) == pytest.approx(0.75)

    def test_frozen_job_stage2(self, tmp_path: Path):
        """Stage 2 frozen_job switches to IBRION=2, POTIM=1.0."""
        self._setup_dir(tmp_path)
        apply_correction(tmp_path, "frozen_job", 2)
        params = read_incar(tmp_path)
        assert params["IBRION"] == "2"
        assert params["POTIM"] == "1.0"

    def test_frozen_job_stage3(self, tmp_path: Path):
        """Stage 3 frozen_job uses damped MD (IBRION=3)."""
        self._setup_dir(tmp_path)
        apply_correction(tmp_path, "frozen_job", 3)
        params = read_incar(tmp_path)
        assert params["IBRION"] == "3"
        assert params["SMASS"] == "3"

    def test_scf_no_converge_stage1(self, tmp_path: Path):
        """Stage 1 scf reduces mixing."""
        self._setup_dir(tmp_path)
        apply_correction(tmp_path, "scf_no_converge", 1)
        params = read_incar(tmp_path)
        assert params["AMIX"] == "0.1"
        assert params["BMIX"] == "0.001"

    def test_scf_no_converge_stage2(self, tmp_path: Path):
        """Stage 2 scf switches ALGO to Normal."""
        self._setup_dir(tmp_path)
        apply_correction(tmp_path, "scf_no_converge", 2)
        params = read_incar(tmp_path)
        assert params["ALGO"] == "Normal"

    def test_scf_no_converge_stage3(self, tmp_path: Path):
        """Stage 3 scf uses ALGO=All with magnetic mixing."""
        self._setup_dir(tmp_path)
        apply_correction(tmp_path, "scf_no_converge", 3)
        params = read_incar(tmp_path)
        assert params["ALGO"] == "All"
        assert params["AMIX_MAG"] == "0.02"

    def test_edwav_stage1(self, tmp_path: Path):
        """Stage 1 edwav reduces POTIM by half."""
        self._setup_dir(tmp_path)
        apply_correction(tmp_path, "edwav", 1)
        params = read_incar(tmp_path)
        assert float(params["POTIM"]) == pytest.approx(0.25)

    def test_edwav_stage2(self, tmp_path: Path):
        """Stage 2 edwav uses ALGO=Normal, POTIM=0.1."""
        self._setup_dir(tmp_path)
        apply_correction(tmp_path, "edwav", 2)
        params = read_incar(tmp_path)
        assert params["ALGO"] == "Normal"
        assert params["POTIM"] == "0.1"

    def test_brion_error_stage1(self, tmp_path: Path):
        """Stage 1 brion switches to IBRION=2, POTIM=1.0."""
        self._setup_dir(tmp_path)
        apply_correction(tmp_path, "brion_error", 1)
        params = read_incar(tmp_path)
        assert params["IBRION"] == "2"
        assert params["POTIM"] == "1.0"

    def test_brion_error_stage2(self, tmp_path: Path):
        """Stage 2 brion switches to IBRION=1."""
        self._setup_dir(tmp_path)
        apply_correction(tmp_path, "brion_error", 2)
        params = read_incar(tmp_path)
        assert params["IBRION"] == "1"

    def test_brion_error_stage3(self, tmp_path: Path):
        """Stage 3 brion uses damped MD."""
        self._setup_dir(tmp_path)
        apply_correction(tmp_path, "brion_error", 3)
        params = read_incar(tmp_path)
        assert params["IBRION"] == "3"
        assert params["SMASS"] == "3"

    def test_unknown_error_fallback_contcar(self, tmp_path: Path):
        """Unknown error type falls back to CONTCAR->POSCAR copy."""
        self._setup_dir(tmp_path)
        (tmp_path / "CONTCAR").write_text("updated structure\n")
        result = apply_correction(tmp_path, "some_unknown_error", 1)
        assert result is True
        assert (tmp_path / "POSCAR").read_text() == "updated structure\n"

    def test_none_error_fallback(self, tmp_path: Path):
        """None error type falls back to CONTCAR->POSCAR copy."""
        self._setup_dir(tmp_path)
        result = apply_correction(tmp_path, None, 1)
        assert result is True

    def test_no_contcar_returns_false(self, tmp_path: Path):
        """Returns False when no CONTCAR available for fallback."""
        (tmp_path / "INCAR").write_text("ENCUT = 520\n")
        result = apply_correction(tmp_path, "unknown_thing", 1)
        assert result is False

    def test_contcar_copied_on_known_error(self, tmp_path: Path):
        """Known error corrections also restart from CONTCAR."""
        self._setup_dir(tmp_path)
        (tmp_path / "CONTCAR").write_text("relaxed structure\n")
        apply_correction(tmp_path, "frozen_job", 1)
        assert (tmp_path / "POSCAR").read_text() == "relaxed structure\n"

    def test_escalation_stages_differ(self, tmp_path: Path):
        """Repeated calls with increasing attempt produce different INCAR states."""
        self._setup_dir(tmp_path)
        apply_correction(tmp_path, "scf_no_converge", 1)
        stage1 = read_incar(tmp_path)

        self._setup_dir(tmp_path)
        apply_correction(tmp_path, "scf_no_converge", 2)
        stage2 = read_incar(tmp_path)

        assert stage1 != stage2
