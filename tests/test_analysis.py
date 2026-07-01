"""Tests for vasp_sop.defect.analysis — defect post-processing pipeline."""

from pathlib import Path

from vasp_sop.core.config import PipelineConfig


class TestAnalyze:
    """analyze() — defect energetics post-processing."""

    def test_skip_when_summary_exists(self, tmp_path: Path):
        """If defect_energy_summary.json exists, analyze returns immediately."""
        from vasp_sop.defect.analysis import analyze
        (tmp_path / "defect_energy_summary.json").write_text("{}")
        analyze(tmp_path, tmp_path, PipelineConfig(formula="GaN"),
                tmp_path / "u.yaml", tmp_path / "se.yaml", tmp_path / "tv.yaml")

    def test_returns_early_when_outcar_missing(self, tmp_path: Path, monkeypatch):
        """If defect dirs have no OUTCAR, analyze returns without running
        any pydefect commands."""
        (tmp_path / "perfect").mkdir()
        calls = []
        monkeypatch.setattr("vasp_sop.defect.analysis.run_local",
                           lambda *a, **kw: calls.append(a))
        from vasp_sop.defect.analysis import analyze
        analyze(tmp_path, tmp_path, PipelineConfig(formula="GaN"),
                tmp_path / "u.yaml", tmp_path / "se.yaml", tmp_path / "tv.yaml")
        assert len(calls) == 0

    def test_calls_cr_when_outcar_present(self, tmp_path: Path, monkeypatch):
        """With OUTCARs in defect dirs, pydefect_vasp cr runs."""
        (tmp_path / "perfect").mkdir()
        (tmp_path / "perfect" / "OUTCAR").write_text("converged\n")
        defect = tmp_path / "Va_X_0"
        defect.mkdir()
        (defect / "OUTCAR").write_text("converged\n")
        recorded = []
        monkeypatch.setattr("vasp_sop.defect.analysis.run_local",
                           lambda cmd, cwd: recorded.append(cmd))
        # restore_from_cache is imported inside analyze(), so patch the
        # function at the cache module level.
        monkeypatch.setattr("vasp_sop.core.cache.restore_from_cache",
                           lambda p: True)
        from vasp_sop.defect.analysis import analyze
        analyze(tmp_path, tmp_path, PipelineConfig(formula="GaN"),
                tmp_path / "u.yaml", tmp_path / "se.yaml", tmp_path / "tv.yaml")
        assert any("pydefect_vasp cr" in c for c in recorded)
