"""Tests for vasp_sop.defect.pydefect_adapter — pydefect CLI wrapper (#103)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vasp_sop.defect import pydefect_adapter as pa


# ── calc_results ────────────────────────────────────────────────────────────

class TestCalcResults:
    def test_reads_existing_calc_results(self, tmp_path: Path, monkeypatch):
        d = tmp_path / "Va_Ga_0"
        d.mkdir()
        cr = {"energy": -100.5}
        (d / "calc_results.json").write_text(json.dumps(cr))

        # run_local should NOT be called
        calls = []
        monkeypatch.setattr(pa, "run_local", lambda *a, **kw: calls.append(a))

        result = pa.calc_results([d], cwd=tmp_path)
        assert result == [cr]
        assert calls == []

    def test_runs_cr_when_no_calc_results(self, tmp_path: Path, monkeypatch):
        d = tmp_path / "Va_Ga_0"
        d.mkdir()
        cr = {"energy": -200.0}

        def fake_run(cmd, cwd, **kw):
            # Simulate pydefect_vasp cr writing the file
            (d / "calc_results.json").write_text(json.dumps(cr))

        monkeypatch.setattr(pa, "run_local", fake_run)
        result = pa.calc_results([d], cwd=tmp_path)
        assert result == [cr]

    def test_skips_on_failure(self, tmp_path: Path, monkeypatch):
        d = tmp_path / "Va_Ga_0"
        d.mkdir()

        def fake_run(cmd, cwd, **kw):
            raise RuntimeError("pydefect crashed")

        monkeypatch.setattr(pa, "run_local", fake_run)
        result = pa.calc_results([d], cwd=tmp_path)
        assert result == []

    def test_multiple_dirs(self, tmp_path: Path, monkeypatch):
        dirs = []
        for i in range(3):
            d = tmp_path / f"Va_Ga_{i}"
            d.mkdir()
            (d / "calc_results.json").write_text(json.dumps({"i": i}))
            dirs.append(d)

        monkeypatch.setattr(pa, "run_local", lambda *a, **kw: None)
        result = pa.calc_results(dirs, cwd=tmp_path)
        assert [r["i"] for r in result] == [0, 1, 2]


# ── efnv ────────────────────────────────────────────────────────────────────

class TestEfnv:
    def test_reads_existing_correction(self, tmp_path: Path, monkeypatch):
        d = tmp_path / "Va_Ga_0"
        d.mkdir()
        corr = {"correction": 0.123}
        (d / "correction.json").write_text(json.dumps(corr))

        calls = []
        monkeypatch.setattr(pa, "run_local", lambda *a, **kw: calls.append(a))

        pcr = tmp_path / "perfect_cr.json"
        uc = tmp_path / "unitcell.yaml"
        result = pa.efnv([d], cwd=tmp_path, perfect_calc_results=pcr, unitcell_yaml=uc)
        assert result == [corr]
        assert calls == []

    def test_runs_efnv_when_no_correction(self, tmp_path: Path, monkeypatch):
        d = tmp_path / "Va_Ga_0"
        d.mkdir()
        corr = {"correction": 0.456}

        def fake_run(cmd, cwd, **kw):
            (d / "correction.json").write_text(json.dumps(corr))

        monkeypatch.setattr(pa, "run_local", fake_run)
        pcr = tmp_path / "perfect_cr.json"
        uc = tmp_path / "unitcell.yaml"
        result = pa.efnv([d], cwd=tmp_path, perfect_calc_results=pcr, unitcell_yaml=uc)
        assert result == [corr]

    def test_skips_on_failure(self, tmp_path: Path, monkeypatch):
        d = tmp_path / "Va_Ga_0"
        d.mkdir()

        def fake_run(cmd, cwd, **kw):
            raise RuntimeError("efnv failed")

        monkeypatch.setattr(pa, "run_local", fake_run)
        pcr = tmp_path / "perfect_cr.json"
        uc = tmp_path / "unitcell.yaml"
        result = pa.efnv([d], cwd=tmp_path, perfect_calc_results=pcr, unitcell_yaml=uc)
        assert result == []

    def test_command_includes_pcr_and_u(self, tmp_path: Path, monkeypatch):
        d = tmp_path / "Va_Ga_0"
        d.mkdir()
        pcr = tmp_path / "perfect" / "calc_results.json"
        uc = tmp_path / "unitcell.yaml"

        cmds = []

        def fake_run(cmd, cwd, **kw):
            cmds.append(cmd)
            (d / "correction.json").write_text("{}")

        monkeypatch.setattr(pa, "run_local", fake_run)
        pa.efnv([d], cwd=tmp_path, perfect_calc_results=pcr, unitcell_yaml=uc)
        assert len(cmds) == 1
        assert "efnv" in cmds[0]
        assert str(pcr) in cmds[0]
        assert str(uc) in cmds[0]


# ── defect_energy_summary ───────────────────────────────────────────────────

class TestDefectEnergySummary:
    def test_reads_existing_summary(self, tmp_path: Path, monkeypatch):
        summary = {"defects": [{"name": "Va_Ga_0", "energy": 1.5}]}
        (tmp_path / "defect_energy_summary.json").write_text(json.dumps(summary))

        calls = []
        monkeypatch.setattr(pa, "run_local", lambda *a, **kw: calls.append(a))

        d = tmp_path / "Va_Ga_0"
        d.mkdir()
        result = pa.defect_energy_summary(
            cwd=tmp_path,
            dirs=[d],
            unitcell_yaml=tmp_path / "uc.yaml",
            perfect_band_edge_state=tmp_path / "pbes.json",
            target_vertices=tmp_path / "tv.yaml",
        )
        assert result == summary
        assert calls == []

    def test_runs_des_when_no_summary(self, tmp_path: Path, monkeypatch):
        summary = {"defects": []}
        d = tmp_path / "Va_Ga_0"
        d.mkdir()

        def fake_run(cmd, cwd, **kw):
            (tmp_path / "defect_energy_summary.json").write_text(json.dumps(summary))

        monkeypatch.setattr(pa, "run_local", fake_run)
        result = pa.defect_energy_summary(
            cwd=tmp_path,
            dirs=[d],
            unitcell_yaml=tmp_path / "uc.yaml",
            perfect_band_edge_state=tmp_path / "pbes.json",
            target_vertices=tmp_path / "tv.yaml",
        )
        assert result == summary

    def test_returns_none_on_failure(self, tmp_path: Path, monkeypatch):
        d = tmp_path / "Va_Ga_0"
        d.mkdir()

        def fake_run(cmd, cwd, **kw):
            raise RuntimeError("des failed")

        monkeypatch.setattr(pa, "run_local", fake_run)
        result = pa.defect_energy_summary(
            cwd=tmp_path,
            dirs=[d],
            unitcell_yaml=tmp_path / "uc.yaml",
            perfect_band_edge_state=tmp_path / "pbes.json",
            target_vertices=tmp_path / "tv.yaml",
        )
        assert result is None

    def test_returns_none_when_no_dirs(self, tmp_path: Path, monkeypatch):
        calls = []
        monkeypatch.setattr(pa, "run_local", lambda *a, **kw: calls.append(a))
        result = pa.defect_energy_summary(
            cwd=tmp_path,
            dirs=[],
            unitcell_yaml=tmp_path / "uc.yaml",
            perfect_band_edge_state=tmp_path / "pbes.json",
            target_vertices=tmp_path / "tv.yaml",
        )
        assert result is None
        assert calls == []

    def test_command_includes_all_args(self, tmp_path: Path, monkeypatch):
        d = tmp_path / "Va_Ga_0"
        d.mkdir()
        uc = tmp_path / "uc.yaml"
        pbes = tmp_path / "pbes.json"
        tv = tmp_path / "tv.yaml"

        cmds = []

        def fake_run(cmd, cwd, **kw):
            cmds.append(cmd)
            (tmp_path / "defect_energy_summary.json").write_text("{}")

        monkeypatch.setattr(pa, "run_local", fake_run)
        pa.defect_energy_summary(
            cwd=tmp_path, dirs=[d],
            unitcell_yaml=uc, perfect_band_edge_state=pbes, target_vertices=tv,
        )
        assert len(cmds) == 1
        cmd = cmds[0]
        assert "des" in cmd
        assert "Va_Ga_0" in cmd
        assert str(uc) in cmd
        assert str(pbes) in cmd
        assert str(tv) in cmd




class TestOverrideIonicConv:
    def test_patches_when_false_and_converged(self, tmp_path: Path):
        d = tmp_path / "Va_Ba_0"
        d.mkdir()
        (d / "calc_results.json").write_text(json.dumps({"ionic_conv": False, "e": -10}))
        check_calls = []
        orig = pa.check_converged
        pa.check_converged = lambda p: True
        try:
            data = json.loads((d / "calc_results.json").read_text())
            result = pa._override_ionic_conv(d, data)
        finally:
            pa.check_converged = orig
        assert result is True
        written = json.loads((d / "calc_results.json").read_text())
        assert written["ionic_conv"] is True
        assert written["e"] == -10

    def test_noop_when_already_true(self, tmp_path: Path):
        d = tmp_path / "Va_Ba_0"
        d.mkdir()
        (d / "calc_results.json").write_text(json.dumps({"ionic_conv": True, "e": -10}))
        orig = pa.check_converged
        pa.check_converged = lambda p: True
        try:
            data = json.loads((d / "calc_results.json").read_text())
            result = pa._override_ionic_conv(d, data)
        finally:
            pa.check_converged = orig
        assert result is False
        written = json.loads((d / "calc_results.json").read_text())
        assert written["ionic_conv"] is True

    def test_noop_when_unconverged_dir(self, tmp_path: Path):
        d = tmp_path / "Va_Ba_0"
        d.mkdir()
        (d / "calc_results.json").write_text(json.dumps({"ionic_conv": False, "e": -10}))
        orig = pa.check_converged
        pa.check_converged = lambda p: False
        try:
            data = json.loads((d / "calc_results.json").read_text())
            result = pa._override_ionic_conv(d, data)
        finally:
            pa.check_converged = orig
        assert result is False
        written = json.loads((d / "calc_results.json").read_text())
        assert written["ionic_conv"] is False