"""Tests for vasp_sop.cli.main -- batch run, advance system, dry-run behavior.

These tests verify that the batch pipeline correctly handles dry-run vs real
submission, and that cached submission logic isn't silently skipped.
"""

from pathlib import Path
import yaml
import pytest


@pytest.fixture
def competing_system(tmp_path: Path) -> Path:
    """Create a minimal system in COMPETING phase."""
    formula = "NaCl"
    mpid = "12345"
    root = tmp_path / "system"
    root.mkdir(parents=True)
    cpd = root / "cpd"

    plan = {
        "project": {"formula": formula, "dopant_elements": [],
                     "poscar_src": f"MP mp-{mpid}"},
        "parameters": {"functional": "pbesol"},
        "supercell": {"tool": "doped", "min_distance": 10.0},
    }
    (root / "plan.yaml").write_text(yaml.dump(plan))

    target_dir = cpd / f"{formula}_mp-{mpid}"
    target_dir.mkdir(parents=True)
    _write_poscar(target_dir, 4)
    _write_incar(target_dir)
    _write_potcar(target_dir)
    _write_kpoints(target_dir)
    _write_converged_outcar(target_dir)

    comp_dir = cpd / "Other_mp-99999"
    comp_dir.mkdir(parents=True)
    _write_poscar(comp_dir, 2)
    _write_incar(comp_dir)
    _write_potcar(comp_dir)
    _write_kpoints(comp_dir)
    _write_truncated_outcar(comp_dir)

    return root


def _write_poscar(d: Path, n_atoms: int) -> None:
    """Write a minimal valid POSCAR."""
    lines = [
        "Test POSCAR",
        "1.0",
        "10.0 0.0 0.0",
        "0.0 10.0 0.0",
        "0.0 0.0 10.0",
        "X",
        str(n_atoms),
        "Direct",
    ]
    for i in range(n_atoms):
        lines.append(f"{i/n_atoms:.6f} {i/n_atoms:.6f} {i/n_atoms:.6f}")
    (d / "POSCAR").write_text("\n".join(lines) + "\n")


def _write_incar(d: Path) -> None:
    (d / "INCAR").write_text("SYSTEM = test\n")


def _write_potcar(d: Path) -> None:
    (d / "POTCAR").write_text("dummy POTCAR\n")


def _write_kpoints(d: Path) -> None:
    text = "k-points\n0\nGamma\n1 1 1\n0 0 0\n"
    (d / "KPOINTS").write_text(text)


def _write_converged_outcar(d: Path) -> None:
    text = (" some header\n"
            "  reached required accuracy - convergence\n"
            "  reached required accuracy - convergence\n")
    (d / "OUTCAR").write_text(text)


def _write_truncated_outcar(d: Path) -> None:
    (d / "OUTCAR").write_text("some header\n  reached required\n")


def _make_system_dict(root: Path) -> dict:
    """Build the system dict that _advance_one_system expects."""
    from vasp_sop.core.config import PipelineConfig
    plan = yaml.safe_load((root / "plan.yaml").read_text())
    config = PipelineConfig.from_plan(plan, root=root)
    src = config.poscar_src
    mpid = src.split("mp-", 1)[1] if src.startswith("MP mp-") else None
    return {
        "name": config.formula or root.name,
        "root": root,
        "config": config,
        "formula": config.formula,
        "mpid": mpid,
    }


# Tests


class TestAdvanceOneSystem:
    """_advance_one_system -- dry-run vs real submission."""

    @pytest.fixture(autouse=True)
    def _patch_heavy(self, monkeypatch, tmp_path: Path):
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")
        monkeypatch.setattr("vasp_sop.core.cache.cache_lookup",
                            lambda p: {"total_energy": 0.0} if "NaCl_mp-12345" in str(p) else None)
        monkeypatch.setattr("vasp_sop.defect.builder.build_all", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.vasp.io.check_converged",
                            lambda p: "NaCl_mp-12345" in str(p))
        monkeypatch.setattr("vasp_sop.defect.cpd.compute_chemical_potentials",
                            lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.cpd._get_target_composition",
                            lambda *a: {})

    def test_dry_run_does_not_submit(self, competing_system, monkeypatch):
        calls = []
        monkeypatch.setattr("vasp_sop.core.jobs.submit_vasp",
                            lambda p: (calls.append(p) or
                                       type("J", (), {"task_name": "t"})()))
        from vasp_sop.cli.main import _advance_one_system
        s = _make_system_dict(competing_system)
        _advance_one_system(s, dry_run=True)
        assert len(calls) == 0

    def test_non_dry_submits_competing(self, competing_system, monkeypatch):
        calls = []
        monkeypatch.setattr("vasp_sop.core.jobs.submit_vasp",
                            lambda p: (calls.append(p) or
                                       type("J", (), {"task_name": "t"})()))
        from vasp_sop.cli.main import _advance_one_system
        s = _make_system_dict(competing_system)
        _advance_one_system(s, dry_run=False)
        assert len(calls) >= 1
        comp_dir = str(competing_system / "cpd" / "Other_mp-99999")
        assert comp_dir in {str(p) for p in calls}


class TestCachePutGet:
    _cr: Path | None = None

    @pytest.fixture(autouse=True)
    def _isolate_cache(self, tmp_path: Path) -> None:
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")

    def test_roundtrip(self, tmp_path: Path):
        from vasp_sop.core.cache import vasp_results_put, vasp_results_get
        src = tmp_path / "src"
        src.mkdir()
        (src / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -10.0 eV\n"
            " General timing and accounting\n"
        )
        (src / "CONTCAR").write_text(
            "H\n1.0\n10 0 0\n0 10 0\n0 0 10\nH\n1\nDirect\n0 0 0\n"
        )
        vasp_results_put(src, "TestMe", "42")
        cached = vasp_results_get("TestMe", "42")
        assert cached is not None
        assert cached["total_energy"] == -10.0
        assert cached["converged"] == 1

    def test_get_missing_returns_none(self):
        from vasp_sop.core.cache import vasp_results_get
        assert vasp_results_get("Never", "cached") is None

    def test_put_does_not_delete_others(self, tmp_path: Path):
        from vasp_sop.core.cache import vasp_results_put, vasp_results_get
        src1 = tmp_path / "src1"
        src1.mkdir()
        (src1 / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -10.0 eV\n"
            " General timing and accounting\n"
        )
        vasp_results_put(src1, "First", "1")
        src2 = tmp_path / "src2"
        src2.mkdir()
        (src2 / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -10.0 eV\n"
            " General timing and accounting\n"
        )
        vasp_results_put(src2, "Second", "2")
        assert vasp_results_get("First", "1") is not None
        assert vasp_results_get("Second", "2") is not None


class TestCrispActiveDirs:
    """Issue #17: _crisp_active_dirs must skip subprocess when skip=True."""

    def test_dry_run_skips_crisp_subprocess(self, monkeypatch):
        """When skip=True (dry-run), the function returns set() without
        ever spawning subprocess.run."""
        import subprocess
        calls = []

        def fake_run(*a, **kw):
            calls.append((a, kw))
            raise AssertionError(
                "subprocess.run should not be called when skip=True"
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        from vasp_sop.cli.main import _crisp_active_dirs
        result = _crisp_active_dirs(skip=True)
        assert result == set()
        assert calls == []

    def test_real_run_queries_crisp(self, monkeypatch):
        """When skip=False, the function calls `crisp jobs` and parses
        the JSON response into a set of local_dir paths."""
        import subprocess
        import json as _json

        fake_payload = _json.dumps({
            "jobs": [
                {"status": "running", "local_dir": "/tmp/a"},
                {"status": "submitted", "local_dir": "/tmp/b"},
                {"status": "completed", "local_dir": "/tmp/c"},  # not alive
                {"status": "running", "local_dir": ""},           # no dir
            ]
        })
        calls = []

        def fake_run(*a, **kw):
            calls.append((a, kw))
            r = type("R", (), {})()
            r.stdout = fake_payload
            r.returncode = 0
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        from vasp_sop.cli.main import _crisp_active_dirs
        result = _crisp_active_dirs(skip=False)
        assert result == {"/tmp/a", "/tmp/b"}
        assert len(calls) == 1


class TestBatchStatus:
    """batch status shows phase column + Running/Done/Total."""

    def _make_system(self, tmp_path: Path) -> Path:
        d = tmp_path / "GaN"
        d.mkdir()
        plan = {
            "project": {"formula": "GaN", "dopant_elements": [],
                        "poscar_src": "MP mp-804"},
            "parameters": {"functional": "pbesol"},
            "supercell": {"tool": "doped", "min_distance": 10.0},
        }
        (d / "plan.yaml").write_text(yaml.dump(plan))
        return d

    def test_batch_status_header(self, tmp_path, capsys):
        self._make_system(tmp_path)
        from vasp_sop.cli.main import _batch_status
        _batch_status(tmp_path)
        captured = capsys.readouterr().out
        assert "System" in captured
        assert "Phase" in captured
        assert "Run" in captured
        assert "Done" in captured

    def test_batch_status_no_systems(self, tmp_path, capsys):
        from vasp_sop.cli.main import _batch_status
        _batch_status(tmp_path)
        captured = capsys.readouterr().out
        assert "No vasp-sop systems found" in captured

class TestAdvanceDryRunPostprocess:
    """Issue #20: dry-run in UC_DF phase must preview post-processing
    without mutating state."""

    @pytest.fixture(autouse=True)
    def _patch_heavy(self, monkeypatch, tmp_path: Path):
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")
        monkeypatch.setattr("vasp_sop.core.cache.cache_lookup",
                            lambda p: {"total_energy": 0.0} if "NaCl_mp-12345" in str(p) else None)
        monkeypatch.setattr("vasp_sop.defect.builder.build_all", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.vasp.io.check_converged",
                            lambda p: "NaCl_mp-12345" in str(p))
        monkeypatch.setattr("vasp_sop.defect.cpd.compute_chemical_potentials",
                            lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.cpd._get_target_composition",
                            lambda *a: {})

    def _make_uc_df_system(self, tmp_path: Path, *, with_artifacts: bool) -> Path:
        """Build a system whose _phase() returns 'UC_DF' (target converged,
        CPD done, UC inputs present, defect tree started)."""
        formula = "NaCl"
        mpid = "12345"
        root = tmp_path / f"system_{with_artifacts}"
        root.mkdir(parents=True)
        cpd = root / "cpd"
        cpd.mkdir()
        uc = root / "unitcell"
        uc.mkdir()
        df = root / "defect"
        df.mkdir()

        plan = {
            "project": {"formula": formula, "dopant_elements": [],
                         "poscar_src": f"MP mp-{mpid}"},
            "parameters": {"functional": "pbesol"},
            "supercell": {"tool": "doped", "min_distance": 10.0},
        }
        (root / "plan.yaml").write_text(yaml.dump(plan))

        target_dir = cpd / f"{formula}_mp-{mpid}"
        target_dir.mkdir(parents=True)
        for fname in ("POSCAR", "INCAR", "POTCAR", "KPOINTS", "OUTCAR"):
            (target_dir / fname).write_text("dummy\n")

        if with_artifacts:
            (cpd / "target_vertices.yaml").write_text("dummy: 1\n")
            (cpd / "standard_energies.yaml").write_text("dummy: 1\n")
            (uc / "unitcell.yaml").write_text("dummy: 1\n")
            for t in ("band", "dos", "dielectric"):
                tdir = uc / t
                tdir.mkdir()
                (tdir / "INCAR").write_text("dummy\n")
            defect_dir = df / "Va_Na_0"
            defect_dir.mkdir()
            (defect_dir / "CONTCAR").write_text("dummy\n")

        return root

    def test_dry_run_logs_would_postprocess(self, tmp_path, monkeypatch, capsys):
        """With all artifacts present, dry-run logs the 'would post-process'
        message and does NOT call _analyze_defects."""
        root = self._make_uc_df_system(tmp_path, with_artifacts=True)
        analyze_calls = []
        monkeypatch.setattr(
            "vasp_sop.defect.analysis.analyze",
            lambda *a, **kw: analyze_calls.append((a, kw))
            or (_ for _ in ()).throw(
                AssertionError("analyze must not run in dry-run"),
            ),
        )

        from vasp_sop.cli.main import _advance_one_system
        s = _make_system_dict(root)
        _advance_one_system(s, dry_run=True)
        captured = capsys.readouterr().out
        assert "would post-process" in captured
        assert analyze_calls == []

    def test_dry_run_logs_would_skip_postprocess(self, tmp_path, monkeypatch, capsys):
        """System in UC_DF phase but missing post-processing artifacts:
        dry-run logs the 'post-process blocked' message naming the missing
        files."""
        # Start from a UC_DF system (artifacts present) then strip them.
        root = self._make_uc_df_system(tmp_path, with_artifacts=True)
        # Remove the artifacts we want the test to flag as missing.
        (root / "cpd" / "standard_energies.yaml").unlink()
        (root / "unitcell" / "unitcell.yaml").unlink()
        # defect CONTCAR is removed by deleting the defect dir.
        import shutil as _sh
        _sh.rmtree(root / "defect" / "Va_Na_0")

        from vasp_sop.cli.main import _advance_one_system
        s = _make_system_dict(root)
        _advance_one_system(s, dry_run=True)
        captured = capsys.readouterr().out
        assert "post-process blocked" in captured
        # The names of the missing files should be listed in the message.
        assert "unitcell.yaml" in captured
        assert "standard_energies.yaml" in captured
        assert "CONTCAR" in captured


class TestBatchNoDuplicateSubmission:
    """Issue #50: verify each phase dir is submitted at most once
    across consecutive poll cycles (no re-submission leak)."""

    @pytest.fixture(autouse=True)
    def _patch_common(self, monkeypatch, tmp_path: Path):
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")
        monkeypatch.setattr("vasp_sop.core.cache.cache_lookup",
                           lambda p: {"total_energy": 0.0}
                           if "NaCl_mp-12345" in str(p) else None)
        monkeypatch.setattr("vasp_sop.defect.builder.build_all",
                           lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.builder._generate_vasp_inputs",
                           lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.vasp.io.check_converged",
                           lambda p: "NaCl_mp-12345" in str(p))
        monkeypatch.setattr("vasp_sop.vasp.io.prepare_inputs",
                           lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.cpd.compute_chemical_potentials",
                           lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.cpd._get_target_composition",
                           lambda *a: {})
        monkeypatch.setattr("vasp_sop.defect.analysis.analyze",
                           lambda *a, **kw: None)
    def test_competing_not_resubmitted(self, competing_system, monkeypatch):
        """Competing dir submitted once across two cycles.

        Second cycle: is_submitted() returns True → skip.
        """
        calls = []
        monkeypatch.setattr("vasp_sop.core.jobs.submit_vasp",
                           lambda p: (calls.append(str(p)) or
                                      type("J", (), {"task_name": "t"})()))
        from vasp_sop.cli.main import _advance_one_system
        s = _make_system_dict(competing_system)

        _advance_one_system(s, dry_run=False)  # cycle 1
        cycle1_count = len(calls)
        assert cycle1_count >= 1, "first cycle should submit something"

        _advance_one_system(s, dry_run=False)  # cycle 2
        assert len(calls) == cycle1_count, \
            "second cycle must not re-submit (is_submitted guard)"

        comp_dir = str((competing_system / "cpd" / "Other_mp-99999").resolve())
        assert calls.count(comp_dir) == 1, \
            f"competing dir submitted {calls.count(comp_dir)} times, expected 1"

    def _make_ucdf_system(self, tmp_path: Path) -> Path:
        """Build a minimal system in UC_DF phase (target+cached, CPD done,
        UC inputs present, defect tree exists)."""
        formula = "NaCl"
        mpid = "12345"
        root = tmp_path / "ucdf_system"
        root.mkdir(parents=True)

        plan = {
            "project": {"formula": formula, "dopant_elements": [],
                        "poscar_src": f"MP mp-{mpid}"},
            "parameters": {"functional": "pbesol"},
            "supercell": {"tool": "doped", "min_distance": 10.0},
        }
        (root / "plan.yaml").write_text(yaml.dump(plan))

        # CPD done: target converged + target_vertices present
        cpd = root / "cpd"
        cpd.mkdir()
        target_dir = cpd / f"{formula}_mp-{mpid}"
        target_dir.mkdir()
        (target_dir / "OUTCAR").write_text("converged\n")
        (cpd / "target_vertices.yaml").write_text("tv: 1\n")
        (cpd / "standard_energies.yaml").write_text("se: 1\n")

        # Defect tree
        df = root / "defect"
        df.mkdir()
        perfect = df / "perfect"
        perfect.mkdir()
        for d in (perfect,):
            _write_incar(d)
            _write_kpoints(d)
            _write_potcar(d)
            _write_poscar(d, 2)
        defect_dir = df / "Va_Na_0"
        defect_dir.mkdir()
        _write_incar(defect_dir)
        _write_kpoints(defect_dir)
        _write_potcar(defect_dir)
        _write_poscar(defect_dir, 2)

        # UC inputs present
        uc = root / "unitcell"
        uc.mkdir()
        for t in ("band", "dos", "dielectric"):
            td = uc / t
            td.mkdir()
            _write_incar(td)
            _write_kpoints(td)

        return root

    def test_ucdf_not_resubmitted(self, tmp_path, monkeypatch):
        """UC and defect dirs submitted once across two cycles."""
        calls = []
        monkeypatch.setattr("vasp_sop.core.jobs.submit_vasp",
                           lambda p: (calls.append(str(p)) or
                                      type("J", (), {"task_name": "t"})()))
        from vasp_sop.cli.main import _advance_one_system
        root = self._make_ucdf_system(tmp_path)
        s = _make_system_dict(root)

        _advance_one_system(s, dry_run=False)  # cycle 1
        cycle1_count = len(calls)
        assert cycle1_count >= 1, "first cycle should submit UC + defect jobs"

        _advance_one_system(s, dry_run=False)  # cycle 2
        assert len(calls) == cycle1_count, \
            "second cycle must not re-submit (is_submitted guard)"

        uc_band = str((root / "unitcell" / "band").resolve())
        assert calls.count(uc_band) == 1, \
            f"uc-band submitted {calls.count(uc_band)} times, expected 1"
        defect_dir = str((root / "defect" / "Va_Na_0").resolve())
        assert calls.count(defect_dir) == 1, \
            f"defect dir submitted {calls.count(defect_dir)} times, expected 1"


class TestCachePut:
    """Tests for cache put CLI command."""

    @pytest.fixture(autouse=True)
    def _isolate_cache(self, tmp_path: Path) -> None:
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")

    def test_cache_put_auto_detect(self, tmp_path, monkeypatch, capsys):
        """cache put with auto-detected formula/task_id."""
        d = tmp_path / "GaN_mp-804"
        d.mkdir()
        (d / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -12.0 eV\n"
            " General timing and accounting\n"
        )
        (d / "CONTCAR").write_text(
            "GaN\n1.0\n3.19 0 0\n0 3.19 0\n0 0 5.19\nGa N\n1 1\nDirect\n"
            "0 0 0\n0.333 0.667 0.5\n"
        )
        from vasp_sop.cli.main import _handle_cache
        import argparse
        args = argparse.Namespace(cache_action="put", path=d,
                                   formula=None, task_name=None, recursive=False)
        _handle_cache(args)
        captured = capsys.readouterr().out
        assert "converged" in captured
        from vasp_sop.core.cache import vasp_results_get, _detect_calc_info
        _, ch, _ = _detect_calc_info(d)
        assert vasp_results_get("GaN", ch) is not None


    def test_cache_put_explicit_formula_and_task_id(self, tmp_path, monkeypatch, capsys):
        """cache put with explicit --formula and --task-id."""
        d = tmp_path / "some_dir"
        d.mkdir()
        (d / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -10.0 eV\n"
            " General timing and accounting\n"
        )
        (d / "CONTCAR").write_text(
            "H\n1.0\n10 0 0\n0 10 0\n0 0 10\nH\n1\nDirect\n0 0 0\n"
        )
        from vasp_sop.cli.main import _handle_cache
        import argparse
        args = argparse.Namespace(cache_action="put", path=d,
                                   formula="GaN", task_name="999", recursive=False)
        _handle_cache(args)
        from vasp_sop.core.cache import vasp_results_get, _detect_calc_info
        _, ch, _ = _detect_calc_info(d)
        assert vasp_results_get("GaN", ch) is not None

    def test_cache_put_recursive(self, tmp_path, monkeypatch, capsys):
        """cache put -r finds and caches all OUTCARs."""
        dirs = ["sys_A/cpd/GaN_mp-804", "sys_A/cpd/Other_mp-101",
                "sys_B/defect/Va_Na_0", "sys_B/unitcell/band"]
        for rel in dirs:
            p = tmp_path / rel
            p.mkdir(parents=True)
            (p / "OUTCAR").write_text(
                " free  energy    TOTEN  =    -5.0 eV\n"
                " General timing and accounting\n"
            )
            if "defect" in rel or "unitcell" in rel:
                (p / "POSCAR").write_text(
                    "NaCl\n1.0\n5.64 0 0\n0 5.64 0\n0 0 5.64\nNa Cl\n1 1\nDirect\n"
                    "0 0 0\n0.5 0.5 0.5\n"
                )
            else:
                (p / "CONTCAR").write_text(
                    "GaN\n1.0\n3.19 0 0\n0 3.19 0\n0 0 5.19\nGa N\n1 1\nDirect\n"
                    "0 0 0\n0.333 0.667 0.5\n"
                )
        from vasp_sop.cli.main import _handle_cache
        import argparse
        args = argparse.Namespace(cache_action="put", path=tmp_path,
                                   formula=None, task_name=None, recursive=True)
        _handle_cache(args)
        captured = capsys.readouterr().out
        assert "Cached 4 directories" in captured
        from vasp_sop.core.cache import vasp_results_get, _detect_calc_info
        _, ch1, _ = _detect_calc_info(tmp_path / "sys_A/cpd/GaN_mp-804")
        _, ch2, _ = _detect_calc_info(tmp_path / "sys_A/cpd/Other_mp-101")
        assert vasp_results_get("GaN", ch1) is not None
        assert vasp_results_get("Other", ch2) is not None


class TestFullPipelineWalkthrough:
    """Drive a system through all 5 phases: TARGET → COMPETING → CPD_POST → UC_DF → DONE.

    Each phase transition is verified by checking _phase() output and
    asserting _advance_one_system produces the expected side effects
    (submit_vasp calls, file creation, etc.).
    """

    def _write_converged_outcar(self, d: Path) -> None:
        """Write an OUTCAR that satisfies check_converged (max-force < 0.03)."""
        text = (
            " General timing and accounting\n"
            "   100.00% CPU utilisation\n"
            " TOTAL-FORCE (eV/Angst)\n"
            " ---\n"
            " 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000\n"
        )
        (d / "OUTCAR").write_text(text)

    def _write_unconverged_outcar(self, d: Path) -> None:
        """Write an OUTCAR that fails check_converged (no timing marker)."""
        (d / "OUTCAR").write_text("some header\n  reached required\n")

    @pytest.fixture(autouse=True)
    def _patch_common(self, monkeypatch, tmp_path: Path):
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")
        monkeypatch.setattr("vasp_sop.defect.builder.build_all", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.builder._generate_vasp_inputs", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.vasp.io.prepare_inputs", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.cpd.compute_chemical_potentials", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.cpd._get_target_composition", lambda *a: {})
        monkeypatch.setattr("vasp_sop.defect.unitcell._prepare_all_inputs", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.unitcell.build_unitcell_yaml", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.analysis.analyze", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.core.jobs.move_crisp_outputs", lambda *a, **kw: None)

    def _make_system(self, tmp_path: Path, formula: str = "GaN", mpid: str = "804") -> Path:
        """Create a minimal system with plan.yaml and target dir (no OUTCAR)."""
        root = tmp_path / formula
        root.mkdir()
        plan = {
            "project": {"formula": formula, "dopant_elements": [],
                        "poscar_src": f"MP mp-{mpid}"},
            "parameters": {"functional": "pbesol"},
            "supercell": {"tool": "doped", "min_distance": 10.0},
        }
        (root / "plan.yaml").write_text(yaml.dump(plan))
        cpd = root / "cpd"
        cpd.mkdir()
        target = cpd / f"{formula}_mp-{mpid}"
        target.mkdir()
        _write_poscar(target, 2)
        _write_incar(target)
        _write_potcar(target)
        _write_kpoints(target)
        return root

    def _assert_job_state(self, calc_dir: Path, expected: str = "running") -> None:
        """Assert JobStore has *expected* state for a calculation directory."""
        from vasp_sop.core.job_store import JobStore
        store = JobStore()
        actual = store.latest(str(calc_dir.resolve()))
        store.close()
        assert actual == expected, \
            f"JobStore: expected {expected} for {calc_dir.name}, got {actual}"

    def test_walkthrough(self, tmp_path, monkeypatch):
        """Walk through TARGET → COMPETING → CPD_POST → UC_DF → DONE."""
        from vasp_sop.cli.main import _phase, _advance_one_system

        formula = "GaN"
        mpid = "804"
        root = self._make_system(tmp_path, formula, mpid)

        # Shared submit tracker + fake cache
        submit_calls: list[str] = []
        monkeypatch.setattr("vasp_sop.core.jobs.submit_vasp",
                           lambda p: (submit_calls.append(str(p.resolve())) or
                                      type("J", (), {"task_name": "t"})()))

        cache_data: dict[str, dict] = {}
        monkeypatch.setattr("vasp_sop.core.cache.cache_lookup",
                           lambda p: cache_data.get(str(p.resolve())))
        monkeypatch.setattr("vasp_sop.core.cache.vasp_results_get",
                           lambda f, k: cache_data.get(f"{f}_{k}"))

        s = _make_system_dict(root)

        # ── Phase 1: TARGET ────────────────────────────────────────
        assert _phase(s) == "TARGET", "bare system should start in TARGET"
        _advance_one_system(s, dry_run=False)
        # TARGET is a no-op when target is not cached — no submission

        # ── Phase 2: COMPETING ─────────────────────────────────────
        # Cache target result to advance past TARGET
        cache_data[f"{formula}_{mpid}"] = {"total_energy": -12.0}
        td = root / "cpd" / f"{formula}_mp-{mpid}"
        cache_data[str(td.resolve())] = {"total_energy": -12.0}

        # Add unconverged competing dir so _competing_dirs returns it
        comp = root / "cpd" / "Ga_mp-142"
        comp.mkdir()
        _write_poscar(comp, 1)
        _write_incar(comp)
        _write_potcar(comp)
        _write_kpoints(comp)
        self._write_unconverged_outcar(comp)

        # Advance — records cached target as done in JobStore, then submits competing
        _advance_one_system(s, dry_run=False)
        assert _phase(s) != "TARGET", "system should advance past TARGET"
        assert str(comp.resolve()) in submit_calls, \
            "competing phase should be submitted"
        self._assert_job_state(comp)
        # ── Phase 3: CPD_POST ──────────────────────────────────────
        # Cache + converge competing dir so _competing_dirs returns empty
        cache_data[str(comp.resolve())] = {"total_energy": -5.0}
        cache_data["Ga_142"] = {"total_energy": -5.0}
        self._write_converged_outcar(comp)

        assert _phase(s) == "CPD_POST", "no pending competing dirs → CPD_POST"
        _advance_one_system(s, dry_run=False)

        # ── Phase 4: UC_DF ─────────────────────────────────────────
        # Add CPD artifacts
        cpd = root / "cpd"
        (cpd / "target_vertices.yaml").write_text("tv: 1\n")
        (cpd / "standard_energies.yaml").write_text("se: 1\n")

        assert _phase(s) == "UC_DF", "CPD artifacts present → UC_DF"

        # Create UC and defect directories
        uc = root / "unitcell"
        uc.mkdir()
        for t in ("band", "dos", "dielectric"):
            td = uc / t
            td.mkdir()
            _write_incar(td)
        df = root / "defect"
        df.mkdir()
        perfect = df / "perfect"
        perfect.mkdir()
        _write_incar(perfect)
        _write_kpoints(perfect)
        _write_poscar(perfect, 2)
        _write_potcar(perfect)

        defect_dir = df / "Va_Ga_0"
        defect_dir.mkdir()
        _write_incar(defect_dir)
        _write_kpoints(defect_dir)
        _write_poscar(defect_dir, 2)
        _write_potcar(defect_dir)
        _advance_one_system(s, dry_run=False)
        # Should submit UC (band/dos/dielectric) + defect (perfect + Va_Ga_0)
        for t in ("band", "dos", "dielectric"):
            assert str((uc / t).resolve()) in submit_calls, \
                f"uc-{t} should be submitted"
        assert str(perfect.resolve()) in submit_calls, "perfect should be submitted"
        self._assert_job_state(uc / "band")

        # ── Phase 5: DONE ──────────────────────────────────────────
        # Cache all UC + defect results and add converged OUTCARs
        for t in ("band", "dos", "dielectric"):
            d = uc / t
            cache_data[str(d.resolve())] = {"total_energy": -5.0}
            self._write_converged_outcar(d)
        for d in (perfect, defect_dir):
            cache_data[str(d.resolve())] = {"total_energy": -5.0}
            self._write_converged_outcar(d)
        from vasp_sop.core.job_store import JobStore
        for d in (uc / "band", uc / "dos", uc / "dielectric", perfect, defect_dir):
            JobStore().record(str(d.resolve()), "done")

        # Required intermediate files for DONE
        (cpd / "composition_energies.yaml").write_text("ce: 1\n")
        (cpd / "chem_pot_diag.json").write_text('{"tv": 1}\n')
        (uc / "unitcell.yaml").write_text("uy: 1\n")
        (df / "defect_energy_summary.json").write_text("{}")
        for d in (perfect, defect_dir):
            (d / "calc_results.json").write_text("{}\n")
            (d / "correction.json").write_text("{}\n")
            (d / "defect_structure_info.json").write_text("{}\n")
            (d / "defect_volume_fraction.json").write_text("{}\n")
        (perfect / "perfect_band_edge_state.json").write_text("{}\n")

        assert _phase(s) == "DONE", "all artifacts present → DONE"

    def test_uc_resubmit_when_vasprxml_missing(self, tmp_path, monkeypatch):
        """UC task with converged OUTCAR but missing vasprun.xml → re-submitted."""
        from vasp_sop.cli.main import _phase, _advance_one_system

        root = self._make_system(tmp_path, "GaN", "804")
        cpd = root / "cpd"
        (cpd / "target_vertices.yaml").write_text("tv: 1\n")
        (cpd / "standard_energies.yaml").write_text("se: 1\n")

        # Create UC dirs with converged OUTCAR but NO vasprun.xml
        uc = root / "unitcell"
        uc.mkdir()
        for t in ("band", "dos", "dielectric"):
            td = uc / t
            td.mkdir()
            _write_incar(td)
            self._write_converged_outcar(td)

        submit_calls: list[str] = []
        monkeypatch.setattr("vasp_sop.core.jobs.submit_vasp",
                           lambda p: (submit_calls.append(str(p.resolve())) or
                                      type("J", (), {"task_name": "t"})()))
        cache_data: dict[str, dict] = {}
        # Cache only the target (so phase advances past COMPETING)
        td = root / "cpd" / "GaN_mp-804"
        cache_data[str(td.resolve())] = {"total_energy": -12.0}
        cache_data["GaN_804"] = {"total_energy": -12.0}
        monkeypatch.setattr("vasp_sop.core.cache.cache_lookup",
                           lambda p: cache_data.get(str(p.resolve())))

        s = _make_system_dict(root)
        _advance_one_system(s, dry_run=False)
        self._assert_job_state(uc / "band")

        # band and dos should be re-submitted (missing vasprun.xml)
        assert str((uc / "band").resolve()) in submit_calls, \
            "band should re-submit (no vasprun.xml)"
        assert str((uc / "dos").resolve()) in submit_calls, \
            "dos should re-submit (no vasprun.xml)"
        # dielectric should NOT be re-submitted (OUTCAR only is sufficient)
        assert str((uc / "dielectric").resolve()) not in submit_calls, \
            "dielectric should not re-submit (OUTCAR sufficient)"
