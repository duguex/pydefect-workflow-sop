"""Tests for vasp_sop.cli.main -- batch run, advance system, dry-run behavior.

These tests verify that the batch pipeline correctly handles dry-run vs real
submission, and that cached submission logic isn't silently skipped.
"""

from pathlib import Path
import os
import time
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


class TestHandleCpd:
    def test_energies_dispatches_without_path_scope_error(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "vasp_sop.defect.cpd.compute_chemical_potentials",
            lambda *args: calls.append(args),
        )
        from argparse import Namespace
        from vasp_sop.cli.main import _handle_cpd

        _handle_cpd(Namespace(
            action="energies", cpd_dir=tmp_path, formula="CsEuCl3",
        ))

        assert calls
        assert calls[0][0] == tmp_path.resolve()


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



class TestCompetingFailureGate:
    def test_failed_competing_phase_blocks_cpd(self, competing_system, tmp_path):
        """A failed competing phase must not silently advance to CPD."""
        from vasp_sop.core.cache import override_cache_root
        from vasp_sop.core.job_store import JobStore
        from vasp_sop.cli.main import _phase

        override_cache_root(tmp_path / ".vasp_sop")
        root = competing_system
        target = root / "cpd" / "NaCl_mp-12345"
        competing = root / "cpd" / "Other_mp-99999"
        store = JobStore()
        store.record(str(target.resolve()), "converged")
        store.record(str(competing.resolve()), "failed", reason="crisp_failed")

        assert _phase(_make_system_dict(root)) == "COMPETING"

    def test_missing_competing_inputs_blocks_cpd(self, competing_system, tmp_path):
        """A POSCAR-only competing phase must keep the system in COMPETING."""
        from vasp_sop.core.cache import override_cache_root
        from vasp_sop.core.job_store import JobStore
        from vasp_sop.cli.main import _phase

        override_cache_root(tmp_path / ".vasp_sop")
        root = competing_system
        target = root / "cpd" / "NaCl_mp-12345"
        other = root / "cpd" / "Other_mp-99999"
        (other / "OUTCAR").write_text(
            " General timing and accounting\n"
            " TOTAL-FORCE (eV/Angst)\n ---\n"
            " 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000\n"
        )
        incomplete = root / "cpd" / "Missing_mp-777"
        incomplete.mkdir()
        _write_poscar(incomplete, 2)
        _write_converged_outcar(incomplete)
        JobStore().record(str(target.resolve()), "converged")

        assert _phase(_make_system_dict(root)) == "COMPETING"

    def test_failed_marker_overrides_old_converged_output(self, competing_system, tmp_path):
        """A failed latest attempt cannot validate an older converged OUTCAR."""
        from vasp_sop.core.cache import override_cache_root
        from vasp_sop.core.job_store import JobStore
        from vasp_sop.cli.main import _phase

        override_cache_root(tmp_path / ".vasp_sop")
        root = competing_system
        target = root / "cpd" / "NaCl_mp-12345"
        competing = root / "cpd" / "Other_mp-99999"
        (competing / "OUTCAR").write_text(
            " General timing and accounting\n"
            " TOTAL-FORCE (eV/Angst)\n ---\n"
            " 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000\n"
        )
        (competing / ".failed").write_text("EXIT_CODE: 1\n")
        JobStore().record(str(target.resolve()), "converged")

        assert _phase(_make_system_dict(root)) == "COMPETING"


    def test_cpd_persistence_does_not_regress_on_failed_competing(
        self, competing_system, tmp_path
    ):
        """After CPD artifacts exist, later failed phase records do not regress phase."""
        from vasp_sop.core.cache import override_cache_root
        from vasp_sop.core.job_store import JobStore
        from vasp_sop.cli.main import _phase

        override_cache_root(tmp_path / ".vasp_sop")
        root = competing_system
        cpd = root / "cpd"
        (cpd / "target_vertices.yaml").write_text("vertices: []\n")
        store = JobStore()
        store.record(str((cpd / "Other_mp-99999").resolve()), "failed")

        assert _phase(_make_system_dict(root)) == "UNITCELL_DEFECT"


class TestBatchCpdTargetHandoff:
    def test_cpd_stage_restores_structure_opt_from_cache(self, competing_system, tmp_path, monkeypatch):
        """CPD phase: put target → fetch structure_opt from cache."""
        from vasp_sop.core.cache import override_cache_root
        from vasp_sop.core.job_store import JobStore
        from vasp_sop.cli.main import _advance_one_system

        override_cache_root(tmp_path / ".vasp_sop")
        root = competing_system
        target = root / "cpd" / "NaCl_mp-12345"
        (root / "defect" / "defect_in.yaml").parent.mkdir(parents=True, exist_ok=True)
        (root / "defect" / "defect_in.yaml").write_text("formula: NaCl\n")
        for f, content in [("OUTCAR", "t\n"), ("CONTCAR", "t\n"), ("vasprun.xml", "t\n"),
                           ("INCAR", "ENCUT=400\n"), ("KPOINTS", "4 4 4\n"), ("POTCAR", "PAW\n")]:
            (target / f).write_text(content)
        competing = root / "cpd" / "Other_mp-99999"
        store = JobStore()
        store.record(str(target.resolve()), "converged")
        store.record(str(competing.resolve()), "converged")

        source = root / "unitcell" / "structure_opt"
        put_calls = []
        fetch_calls = []
        monkeypatch.setattr("vasp_sop.core.cache.vasp_results_put",
                           lambda p, **kw: (put_calls.append(p) or "fake-key"))
        monkeypatch.setattr("vasp_sop.core.cache._vc_fetch",
                           lambda key, target_dir, **kw: (
                               fetch_calls.append((key, target_dir)),
                               Path(target_dir).mkdir(parents=True, exist_ok=True),
                               True)[-1])
        monkeypatch.setattr("vasp_sop.defect.cpd.compute_chemical_potentials",
                           lambda *args, **kwargs: None)

        _advance_one_system(_make_system_dict(root), dry_run=False)

        assert len(put_calls) == 1 and put_calls[0] == target, "should put target"
        assert len(fetch_calls) == 1, "should fetch to staging"
        assert fetch_calls[0][0] == "fake-key"
        assert str(fetch_calls[0][1]).startswith(str(source.parent))

def _write_si_inputs(d: Path, *, energy: float = -10.0) -> None:
    """Write all 7 VASP input files required by vasp-cache v0.3.0."""
    poscar = """Si
1.0
5.43 0 0
0 5.43 0
0 0 5.43
Si
2
Direct
0 0 0
0.25 0.25 0.25
"""
    (d / "POSCAR").write_text(poscar)
    (d / "CONTCAR").write_text(poscar)
    (d / "INCAR").write_text("ENCUT = 520\nGGA = PE\n")
    (d / "KPOINTS").write_text("A\n0\nGamma\n4 4 4\n0 0 0\n")
    (d / "POTCAR").write_text(
        "  PAW_PBE Si 05Jan2001\n  TITEL  = PAW_PBE Si 05Jan2001\n   4.00000000000000\n"
    )
    (d / "vasprun.xml").write_text(
        "<modeling><calculation><scstep><energy>"
        f'<i name="e_fr_energy">{energy}</i>'
        "</energy></scstep></calculation></modeling>\n"
    )
    (d / "OUTCAR").write_text(
        f" free  energy    TOTEN  =    {energy} eV\n"
        " General timing and accounting\n"
    )


class TestCachePutGet:
    _cr: Path | None = None

    @pytest.fixture(autouse=True)
    def _isolate_cache(self, tmp_path: Path) -> None:
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")

    def test_put_and_lookup(self, tmp_path: Path):
        from vasp_sop.core.cache import vasp_results_put, cache_lookup
        src = tmp_path / "src"
        src.mkdir()
        _write_si_inputs(src, energy=-10.0)
        key = vasp_results_put(src)
        assert key is not None
        result = cache_lookup(src)
        assert result is not None
        assert result["formula"] == "Si"

    def test_get_missing_returns_none(self):
        from vasp_sop.core.cache import vasp_results_get
        assert vasp_results_get("Never", "no_such_key") is None

    def test_identical_inputs_share_key(self, tmp_path: Path):
        from vasp_sop.core.cache import vasp_results_put, cache_lookup, vasp_results_get
        src1 = tmp_path / "src1"
        src1.mkdir()
        _write_si_inputs(src1, energy=-10.0)
        key1 = vasp_results_put(src1)
        assert key1 is not None
        src2 = tmp_path / "src2"
        src2.mkdir()
        _write_si_inputs(src2, energy=-10.0)
        key2 = vasp_results_put(src2)
        assert key2 is not None
        # Identity-based cache: identical inputs → same key (dedup)
        assert key1 == key2
        assert vasp_results_get("Si", key1) is not None


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
        assert "CPD" in captured
        assert "UC" in captured
        assert "Done" in captured

    def test_batch_status_no_systems(self, tmp_path, capsys):
        from vasp_sop.cli.main import _batch_status
        _batch_status(tmp_path)
        captured = capsys.readouterr().out
        assert "No vasp-sop systems found" in captured

    def test_batch_status_reports_loop_and_keeps_phase_table(
        self, tmp_path, capsys
    ):
        """Status includes lifecycle details and the existing phase table."""
        self._make_system(tmp_path)
        from vasp_sop.core.batch_lifecycle import _pid_file

        _pid_file(tmp_path).write_text(
            f"{os.getpid()}\n{tmp_path}\n{time.time() - 3661}\n"
        )
        (tmp_path / "batch_snapshot.json").write_text(
            '{"timestamp":"2026-07-16T00:00:00Z",'
            '"phases":{"COMPLETE":1,"COMPETING":2}}'
        )
        try:
            from vasp_sop.cli.main import _batch_status
            _batch_status(tmp_path)
        finally:
            _pid_file(tmp_path).unlink(missing_ok=True)

        captured = capsys.readouterr().out
        assert "Loop running" in captured
        assert f"PID {os.getpid()}" in captured
        assert "uptime" in captured.lower()
        assert "COMPLETE=1" in captured
        assert "System" in captured
        assert "Phase" in captured


class TestAdvanceDryRunPostprocess:
    """Issue #20: dry-run in UNITCELL_DEFECT phase must preview post-processing
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
        """Build a system whose _phase() returns 'UNITCELL_DEFECT' (target converged,
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
        # NOTE: standard_energies.yaml and target_vertices.yaml are phase
        # gate requirements (issue #93) — removing them would regress the
        # system to CHEM_POT_DIAGRAM.  Only remove downstream artifacts.
        root = self._make_uc_df_system(tmp_path, with_artifacts=True)
        # Remove the artifacts we want the test to flag as missing.
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
        monkeypatch.setattr("vasp_sop.core.cache.vasp_results_put", lambda *a, **kw: "mock-key")
        monkeypatch.setattr("vasp_sop.core.cache.restore_from_key", lambda key, target_dir, **kw: True)
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
        """Build a minimal system in UNITCELL_DEFECT phase (target+cached, CPD done,
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
    """Tests for cache put CLI command (v0.3.0 identity-based)."""

    @pytest.fixture(autouse=True)
    def _isolate_cache(self, tmp_path: Path) -> None:
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")

    def test_cache_put_full_inputs(self, tmp_path, capsys):
        """cache put with complete VASP inputs → key returned."""
        d = tmp_path / "GaN_mp-804"
        d.mkdir()
        _write_si_inputs(d, energy=-12.0)
        from vasp_sop.cli.main import _handle_cache
        import argparse
        args = argparse.Namespace(cache_action="put", cache_root=None, path=d,
                                   formula=None, task_name=None, recursive=False)
        _handle_cache(args)
        captured = capsys.readouterr().out
        assert "key=" in captured

    def test_cache_put_missing_inputs(self, tmp_path, capsys):
        """cache put with only OUTCAR → identity fails, key is None."""
        d = tmp_path / "bare"
        d.mkdir()
        (d / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -10.0 eV\n"
            " General timing and accounting\n"
        )
        from vasp_sop.cli.main import _handle_cache
        import argparse
        args = argparse.Namespace(cache_action="put", cache_root=None, path=d,
                                   formula=None, task_name=None, recursive=False)
        _handle_cache(args)
        captured = capsys.readouterr().out
        assert "converged" in captured  # OUTCAR was converged

    def test_cache_put_recursive(self, tmp_path, capsys):
        """cache put -r with full inputs in each dir."""
        dirs = ["sys_A/cpd/GaN_mp-804", "sys_A/cpd/Other_mp-101"]
        for rel in dirs:
            p = tmp_path / rel
            p.mkdir(parents=True)
            _write_si_inputs(p, energy=-5.0)
        from vasp_sop.cli.main import _handle_cache
        import argparse
        args = argparse.Namespace(cache_action="put", cache_root=None, path=tmp_path,
                                   formula=None, task_name=None, recursive=True)
        _handle_cache(args)
        captured = capsys.readouterr().out
        assert "Cached 2 directories" in captured


class TestFullPipelineWalkthrough:
    """Drive a system through all 5 phases: STRUCTURE_OPT → COMPETING → CHEM_POT_DIAGRAM → UNITCELL_DEFECT → COMPLETE.

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
        monkeypatch.setattr("vasp_sop.core.cache.vasp_results_put", lambda *a, **kw: "mock-key-0000")
        monkeypatch.setattr("vasp_sop.core.cache.restore_from_key", lambda key, target_dir, **kw: True)

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

    def _assert_job_state(self, calc_dir: Path, expected: str = "submitted") -> None:
        """Assert JobStore has *expected* state for a calculation directory."""
        from vasp_sop.core.job_store import JobStore
        store = JobStore()
        actual = store.latest(str(calc_dir.resolve()))
        store.close()
        assert actual == expected, \
            f"JobStore: expected {expected} for {calc_dir.name}, got {actual}"

    def test_walkthrough(self, tmp_path, monkeypatch):
        """Walk through STRUCTURE_OPT → COMPETING → CHEM_POT_DIAGRAM → UNITCELL_DEFECT → COMPLETE."""
        from vasp_sop.cli.main import _phase, _advance_one_system
        from vasp_sop.core.job_store import JobStore

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

        # ── Phase 1: STRUCTURE_OPT ────────────────────────────────────────
        assert _phase(s) == "STRUCTURE_OPT", "bare system should start in STRUCTURE_OPT"
        _advance_one_system(s, dry_run=False)
        # Target submitted; simulate backfill: mark converged for next cycle
        td = root / "cpd" / f"{formula}_mp-{mpid}"
        assert str(td.resolve()) in submit_calls, "target should be submitted"
        JobStore().record(str(td.resolve()), "converged", source="backfill")
        JobStore().untrack(str(td.resolve()))

        # ── Phase 2: COMPETING ─────────────────────────────────────
        # Cache target result + disk OUTCAR so JobStore may record converged
        # only when check_converged is true (no false converged).
        cache_data[f"{formula}_{mpid}"] = {"total_energy": -12.0}
        td = root / "cpd" / f"{formula}_mp-{mpid}"
        cache_data[str(td.resolve())] = {"total_energy": -12.0}
        self._write_converged_outcar(td)

        # Add unconverged competing dir so _competing_dirs returns it
        comp = root / "cpd" / "Ga_mp-142"
        comp.mkdir()
        _write_poscar(comp, 1)
        _write_incar(comp)
        _write_potcar(comp)
        _write_kpoints(comp)
        self._write_unconverged_outcar(comp)

        # Advance — records target converged from disk, then submits competing
        _advance_one_system(s, dry_run=False)
        assert _phase(s) != "STRUCTURE_OPT", "system should advance past STRUCTURE_OPT"
        assert str(comp.resolve()) in submit_calls, \
            "competing phase should be submitted"
        self._assert_job_state(comp)
        # ── Phase 3: CHEM_POT_DIAGRAM ──────────────────────────────────────
        # Cache + converge competing dir so _competing_dirs returns empty
        cache_data[str(comp.resolve())] = {"total_energy": -5.0}
        cache_data["Ga_142"] = {"total_energy": -5.0}
        self._write_converged_outcar(comp)

        assert _phase(s) == "CHEM_POT_DIAGRAM", "no pending competing dirs → CHEM_POT_DIAGRAM"
        _advance_one_system(s, dry_run=False)

        # ── Phase 4: UNITCELL_DEFECT ─────────────────────────────────────────
        # Add CPD artifacts
        cpd = root / "cpd"
        (cpd / "target_vertices.yaml").write_text("tv: 1\n")
        (cpd / "standard_energies.yaml").write_text("se: 1\n")

        assert _phase(s) == "UNITCELL_DEFECT", "CPD artifacts present → UNITCELL_DEFECT"

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

        # ── Phase 5: COMPLETE ──────────────────────────────────────────
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
            JobStore().record(str(d.resolve()), "converged")

        # Required intermediate files for COMPLETE
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

        assert _phase(s) == "COMPLETE", "all artifacts present → COMPLETE"
    def test_structure_opt_cache_hit_skips_vasp_submission(self, tmp_path, monkeypatch):
        """STRUCTURE_OPT with cache hit restores from cache, never submits VASP."""
        from vasp_sop.cli.main import _phase, _advance_one_system
        from vasp_sop.core.job_store import JobStore

        formula, mpid = "GaN", "804"
        root = self._make_system(tmp_path, formula, mpid)

        submit_calls = []
        monkeypatch.setattr("vasp_sop.core.jobs.submit_vasp",
                           lambda p: (_ for _ in ()).throw(AssertionError("submit_vasp called")))
        restore_calls = []
        def _mock_restore(p, **kw):
            restore_calls.append(str(p))
            td_path = Path(p)
            (td_path / "OUTCAR").write_text(
                " General timing and accounting\n"
                " TOTAL-FORCE (eV/Angst)\n ---\n"
                " 0.0 0.0 0.0 0.0 0.0 0.0\n"
            )
            (td_path / "CONTCAR").write_text("mock\n")
            (td_path / "vasprun.xml").write_text("<mock/>\n")
            return True
        monkeypatch.setattr("vasp_sop.core.cache.restore_from_cache", _mock_restore)

        td = root / "cpd" / f"{formula}_mp-{mpid}"
        monkeypatch.setattr("vasp_sop.core.cache.cache_lookup",
                           lambda p, **kw: ({"formula": formula, "final_energy": -12.0}
                                            if str(p.resolve()) == str(td.resolve()) else None))

        s = _make_system_dict(root)
        assert _phase(s) == "STRUCTURE_OPT"

        _advance_one_system(s, dry_run=False)

        assert restore_calls == [str(td.resolve())], "restore_from_cache should be called for target"
        assert JobStore().latest(str(td.resolve())) == "converged", "target should be marked converged"

    def test_cache_only_full_pipeline_zero_vasp_submit(self, tmp_path, monkeypatch):
        """STRUCTURE_OPT → CPD without any VASP when target + competing are cached."""
        from vasp_sop.cli.main import _phase, _advance_one_system
        from vasp_sop.core.job_store import JobStore
        from pymatgen.core import Composition

        formula, mpid = "GaN", "804"
        root = self._make_system(tmp_path, formula, mpid)

        # ── Mock submit_vasp: must NEVER be called ────────────────────
        submit_calls = []
        monkeypatch.setattr("vasp_sop.core.jobs.submit_vasp",
                           lambda p: (submit_calls.append(str(p)) or
                                      type("J", (), {"task_name": "t"})()))

        # ── Mock cache: target + competing dirs all hit ───────────────
        td = root / "cpd" / f"{formula}_mp-{mpid}"
        comp = root / "cpd" / "Ga_mp-142"
        comp.mkdir()
        _write_poscar(comp, 1); _write_incar(comp); _write_potcar(comp); _write_kpoints(comp)

        cache_dirs = {str(td.resolve()): {"formula": formula, "final_energy": -12.0},
                      str(comp.resolve()): {"formula": "Ga", "final_energy": -5.0}}
        monkeypatch.setattr("vasp_sop.core.cache.cache_lookup",
                           lambda p, **kw: cache_dirs.get(str(p.resolve())))
        def _mock_restore(p, **kw):
            d = Path(p)
            poscar = (d / "POSCAR").read_text()
            (d / "CONTCAR").write_text(poscar)
            (d / "OUTCAR").write_text(" General timing and accounting\n"
                " TOTAL-FORCE (eV/Angst)\n ---\n 0.0 0.0 0.0 0.0 0.0 0.0\n")
            (d / "vasprun.xml").write_text("<mock/>\n")
            return True
        monkeypatch.setattr("vasp_sop.core.cache.restore_from_cache", _mock_restore)
        # ── Mock cache put + structure_opt restore ──────────────────
        monkeypatch.setattr("vasp_sop.core.cache.vasp_results_put",
                           lambda p, **kw: "fake-key-1234")
        def _mock_restore_key(key, target_dir, **kw):
            d = Path(target_dir)
            d.mkdir(parents=True, exist_ok=True)
            poscar = (td / "POSCAR").read_text()
            for f, content in [("POSCAR", poscar), ("CONTCAR", poscar),
                               ("INCAR", "x\n"), ("KPOINTS", "x\n"), ("POTCAR", "x\n"),
                               ("OUTCAR", "x\n"), ("vasprun.xml", "x\n")]:
                (d / f).write_text(content)
            return True
        monkeypatch.setattr("vasp_sop.core.cache.restore_from_key", _mock_restore_key)

        # ── Mock CPD computation + composition ─────────────────────
        cpd_done = []
        monkeypatch.setattr("vasp_sop.defect.cpd.compute_chemical_potentials",
                           lambda *a, **kw: cpd_done.append(True))
        monkeypatch.setattr("vasp_sop.defect.cpd._get_target_composition",
                           lambda formula: Composition("X"))

        s = _make_system_dict(root)

        # Advance through phases
        phases_seen = set()
        for _ in range(10):
            p = _phase(s)
            phases_seen.add(p)
            if p in ("COMPLETE", "NO_TARGET", "UNITCELL_DEFECT"):
                break
            _advance_one_system(s, dry_run=False)

        assert "CHEM_POT_DIAGRAM" in phases_seen, "should reach CPD phase"
        assert submit_calls == [], f"VASP submitted: {submit_calls}"
        assert cpd_done, "CPD computation should have run"
        so = root / "unitcell" / "structure_opt"
        assert (so / "OUTCAR").is_file(), "structure_opt should be restored from cache"

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



class TestPhaseFailedSkip:
    """JobStore 'failed' defects must not block COMPLETE (issue #0005 / failed-gate)."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path: Path):
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")

    def _complete_ready_system(self, tmp_path: Path) -> tuple[dict, Path, Path]:
        """System with all COMPLETE gates except one optional second defect."""
        formula, mpid = "GaN", "804"
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
        (cpd / "target_vertices.yaml").write_text("tv: 1\n")
        (cpd / "composition_energies.yaml").write_text("ce: 1\n")
        (cpd / "standard_energies.yaml").write_text("se: 1\n")
        (cpd / "chem_pot_diag.json").write_text("{}\n")

        uc = root / "unitcell"
        uc.mkdir()
        (uc / "unitcell.yaml").write_text("uy: 1\n")
        for t in ("band", "dos", "dielectric"):
            td = uc / t
            td.mkdir()
            _write_incar(td)

        df = root / "defect"
        df.mkdir()
        (df / "defect_energy_summary.json").write_text("{}\n")
        perfect = df / "perfect"
        perfect.mkdir()
        (perfect / "perfect_band_edge_state.json").write_text("{}\n")

        good = df / "Va_Ga_0"
        good.mkdir()
        _write_incar(good)
        _write_poscar(good, 2)
        _write_potcar(good)
        _write_kpoints(good)
        (good / "calc_results.json").write_text("{}\n")
        (good / "correction.json").write_text("{}\n")
        (good / "defect_structure_info.json").write_text("{}\n")

        s = _make_system_dict(root)
        from vasp_sop.core.job_store import JobStore
        for d in (uc / "band", uc / "dos", uc / "dielectric", perfect, good):
            JobStore().record(str(d.resolve()), "converged")
        return s, root, df

    def test_failed_defect_does_not_block_complete(self, tmp_path: Path):
        """Failed defect without analysis intermediates must not block COMPLETE."""
        from vasp_sop.cli.main import _phase
        from vasp_sop.core.job_store import JobStore

        s, _root, df = self._complete_ready_system(tmp_path)
        bad = df / "Va_Ga_-3"
        bad.mkdir()
        _write_incar(bad)
        _write_poscar(bad, 2)
        _write_potcar(bad)
        _write_kpoints(bad)
        # No analysis intermediates — only JobStore failed
        JobStore().record(str(bad.resolve()), "failed", reason="unconverged")

        assert _phase(s) == "COMPLETE"

    def test_unfinished_defect_blocks_complete(self, tmp_path: Path):
        """Defect without intermediates and not failed stays UNITCELL_DEFECT."""
        from vasp_sop.cli.main import _phase

        s, _root, df = self._complete_ready_system(tmp_path)
        pending = df / "Va_Ga_-1"
        pending.mkdir()
        _write_incar(pending)
        _write_poscar(pending, 2)
        _write_potcar(pending)
        _write_kpoints(pending)

        assert _phase(s) == "UNITCELL_DEFECT"

    def test_junk_subdir_without_vasp_inputs_ignored(self, tmp_path: Path):
        """Non-calculation subdirs under defect/ must not block COMPLETE."""
        from vasp_sop.cli.main import _phase

        s, _root, df = self._complete_ready_system(tmp_path)
        junk = df / "c3v"
        junk.mkdir()
        (junk / "readme.txt").write_text("not a calc\n")

        assert _phase(s) == "COMPLETE"


class TestUcFalseConvergedResubmit:
    """UC tasks marked converged in JobStore but missing vasprun must resubmit."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path: Path, monkeypatch):
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")
        monkeypatch.setattr("vasp_sop.defect.builder.build_all", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.builder._generate_vasp_inputs", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.vasp.io.prepare_inputs", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.unitcell._prepare_all_inputs", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.unitcell.build_unitcell_yaml", lambda *a, **kw: None)
        monkeypatch.setattr("vasp_sop.defect.analysis.analyze", lambda *a, **kw: None)

    def test_stale_converged_band_resubmits(self, tmp_path: Path, monkeypatch):
        from vasp_sop.cli.main import _advance_one_system
        from vasp_sop.core.job_store import JobStore

        formula, mpid = "GaN", "804"
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
        (cpd / "target_vertices.yaml").write_text("tv: 1\n")
        (cpd / "standard_energies.yaml").write_text("se: 1\n")

        uc = root / "unitcell"
        uc.mkdir()
        band = uc / "band"
        band.mkdir()
        _write_incar(band)
        # Converged OUTCAR but no vasprun.xml
        (band / "OUTCAR").write_text(
            " General timing and accounting\n"
            " TOTAL-FORCE (eV/Angst)\n"
            " ---\n"
            " 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000\n"
        )
        JobStore().record(str(band.resolve()), "converged", source="stale")

        # dos/dielectric complete enough to not matter for this assert
        for t in ("dos", "dielectric"):
            td = uc / t
            td.mkdir()
            _write_incar(td)

        df = root / "defect"
        df.mkdir()

        submit_calls: list[str] = []
        monkeypatch.setattr(
            "vasp_sop.core.jobs.submit_vasp",
            lambda p: (submit_calls.append(str(p.resolve()))
                       or type("J", (), {"task_name": "t"})()),
        )

        s = _make_system_dict(root)
        _advance_one_system(s, dry_run=False)

        assert str(band.resolve()) in submit_calls, \
            "band marked converged but missing vasprun.xml must resubmit"

class TestAdvanceAnalyzeStatusPrint:
    """batch _advance_one_system must surface analyze() full|partial|failed."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path: Path, monkeypatch):
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")
        monkeypatch.setattr(
            "vasp_sop.defect.builder.build_all", lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "vasp_sop.defect.builder._generate_vasp_inputs",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "vasp_sop.vasp.io.prepare_inputs", lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "vasp_sop.defect.unitcell._prepare_all_inputs",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "vasp_sop.defect.unitcell.build_unitcell_yaml",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "vasp_sop.core.jobs.submit_vasp",
            lambda p: type("J", (), {"task_name": "t"})(),
        )
        monkeypatch.setattr(
            "vasp_sop.core.jobs.move_crisp_outputs", lambda *a, **kw: None,
        )

    def _make_ready_for_postprocess(self, tmp_path: Path) -> Path:
        """UNITCELL_DEFECT system with VASP complete so analyze is invoked."""
        formula, mpid = "GaN", "804"
        root = tmp_path / formula
        root.mkdir()
        plan = {
            "project": {
                "formula": formula,
                "dopant_elements": [],
                "poscar_src": f"MP mp-{mpid}",
            },
            "parameters": {"functional": "pbesol"},
            "supercell": {"tool": "doped", "min_distance": 10.0},
        }
        (root / "plan.yaml").write_text(yaml.dump(plan))
        cpd = root / "cpd"
        cpd.mkdir()
        target = cpd / f"{formula}_mp-{mpid}"
        target.mkdir()
        for f in ("POSCAR", "INCAR", "POTCAR", "KPOINTS"):
            (target / f).write_text("x\n")
        (cpd / "target_vertices.yaml").write_text("tv: 1\n")
        (cpd / "standard_energies.yaml").write_text("se: 1\n")
        (cpd / "composition_energies.yaml").write_text("ce: 1\n")
        (cpd / "chem_pot_diag.json").write_text("{}\n")

        uc = root / "unitcell"
        uc.mkdir()
        (uc / "unitcell.yaml").write_text("uy: 1\n")
        for t in ("band", "dos", "dielectric"):
            td = uc / t
            td.mkdir()
            (td / "INCAR").write_text("NSW = 0\n")
            # band/dos need vasprun for check_task_complete
            (td / "OUTCAR").write_text(
                " General timing and accounting\n"
                " TOTAL-FORCE (eV/Angst)\n"
                " ---\n"
                " 0.0 0.0 0.0 0.0 0.0 0.0\n"
            )
            if t != "dielectric":
                (td / "vasprun.xml").write_text("<xml/>\n")

        df = root / "defect"
        df.mkdir()
        perfect = df / "perfect"
        perfect.mkdir()
        for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"):
            (perfect / f).write_text("x\n")
        (perfect / "OUTCAR").write_text(
            " General timing and accounting\n"
            " TOTAL-FORCE (eV/Angst)\n ---\n"
            " 0.0 0.0 0.0 0.0 0.0 0.0\n"
        )
        defect = df / "Va_Ga_0"
        defect.mkdir()
        for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"):
            (defect / f).write_text("x\n")
        (defect / "OUTCAR").write_text(
            " General timing and accounting\n"
            " TOTAL-FORCE (eV/Angst)\n ---\n"
            " 0.0 0.0 0.0 0.0 0.0 0.0\n"
        )
        # Write calc_results.json so vasprun recovery is skipped (#0004/#0016)
        for child_dir in (perfect, defect):
            (child_dir / "calc_results.json").write_text("{}\n")
        from vasp_sop.core.job_store import JobStore
        for p in (target, uc / "band", uc / "dos", uc / "dielectric",
                  perfect, defect):
            JobStore().record(str(p.resolve()), "converged")
        return root

    def test_partial_status_prints_tilde_not_complete(
        self, tmp_path: Path, monkeypatch, capsys,
    ):
        root = self._make_ready_for_postprocess(tmp_path)
        monkeypatch.setattr(
            "vasp_sop.defect.analysis.analyze",
            lambda *a, **kw: "partial",
        )
        from vasp_sop.cli.main import _advance_one_system
        s = _make_system_dict(root)
        _advance_one_system(s, dry_run=False)
        out = capsys.readouterr().out
        assert "post-process partial" in out
        assert "pipeline complete" not in out

    def test_full_status_prints_pipeline_complete(
        self, tmp_path: Path, monkeypatch, capsys,
    ):
        root = self._make_ready_for_postprocess(tmp_path)
        monkeypatch.setattr(
            "vasp_sop.defect.analysis.analyze",
            lambda *a, **kw: "full",
        )
        from vasp_sop.cli.main import _advance_one_system
        s = _make_system_dict(root)
        _advance_one_system(s, dry_run=False)
        out = capsys.readouterr().out
        assert "pipeline complete" in out

    def test_failed_status_prints_failed(
        self, tmp_path: Path, monkeypatch, capsys,
    ):
        root = self._make_ready_for_postprocess(tmp_path)
        monkeypatch.setattr(
            "vasp_sop.defect.analysis.analyze",
            lambda *a, **kw: "failed",
        )
        from vasp_sop.cli.main import _advance_one_system
        s = _make_system_dict(root)
        _advance_one_system(s, dry_run=False)
        out = capsys.readouterr().out
        assert "post-process failed" in out
        assert "pipeline complete" not in out
    def test_unitcell_build_failure_blocks_analysis(self, tmp_path: Path, monkeypatch):
        root = self._make_ready_for_postprocess(tmp_path)
        for child in (root / "defect").iterdir():
            if child.is_dir():
                (child / "calc_results.json").write_text("{}\n")
        analyze_calls = []

        def fail_build(uc_root, config):
            status_path = Path(uc_root) / "unitcell_build_status.json"
            status_path.write_text(
                '{"status": "failed", "reason": "zero_gap", '
                '"diagnostic": "zero band gap"}\n'
            )

        monkeypatch.setattr("vasp_sop.defect.unitcell.build_unitcell_yaml", fail_build)
        monkeypatch.setattr(
            "vasp_sop.defect.analysis.analyze",
            lambda *a, **kw: analyze_calls.append((a, kw)),
        )

        from vasp_sop.cli.main import _advance_one_system
        with pytest.raises(RuntimeError, match="unitcell blocked"):
            _advance_one_system(_make_system_dict(root), dry_run=False)

        assert analyze_calls == []
        assert (root / "unitcell" / "unitcell_build_status.json").is_file()


class TestDefectAnalyzeCLI:
    def test_analyze_invokes_pipeline(self, tmp_path: Path, monkeypatch, capsys):
        """vasp-sop defect analyze runs analyze() and prints status (#0014)."""
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")
        root = tmp_path / "GaN"
        root.mkdir()
        (root / "plan.yaml").write_text("formula: GaN\n")
        (root / "defect").mkdir()
        (root / "unitcell").mkdir()
        (root / "unitcell" / "unitcell.yaml").write_text("x: 1\n")
        (root / "cpd").mkdir()
        (root / "cpd" / "standard_energies.yaml").write_text("x: 1\n")
        (root / "cpd" / "target_vertices.yaml").write_text("target: GaN\n")

        called = {}

        def fake_analyze(df, proj, cfg, uy, se, tv):
            called["ok"] = True
            return "partial"

        monkeypatch.setattr("vasp_sop.defect.analysis.analyze", fake_analyze)
        monkeypatch.setattr(
            "vasp_sop.core.config.PipelineConfig.from_yaml",
            lambda *a, **kw: type("C", (), {"formula": "GaN"})(),
        )
        from vasp_sop.cli.main import _do_defect_analyze
        args = type("A", (), {"project_dir": root})()
        _do_defect_analyze(args)
        assert called.get("ok")
        assert "partial" in capsys.readouterr().out


class TestBatchRunLoopObservability:
    """Loop-mode batch runs configure logging and persist a cycle snapshot."""

    def _campaign(self, tmp_path: Path) -> Path:
        root = tmp_path / "campaign"
        system = root / "GaN"
        system.mkdir(parents=True)
        (system / "plan.yaml").write_text("formula: GaN\nfunctional: pbesol\n")
        (system / "defect").mkdir()
        (system / "cpd").mkdir()
        (system / "unitcell").mkdir()
        return root

    def test_loop_configures_logging_and_writes_cycle_snapshot(
        self, tmp_path: Path, monkeypatch,
    ):
        root = self._campaign(tmp_path)
        calls: dict[str, object] = {}

        class FakeSnapshotWriter:
            def __init__(self, observed_root: Path) -> None:
                calls["snapshot_root"] = observed_root

            def write(self, state: dict) -> None:
                calls["state"] = state

        monkeypatch.setattr(
            "vasp_sop.core.logging.setup_file_logging",
            lambda observed_root: calls.setdefault("logging_root", observed_root),
        )
        monkeypatch.setattr(
            "vasp_sop.core.snapshot.SnapshotWriter", FakeSnapshotWriter,
        )
        monkeypatch.setattr(
            "vasp_sop.core.config.PipelineConfig.from_yaml",
            lambda *args, **kwargs: type("Config", (), {
                "poscar_src": "", "formula": "GaN",
            })(),
        )
        monkeypatch.setattr("vasp_sop.cli.main._phase", lambda system: "COMPLETE")
        monkeypatch.setattr(
            "vasp_sop.defect.analysis.classify_analyze_status",
            lambda defect_root: "full",
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("crisp unavailable")),
        )

        from vasp_sop.cli.main import _batch_run
        _batch_run(root, dry_run=True, loop=True)

        assert calls["logging_root"] == root
        assert calls["snapshot_root"] == root
        assert calls["state"] == {
            "phases": {"COMPLETE": 1},
            "analyze": {"full": 1, "partial": 0, "failed": 0},
            "crisp_active": -1,
            "crisp_running": -1,
            "crisp_failed": -1,
            "errors": [],
        }

    def test_single_pass_does_not_enable_loop_observability(
        self, tmp_path: Path, monkeypatch,
    ):
        root = self._campaign(tmp_path)
        monkeypatch.setattr(
            "vasp_sop.core.config.PipelineConfig.from_yaml",
            lambda *args, **kwargs: type("Config", (), {
                "poscar_src": "", "formula": "GaN",
            })(),
        )
        monkeypatch.setattr("vasp_sop.cli.main._phase", lambda system: "COMPLETE")
        monkeypatch.setattr(
            "vasp_sop.core.logging.setup_file_logging",
            lambda root: (_ for _ in ()).throw(AssertionError("unexpected logging setup")),
        )
        monkeypatch.setattr(
            "vasp_sop.core.snapshot.SnapshotWriter",
            lambda root: (_ for _ in ()).throw(AssertionError("unexpected snapshot setup")),
        )

        from vasp_sop.cli.main import _batch_run
        _batch_run(root, dry_run=True, loop=False)

    def test_single_pass_logs_advance_exception_without_undefined_logger(
        self, tmp_path: Path, monkeypatch, capsys,
    ):
        root = self._campaign(tmp_path)
        monkeypatch.setattr(
            "vasp_sop.core.config.PipelineConfig.from_yaml",
            lambda *args, **kwargs: type("Config", (), {
                "poscar_src": "", "formula": "GaN",
            })(),
        )
        monkeypatch.setattr("vasp_sop.cli.main._phase", lambda system: "COMPETING")
        monkeypatch.setattr(
            "vasp_sop.cli.main._advance_one_system",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        from vasp_sop.cli.main import _batch_run
        _batch_run(root, dry_run=True, loop=False)

        assert "FAILED (boom)" in capsys.readouterr().out

    def test_loop_skips_failed_unitcell_system(self, tmp_path: Path, monkeypatch):
        root = self._campaign(tmp_path)
        status = root / "GaN" / "unitcell" / "unitcell_build_status.json"
        status.write_text(
            '{"status": "failed", "reason": "zero_gap", '
            '"diagnostic": "zero band gap"}\n'
        )
        monkeypatch.setattr(
            "vasp_sop.core.config.PipelineConfig.from_yaml",
            lambda *args, **kwargs: type("Config", (), {
                "poscar_src": "", "formula": "GaN",
            })(),
        )
        monkeypatch.setattr("vasp_sop.cli.main._phase", lambda system: "UNITCELL_DEFECT")
        advance_calls = []
        monkeypatch.setattr(
            "vasp_sop.cli.main._advance_one_system",
            lambda *args, **kwargs: advance_calls.append(args),
        )
        stop_calls = []

        def stop_once():
            stop_calls.append(True)
            assert len(stop_calls) == 1, "batch loop did not terminate blocked system"
            return False

        monkeypatch.setattr("vasp_sop.cli.main.is_stop_requested", stop_once)
        monkeypatch.setattr("vasp_sop.cli.main._crisp_active_dirs", lambda **kwargs: set())

        from vasp_sop.cli.main import _batch_run
        _batch_run(root, poll_interval=0, dry_run=True, loop=True)

        assert advance_calls == []
    def test_single_pass_reports_blocked_unitcell_summary(
        self, tmp_path: Path, monkeypatch, capsys,
    ):
        root = self._campaign(tmp_path)
        status = root / "GaN" / "unitcell" / "unitcell_build_status.json"
        status.write_text(
            '{"status": "failed", "reason": "zero_gap", '
            '"diagnostic": "zero band gap"}\n'
        )
        monkeypatch.setattr(
            "vasp_sop.core.config.PipelineConfig.from_yaml",
            lambda *args, **kwargs: type("Config", (), {
                "poscar_src": "", "formula": "GaN",
            })(),
        )
        monkeypatch.setattr("vasp_sop.cli.main._phase", lambda system: "UNITCELL_DEFECT")
        advance_calls = []
        monkeypatch.setattr(
            "vasp_sop.cli.main._advance_one_system",
            lambda *args, **kwargs: advance_calls.append(args),
        )

        from vasp_sop.cli.main import _batch_run
        _batch_run(root, dry_run=True, loop=False)

        assert advance_calls == []
        assert "0 running, 1 blocked, 1 remaining" in capsys.readouterr().out
    def test_loop_blocks_runtime_unitcell_failure_without_retry(
        self, tmp_path: Path, monkeypatch,
    ):
        root = self._campaign(tmp_path)
        status = root / "GaN" / "unitcell" / "unitcell_build_status.json"
        monkeypatch.setattr(
            "vasp_sop.core.config.PipelineConfig.from_yaml",
            lambda *args, **kwargs: type("Config", (), {
                "poscar_src": "", "formula": "GaN",
            })(),
        )
        monkeypatch.setattr("vasp_sop.cli.main._phase", lambda system: "UNITCELL_DEFECT")
        advance_calls = []

        def fail_advance(*args, **kwargs):
            advance_calls.append(args)
            status.write_text(
                '{"status": "failed", "reason": "zero_gap", '
                '"diagnostic": "zero band gap"}\n'
            )
            raise RuntimeError("unitcell build failed")

        monkeypatch.setattr("vasp_sop.cli.main._advance_one_system", fail_advance)
        stop_calls = []

        def stop_once():
            stop_calls.append(True)
            assert len(stop_calls) == 1, "runtime-blocked system was retried"
            return False

        monkeypatch.setattr("vasp_sop.cli.main.is_stop_requested", stop_once)
        monkeypatch.setattr("vasp_sop.cli.main._crisp_active_dirs", lambda **kwargs: set())

        from vasp_sop.cli.main import _batch_run
        _batch_run(root, poll_interval=0, dry_run=True, loop=True)

        assert len(advance_calls) == 1
        assert status.is_file()


    def test_empty_campaign_logs_warning(self, tmp_path: Path, caplog, capsys):
        from vasp_sop.cli.main import _batch_run

        _batch_run(tmp_path, loop=True)

        assert any(
            record.name == "vasp_sop.cli.main"
            and record.getMessage() == "No systems found."
            for record in caplog.records
        )
    def test_loop_writes_batch_log_file(self, tmp_path: Path, monkeypatch):
        """Loop mode creates batch_run.log via FileHandler."""
        root = self._campaign(tmp_path)
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")
        monkeypatch.setattr("vasp_sop.core.jobs.submit_vasp",
            lambda path: type("J",(),{"task_name":"T"}))

        from vasp_sop.cli.main import _batch_run
        _batch_run(root, poll_interval=99, dry_run=False, loop=True)

        log_path = root / "batch_run.log"
        assert log_path.is_file(), "batch_run.log should exist"






class TestHandleUnconvergedPoll:
    """_handle_unconverged_poll: CONTCAR restart without parameter changes."""

    def test_nsw_ibrion_preserved_on_restart(self, tmp_path: Path, monkeypatch):
        """CONTCAR→POSCAR must NOT rewrite NSW or IBRION (user policy)."""
        from vasp_sop.core.job_store import JobStore
        from vasp_sop.core.cache import override_cache_root

        override_cache_root(tmp_path / ".vasp_sop")

        # Set up a fresh INCAR with original relaxation params
        (tmp_path / "INCAR").write_text("NSW = 50\nIBRION = 2\nEDIFFG = -0.03\n")
        (tmp_path / "POSCAR").write_text("old\n")
        (tmp_path / "CONTCAR").write_text("contcar\n")
        (tmp_path / "POTCAR").write_text("p\n")
        (tmp_path / "KPOINTS").write_text("k\n")
        # Make OUTCAR appear finished but unconverged
        (tmp_path / "OUTCAR").write_text(
            " General timing and accounting informations for this job:\n"
            " TOTAL-FORCE (eV/Angst)\n ---\n 0.0 0.0 0.0 0.5 0.0 0.0\n"
        )

        # JobStore must have a "submitted" record for the path
        wd_str = str(tmp_path.resolve())
        js = JobStore()
        js.record(wd_str, "submitted", source="test", reason="restart,prev_f=0.5")
        js.track(wd_str)

        monkeypatch.setattr(
            "vasp_sop.core.jobs.submit_vasp",
            lambda path: type("Job", (), {"task_name": "fake"}),  # return fake job
        )

        from vasp_sop.cli.main import _handle_unconverged_poll
        _handle_unconverged_poll(tmp_path)

        # POSCAR replaced from CONTCAR
        assert (tmp_path / "POSCAR").read_text() == "contcar\n"
        # INCAR must keep original NSW and IBRION
        incar = (tmp_path / "INCAR").read_text()
        assert "NSW = 50" in incar, f"NSW modified: {incar}"
        assert "IBRION = 2" in incar, f"IBRION modified: {incar}"
        assert "EDIFFG = -0.03" in incar
        assert "ISTART = 1" in incar


class TestBatchGenerateInputsUnitcell:
    """_batch_generate_inputs --unitcell flag: post-handoff unitcell tasks."""

    def _make_sys(self, tmp_path: Path, formula: str = "GaN", mpid: str = "804"):
        d = tmp_path / formula
        d.mkdir()
        plan = {
            "project": {"formula": formula, "dopant_elements": [],
                         "poscar_src": f"MP mp-{mpid}"},
            "parameters": {"functional": "pbesol"},
            "supercell": {"tool": "doped", "min_distance": 10.0},
        }
        (d / "plan.yaml").write_text(yaml.dump(plan))
        cpd = d / "cpd"
        cpd.mkdir()
        td = cpd / f"{formula}_mp-{mpid}"
        td.mkdir()
        _write_poscar(td, 4)
        _write_incar(td)
        _write_potcar(td)
        _write_kpoints(td)
        return d, td

    def test_unitcell_skips_when_no_contcar(self, tmp_path, capsys, monkeypatch):
        d, _ = self._make_sys(tmp_path)
        from vasp_sop.cli.main import _batch_generate_inputs
        monkeypatch.setattr("vasp_sop.defect.unitcell._prepare_all_inputs",
                           lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not be called")))
        _batch_generate_inputs(tmp_path, unitcell=True)
        captured = capsys.readouterr().out
        assert "Skipped 1 systems" in captured

    def test_unitcell_generates_when_contcar_present(self, tmp_path, capsys, monkeypatch):
        d, td = self._make_sys(tmp_path)
        so = d / "unitcell" / "structure_opt"
        so.mkdir(parents=True)
        (so / "CONTCAR").write_text("optimized\n")
        (so / "POSCAR").write_text("initial\n")

        uc_calls = []
        monkeypatch.setattr("vasp_sop.defect.unitcell._prepare_all_inputs",
                           lambda *args: uc_calls.append(args))

        from vasp_sop.cli.main import _batch_generate_inputs
        _batch_generate_inputs(tmp_path, unitcell=True)
        captured = capsys.readouterr().out
        assert len(uc_calls) == 1
        assert uc_calls[0][1] == td  # target_dir
        assert "OK" in captured




class TestRunPipelineStartup:
    """_run_pipeline generates VASP inputs for canonical target directory."""

    def test_target_dir_gets_inputs_generated(self, tmp_path, monkeypatch):
        from vasp_sop.core.cache import override_cache_root
        override_cache_root(tmp_path / ".vasp_sop")

        root = tmp_path / "GaN"
        root.mkdir()
        cpd = root / "cpd"; cpd.mkdir()
        td = cpd / "GaN_mp-804"; td.mkdir()
        (td / "POSCAR").write_text("GaN\n1\n5 0 0\n0 5 0\n0 0 5\nGa N\n1 1\nDirect\n0 0 0\n0.5 0.5 0.5\n")
        (root / "plan.yaml").write_text(yaml.dump({
            "project": {"formula": "GaN", "dopant_elements": [], "poscar_src": "MP mp-804"},
            "parameters": {"functional": "pbesol"},
            "supercell": {"tool": "doped", "min_distance": 10.0}}))

        from vasp_sop.vasp.io import input_ready
        assert not input_ready(td), "target should lack inputs initially"

        from vasp_sop.cli.main import _batch_generate_inputs
        _batch_generate_inputs(tmp_path)

        assert input_ready(td), "target should have all inputs after generate"
