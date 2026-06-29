"""Tests for vasp_sop.core.cache VASP parsing layer."""

from pathlib import Path

import pytest

from vasp_sop.core.cache import _parse_vasp_dir, _build_blob, _parse_and_build


class TestParseVaspDir:
    """Tests for _parse_vasp_dir (TaskDoc → regex fallback)."""

    def _write_minimal_outcar(self, d: Path, energy: str = "-5.0") -> None:
        (d / "OUTCAR").write_text(
            f" free  energy    TOTEN  =    {energy} eV\n"
            " General timing and accounting\n"
        )

    def test_regex_fallback_converged(self, tmp_path: Path):
        """Regex fallback extracts energy and converged flag."""
        src = tmp_path / "calc"
        src.mkdir()
        self._write_minimal_outcar(src)
        (src / "CONTCAR").write_text(
            "Si\n1.0\n5.43 0 0\n0 5.43 0\n0 0 5.43\nSi\n2\nDirect\n0 0 0\n0.25 0.25 0.25\n"
        )
        result = _parse_vasp_dir(src)
        assert result["converged"] is True
        assert result["total_energy"] == -5.0
        assert result["formula_pretty"] == "Si"
        assert result["nsites"] == 2
        assert result["parsed_by"] == "regex"

    def test_regex_fallback_unconverged(self, tmp_path: Path):
        """Unconverged OUTCAR gives converged=False but still extracts energy."""
        src = tmp_path / "calc"
        src.mkdir()
        (src / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -3.14 eV\n"
            "maximum number of electronic steps reached\n"
        )
        result = _parse_vasp_dir(src)
        assert result["converged"] is False
        assert result["total_energy"] == -3.14

    def test_no_outcar(self, tmp_path: Path):
        src = tmp_path / "empty"
        src.mkdir()
        result = _parse_vasp_dir(src)
        assert result["converged"] is False
        assert result["total_energy"] is None

    def test_tags_from_incar(self, tmp_path: Path):
        src = tmp_path / "calc"
        src.mkdir()
        self._write_minimal_outcar(src)
        (src / "INCAR").write_text(
            "SYSTEM = test\nENCUT = 600\nLDAU = True\nISPIN = 2\n"
        )
        result = _parse_vasp_dir(src)
        assert "DFT+U" in result["tags"]
        assert "spin" in result["tags"]
        assert "high-encut" in result["tags"]


class TestBuildBlob:
    """Tests for _build_blob."""

    def test_blob_with_all_files(self, tmp_path: Path):
        src = tmp_path / "calc"
        src.mkdir()
        (src / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -7.77 eV\n"
            " General timing and accounting\n"
        )
        (src / "CONTCAR").write_text(
            "Si\n1.0\n5.43 0 0\n0 5.43 0\n0 0 5.43\nSi\n2\nDirect\n0 0 0\n0.25 0.25 0.25\n"
        )
        (src / "INCAR").write_text("ENCUT = 520\n")
        (src / "KPOINTS").write_text("Auto\n0\nGamma\n1 1 1\n0 0 0\n")

        blob = _build_blob(src)
        assert "outcar_dict" in blob
        assert "structure_dict" in blob
        assert "incar_dict" in blob
        assert "kpoints_dict" in blob

    def test_blob_minimal(self, tmp_path: Path):
        src = tmp_path / "calc"
        src.mkdir()
        (src / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -1.0 eV\n"
            " General timing and accounting\n"
        )
        blob = _build_blob(src)
        assert "outcar_dict" in blob
        assert "structure_dict" not in blob
        assert "incar_dict" not in blob


class TestParseAndBuild:
    """Tests for _parse_and_build (worker function for backfill)."""

    def test_returns_meta_and_blob(self, tmp_path: Path):
        src = tmp_path / "GaN_mp-804"
        src.mkdir()
        (src / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -9.18 eV\n"
            " General timing and accounting\n"
        )
        (src / "CONTCAR").write_text(
            "GaN\n1.0\n3.19 0 0\n0 3.19 0\n0 0 5.19\nGa N\n1 1\nDirect\n0 0 0\n0.333 0.667 0.5\n"
        )
        result = _parse_and_build(src)
        assert "meta" in result
        assert result["meta"]["formula"] == "GaN"
        assert result["meta"]["total_energy"] == -9.18
        assert result["meta"]["converged"] == 1
        assert "blob" in result