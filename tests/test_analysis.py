"""Tests for vasp_sop.defect.analysis — defect post-processing pipeline."""

from pathlib import Path

from vasp_sop.core.config import PipelineConfig


def _cfg() -> PipelineConfig:
    return PipelineConfig(formula="GaN")


def _force_converged(monkeypatch, dirs=None):
    """Treat listed dirs (or all) as ionically converged."""
    from vasp_sop.defect import analysis as an

    def _conv(ds):
        if dirs is None:
            return list(ds)
        want = {Path(d).resolve() for d in dirs}
        return [d for d in ds if d.resolve() in want]

    monkeypatch.setattr(an, "_converged_dirs", _conv)


def test_run_dir_batches_covers_targets_once(tmp_path: Path, monkeypatch):
    """Large pydefect steps split explicit dirs into bounded batches (#0024)."""
    from vasp_sop.defect import pydefect_adapter as _pdad

    dirs = []
    for i in range(45):
        d = tmp_path / f"Va_X_{i}"
        d.mkdir()
        dirs.append(d)
    commands = []
    monkeypatch.setattr(
        _pdad, "run_local", lambda cmd, cwd, **kw: commands.append((cmd, kw)),
    )

    _pdad._run_batches(
        "pydefect dsi -d", dirs, cwd=tmp_path, batch_size=20, timeout=123,
    )

    assert len(commands) == 3
    assert all(kwargs["timeout"] == 123 for _, kwargs in commands)
    names = []
    for cmd, _ in commands:
        names.extend(cmd.split()[3:])
    assert sorted(names) == sorted(d.name for d in dirs)
    assert len(names) == len(set(names))

class TestAnalyze:
    """analyze() — defect energetics post-processing."""

    def test_skip_when_summary_exists_returns_full(self, tmp_path: Path, monkeypatch):
        """Complete summary + full corrections → full without re-running."""
        from vasp_sop.defect.analysis import analyze

        d = tmp_path / "Va_Ga_0"
        d.mkdir()
        (d / "OUTCAR").write_text("x\n")
        (d / "correction.json").write_text("{}\n")
        (tmp_path / "defect_energy_summary.json").write_text("{}\n")
        _force_converged(monkeypatch)
        status = analyze(
            tmp_path, tmp_path, _cfg(),
            tmp_path / "u.yaml", tmp_path / "se.yaml", tmp_path / "tv.yaml",
        )
        assert status == "full"
        assert (tmp_path / "analyze_status.json").is_file()

    def test_existing_summary_demoted_when_partial(self, tmp_path: Path, monkeypatch):
        """Summary with incomplete corrections is demoted to .partial.json."""
        from vasp_sop.defect.analysis import analyze

        good = tmp_path / "Va_Ga_0"
        good.mkdir()
        (good / "OUTCAR").write_text("x\n")
        (good / "correction.json").write_text("{}\n")
        bad = tmp_path / "Va_Ga_-1"
        bad.mkdir()
        (bad / "OUTCAR").write_text("x\n")
        (tmp_path / "defect_energy_summary.json").write_text("{}\n")
        _force_converged(monkeypatch)
        status = analyze(
            tmp_path, tmp_path, _cfg(),
            tmp_path / "u.yaml", tmp_path / "se.yaml", tmp_path / "tv.yaml",
        )
        assert status == "partial"
        assert not (tmp_path / "defect_energy_summary.json").is_file()
        assert (tmp_path / "defect_energy_summary.partial.json").is_file()

    def test_returns_failed_when_outcar_missing(self, tmp_path: Path, monkeypatch):
        """If defect dirs have no OUTCAR, analyze returns failed."""
        (tmp_path / "perfect").mkdir()
        calls = []
        monkeypatch.setattr(
            "vasp_sop.defect.pydefect_adapter.run_local",
            lambda *a, **kw: calls.append(a),
        )
        from vasp_sop.defect.analysis import analyze

        status = analyze(
            tmp_path, tmp_path, _cfg(),
            tmp_path / "u.yaml", tmp_path / "se.yaml", tmp_path / "tv.yaml",
        )
        assert status == "failed"
        assert len(calls) == 0

    def test_calls_cr_when_vasprun_present(self, tmp_path: Path, monkeypatch):
        """Converged dirs with vasprun.xml trigger pydefect_vasp cr (#0010)."""
        (tmp_path / "perfect").mkdir()
        (tmp_path / "perfect" / "OUTCAR").write_text("converged\n")
        (tmp_path / "perfect" / "vasprun.xml").write_text("<xml/>\n")
        defect = tmp_path / "Va_X_0"
        defect.mkdir()
        (defect / "OUTCAR").write_text("converged\n")
        (defect / "vasprun.xml").write_text("<xml/>\n")
        for name in ("u.yaml", "se.yaml", "tv.yaml"):
            (tmp_path / name).write_text("x: 1\n")
        recorded = []
        monkeypatch.setattr(
            "vasp_sop.defect.pydefect_adapter.run_local",
            lambda cmd, cwd=None, **kw: recorded.append(cmd),
        )
        _force_converged(monkeypatch)
        from vasp_sop.defect.analysis import analyze

        status = analyze(
            tmp_path, tmp_path, _cfg(),
            tmp_path / "u.yaml", tmp_path / "se.yaml", tmp_path / "tv.yaml",
        )
        assert any("pydefect_vasp cr" in c for c in recorded)
        assert status in ("failed", "partial")

    def test_missing_vasprun_listed_in_status(self, tmp_path: Path, monkeypatch):
        """OUTCAR-only converged dir appears in missing_vasprun (#0010/#0013)."""
        import json

        perfect = tmp_path / "perfect"
        perfect.mkdir()
        (perfect / "OUTCAR").write_text("ok\n")
        (perfect / "calc_results.json").write_text("{}\n")
        (perfect / "perfect_band_edge_state.json").write_text("{}\n")
        d = tmp_path / "Va_Ga_0"
        d.mkdir()
        (d / "OUTCAR").write_text("ok\n")  # no vasprun, no calc_results
        for name in ("u.yaml", "se.yaml", "tv.yaml"):
            (tmp_path / name).write_text("x: 1\n")
        monkeypatch.setattr(
            "vasp_sop.defect.pydefect_adapter.run_local", lambda *a, **kw: None,
        )
        _force_converged(monkeypatch)
        from vasp_sop.defect.analysis import analyze

        analyze(
            tmp_path, tmp_path, _cfg(),
            tmp_path / "u.yaml", tmp_path / "se.yaml", tmp_path / "tv.yaml",
        )
        st = json.loads((tmp_path / "analyze_status.json").read_text())
        assert "n_missing_vasprun" in st
        assert "Va_Ga_0" in st["missing_vasprun"]
        assert st["n_dei"] == 0

    def test_missing_outcar_does_not_block_ready_partial(
        self, tmp_path: Path, monkeypatch,
    ):
        """One dir without OUTCAR still allows partial when another is ready (#0011)."""
        perfect = tmp_path / "perfect"
        perfect.mkdir()
        (perfect / "OUTCAR").write_text("ok\n")
        (perfect / "calc_results.json").write_text("{}\n")
        (perfect / "perfect_band_edge_state.json").write_text("{}\n")
        good = tmp_path / "Va_Ga_0"
        good.mkdir()
        (good / "OUTCAR").write_text("ok\n")
        (good / "calc_results.json").write_text("{}\n")
        bad = tmp_path / "Va_Ga_-1"
        bad.mkdir()  # no OUTCAR
        for name in ("u.yaml", "se.yaml", "tv.yaml"):
            (tmp_path / name).write_text("x: 1\n")

        def fake_run(cmd: str, cwd=None, **kw):
            if "efnv" in cmd:
                (good / "correction.json").write_text("{}\n")
                (good / "defect_energy_info.json").write_text("{}\n")

        monkeypatch.setattr("vasp_sop.defect.pydefect_adapter.run_local", fake_run)
        _force_converged(monkeypatch)
        from vasp_sop.defect.analysis import analyze

        status = analyze(
            tmp_path, tmp_path, _cfg(),
            tmp_path / "u.yaml", tmp_path / "se.yaml", tmp_path / "tv.yaml",
        )
        assert status == "partial"
        assert (good / "correction.json").is_file()


    def test_partial_after_pipeline_no_final_summary(
        self, tmp_path: Path, monkeypatch,
    ):
        """Only one of two defects gets correction → partial, no final summary."""
        perfect = tmp_path / "perfect"
        perfect.mkdir()
        (perfect / "OUTCAR").write_text("ok\n")
        (perfect / "calc_results.json").write_text("{}\n")
        (perfect / "perfect_band_edge_state.json").write_text("{}\n")

        good = tmp_path / "Va_Ga_0"
        good.mkdir()
        (good / "OUTCAR").write_text("ok\n")
        (good / "calc_results.json").write_text("{}\n")

        bad = tmp_path / "Va_Ga_-1"
        bad.mkdir()
        (bad / "OUTCAR").write_text("ok\n")
        (bad / "calc_results.json").write_text("{}\n")

        uy = tmp_path / "unitcell.yaml"
        se = tmp_path / "standard_energies.yaml"
        tv = tmp_path / "target_vertices.yaml"
        for p in (uy, se, tv):
            p.write_text("x: 1\n")

        def fake_run(cmd: str, cwd=None, **kw):
            if "efnv" in cmd:
                (good / "correction.json").write_text("{}\n")
                (good / "defect_energy_info.json").write_text("{}\n")
                return
            if " des " in f" {cmd} " or cmd.startswith("pydefect des"):
                (tmp_path / "defect_energy_summary.json").write_text("{}\n")
                return
            if "dsi" in cmd:
                (good / "defect_structure_info.json").write_text("{}\n")
                (bad / "defect_structure_info.json").write_text("{}\n")
                return

        monkeypatch.setattr("vasp_sop.defect.pydefect_adapter.run_local", fake_run)
        _force_converged(monkeypatch)

        from vasp_sop.defect.analysis import analyze

        status = analyze(tmp_path, tmp_path, _cfg(), uy, se, tv)
        assert status == "partial"
        assert not (tmp_path / "defect_energy_summary.json").is_file()
        st = (tmp_path / "analyze_status.json").read_text()
        assert "partial" in st
        assert "Va_Ga_-1" in st

    def test_efnv_skips_unconverged_dirs(self, tmp_path: Path, monkeypatch):
        """efnv is only invoked for ionically converged defect dirs."""
        perfect = tmp_path / "perfect"
        perfect.mkdir()
        (perfect / "OUTCAR").write_text("ok\n")
        (perfect / "calc_results.json").write_text("{}\n")
        (perfect / "perfect_band_edge_state.json").write_text("{}\n")

        good = tmp_path / "Va_Ga_0"
        good.mkdir()
        (good / "OUTCAR").write_text("ok\n")
        (good / "calc_results.json").write_text("{}\n")
        bad = tmp_path / "Va_Ga_-1"
        bad.mkdir()
        (bad / "OUTCAR").write_text("ok\n")
        (bad / "calc_results.json").write_text("{}\n")

        uy = tmp_path / "u.yaml"
        se = tmp_path / "se.yaml"
        tv = tmp_path / "tv.yaml"
        for p in (uy, se, tv):
            p.write_text("x: 1\n")

        recorded: list[str] = []

        def fake_run(cmd: str, cwd=None, **kw):
            recorded.append(cmd)
            if "efnv" in cmd:
                (good / "correction.json").write_text("{}\n")

        monkeypatch.setattr("vasp_sop.defect.pydefect_adapter.run_local", fake_run)
        _force_converged(monkeypatch, dirs=[good])

        from vasp_sop.defect.analysis import analyze

        analyze(tmp_path, tmp_path, _cfg(), uy, se, tv)
        efnv = [c for c in recorded if "efnv" in c]
        assert efnv
        assert "Va_Ga_0" in efnv[0]
        assert "Va_Ga_-1" not in efnv[0]

    def test_path_with_parens_is_shell_quoted(
        self, tmp_path: Path, monkeypatch,
    ):
        """Paths containing '(' must be quoted in CLI strings (#0009)."""
        import shlex

        root = tmp_path / "Sn(SeO3)2"
        defect_root = root / "defect"
        defect_root.mkdir(parents=True)
        perfect = defect_root / "perfect"
        perfect.mkdir()
        (perfect / "OUTCAR").write_text("ok\n")
        (perfect / "calc_results.json").write_text("{}\n")
        (perfect / "perfect_band_edge_state.json").write_text("{}\n")

        d = defect_root / "Va_Sn_0"
        d.mkdir()
        (d / "OUTCAR").write_text("ok\n")
        (d / "calc_results.json").write_text("{}\n")
        (d / "correction.json").write_text("{}\n")

        uy = root / "unitcell" / "unitcell.yaml"
        uy.parent.mkdir(parents=True)
        uy.write_text("x: 1\n")
        se = root / "standard_energies.yaml"
        tv = root / "target_vertices.yaml"
        se.write_text("x: 1\n")
        tv.write_text("target: SnSeO3\nA:\n  chem_pot: 0\n")

        recorded: list[str] = []

        def fake_run(cmd: str, cwd=None, **kw):
            recorded.append(cmd)
            if "efnv" in cmd:
                return
            if " des " in f" {cmd} " or cmd.startswith("pydefect des"):
                (defect_root / "defect_energy_summary.json").write_text("{}\n")
                (d / "defect_energy_info.json").write_text("{}\n")
                return
            if "dsi" in cmd:
                (d / "defect_structure_info.json").write_text("{}\n")
                return

        monkeypatch.setattr("vasp_sop.defect.pydefect_adapter.run_local", fake_run)
        _force_converged(monkeypatch)

        from vasp_sop.defect.analysis import analyze

        analyze(defect_root, root, PipelineConfig(formula="SnSeO3"), uy, se, tv)

        efnv_cmds = [c for c in recorded if "efnv" in c]
        assert efnv_cmds, "efnv should run"
        assert "Sn(SeO3)2" in efnv_cmds[0]
        assert shlex.quote(str(uy)) in efnv_cmds[0]
        assert shlex.quote(str(perfect / "calc_results.json")) in efnv_cmds[0]


class TestClassifyAnalyzeStatus:
    def test_full(self, tmp_path: Path, monkeypatch):
        from vasp_sop.defect import analysis as an

        d = tmp_path / "Va_Ga_0"
        d.mkdir()
        (d / "OUTCAR").write_text("x\n")
        (d / "correction.json").write_text("{}\n")
        (tmp_path / "defect_energy_summary.json").write_text("{}\n")
        monkeypatch.setattr(an, "_converged_dirs", lambda dirs: list(dirs))
        assert an.classify_analyze_status(tmp_path) == "full"

    def test_partial_when_unconverged_present(self, tmp_path: Path, monkeypatch):
        from vasp_sop.defect import analysis as an

        a = tmp_path / "Va_Ga_0"
        a.mkdir()
        (a / "OUTCAR").write_text("x\n")
        (a / "correction.json").write_text("{}\n")
        b = tmp_path / "Va_N_0"
        b.mkdir()
        (b / "OUTCAR").write_text("x\n")
        (tmp_path / "defect_energy_summary.json").write_text("{}\n")
        monkeypatch.setattr(an, "_converged_dirs", lambda dirs: [a])
        assert an.classify_analyze_status(tmp_path) == "partial"

    def test_partial(self, tmp_path: Path, monkeypatch):
        from vasp_sop.defect import analysis as an

        a = tmp_path / "Va_Ga_0"
        a.mkdir()
        (a / "OUTCAR").write_text("x\n")
        (a / "correction.json").write_text("{}\n")
        b = tmp_path / "Va_N_0"
        b.mkdir()
        (b / "OUTCAR").write_text("x\n")
        monkeypatch.setattr(an, "_converged_dirs", lambda dirs: list(dirs))
        assert an.classify_analyze_status(tmp_path) == "partial"


    def test_status_json_has_qa_keys(self, tmp_path: Path, monkeypatch):
        import json
        from vasp_sop.defect import analysis as an

        d = tmp_path / "Va_Ga_0"
        d.mkdir()
        (d / "OUTCAR").write_text("x\n")
        (d / "correction.json").write_text("{}\n")
        monkeypatch.setattr(an, "_converged_dirs", lambda dirs: list(dirs))
        an.reconcile_defect_summaries(tmp_path)
        st = json.loads((tmp_path / "analyze_status.json").read_text())
        for key in (
            "n_eligible", "n_converged", "n_corrected", "n_dei",
            "n_unconverged", "n_missing_vasprun", "missing_vasprun",
            "missing_calc_results", "missing_correction",
        ):
            assert key in st, key

    def test_failed_empty(self, tmp_path: Path):
        from vasp_sop.defect.analysis import classify_analyze_status

        assert classify_analyze_status(tmp_path) == "failed"
