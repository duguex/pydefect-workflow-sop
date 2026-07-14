"""Tests for VASP parse summary (via vasp-cache)."""

from pathlib import Path

from vasp_cache.parse import summarize_calc as _parse_vasp_dir


class TestParseVaspDir:
    def _write_minimal_outcar(self, d: Path, energy: str = "-5.0") -> None:
        (d / "OUTCAR").write_text(
            f" free  energy    TOTEN  =    {energy} eV\n"
            " General timing and accounting\n"
        )

    def test_regex_fallback_converged(self, tmp_path: Path):
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
        assert result["parsed_by"] in {"regex", "TaskDoc"}

    def test_regex_fallback_unconverged(self, tmp_path: Path):
        src = tmp_path / "calc"
        src.mkdir()
        (src / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -3.14 eV\n"
            "maximum number of electronic steps reached\n"
        )
        result = _parse_vasp_dir(src)
        assert result["converged"] is False
        assert result["total_energy"] == -3.14
