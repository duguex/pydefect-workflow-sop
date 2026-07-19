"""Tests for doped charge state prediction with pydefect fallback (#40)."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from vasp_sop.core.config import PipelineConfig
from vasp_sop.defect.builder import _generate_defect_list


class TestChargePredictionConfig:
    """Test config parsing for charge_state_gen_kwargs."""

    def test_default_config_values(self):
        """Default charge_state_gen_kwargs has expected keys and values."""
        cfg = PipelineConfig(formula="GaN")
        assert cfg.charge_state_gen_kwargs["probability_threshold"] == 0.0075
        assert cfg.charge_state_gen_kwargs["padding"] == 1
        assert cfg.charge_state_gen_kwargs["use_doped"] is True

    def test_from_plan_parses_charge_kwargs(self):
        """from_plan reads probability_threshold, padding, use_doped from defects."""
        plan = {
            "project": {"formula": "GaN"},
            "defects": {
                "probability_threshold": 0.01,
                "padding": 2,
                "use_doped": False,
            },
        }
        cfg = PipelineConfig.from_plan(plan)
        assert cfg.charge_state_gen_kwargs["probability_threshold"] == 0.01
        assert cfg.charge_state_gen_kwargs["padding"] == 2
        assert cfg.charge_state_gen_kwargs["use_doped"] is False

    def test_from_plan_defaults_when_missing(self):
        """from_plan uses defaults when charge kwargs are absent."""
        plan = {"project": {"formula": "GaN"}}
        cfg = PipelineConfig.from_plan(plan)
        assert cfg.charge_state_gen_kwargs["probability_threshold"] == 0.0075
        assert cfg.charge_state_gen_kwargs["padding"] == 1
        assert cfg.charge_state_gen_kwargs["use_doped"] is True

    def test_to_plan_roundtrip(self):
        """to_plan serializes charge_state_gen_kwargs into defects section."""
        cfg = PipelineConfig(formula="GaN")
        cfg.charge_state_gen_kwargs = {
            "probability_threshold": 0.02,
            "padding": 3,
            "use_doped": False,
        }
        plan = cfg.to_plan()
        assert plan["defects"]["probability_threshold"] == 0.02
        assert plan["defects"]["padding"] == 3
        assert plan["defects"]["use_doped"] is False


class TestChargePredictionFallback:
    """Test fallback to pydefect when doped is not installed."""

    def test_fallback_when_doped_import_fails(self, tmp_path, monkeypatch):
        """When doped.generation raises ImportError, falls back to pydefect ds."""
        defect_root = tmp_path / "defect"
        defect_root.mkdir()

        cfg = PipelineConfig(formula="GaN", root=tmp_path)
        cfg.charge_state_gen_kwargs["use_doped"] = True

        # Mock the import to fail
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "doped.generation" or (
                name == "doped" and args and "generation" in str(args)
            ):
                raise ImportError("No module named 'doped.generation'")
            return real_import(name, *args, **kwargs)

        # Track that run_local was called (pydefect fallback)
        calls = []

        def fake_run_local(cmd, **kwargs):
            calls.append(cmd)
            # Create defect_in.yaml as pydefect ds would
            defect_in = defect_root / "defect_in.yaml"
            defect_in.write_text(yaml.dump({"V_Ga": [-3, -2, -1, 0, 1]}))

        monkeypatch.setattr("vasp_sop.defect.builder.run_local", fake_run_local)
        monkeypatch.setattr("builtins.__import__", mock_import)

        _generate_defect_list(defect_root, cfg)

        # Verify pydefect ds was called
        assert len(calls) == 1
        assert "pydefect ds" in calls[0]
        assert (defect_root / "defect_in.yaml").is_file()

    def test_use_doped_false_goes_directly_to_pydefect(self, tmp_path, monkeypatch):
        """When use_doped=False, pydefect ds is used without attempting doped import."""
        defect_root = tmp_path / "defect"
        defect_root.mkdir()

        cfg = PipelineConfig(formula="GaN", root=tmp_path)
        cfg.charge_state_gen_kwargs["use_doped"] = False

        calls = []

        def fake_run_local(cmd, **kwargs):
            calls.append(cmd)
            defect_in = defect_root / "defect_in.yaml"
            defect_in.write_text(yaml.dump({"V_N": [-1, 0, 1]}))

        monkeypatch.setattr("vasp_sop.defect.builder.run_local", fake_run_local)

        _generate_defect_list(defect_root, cfg)

        assert len(calls) == 1
        assert "pydefect ds" in calls[0]

    def test_skips_when_defect_in_exists(self, tmp_path, monkeypatch):
        """When defect_in.yaml already exists, generation is skipped."""
        defect_root = tmp_path / "defect"
        defect_root.mkdir()
        (defect_root / "defect_in.yaml").write_text("V_Ga: [-3, 0]\n")

        cfg = PipelineConfig(formula="GaN", root=tmp_path)

        calls = []
        monkeypatch.setattr(
            "vasp_sop.defect.builder.run_local",
            lambda cmd, **kw: calls.append(cmd),
        )

        _generate_defect_list(defect_root, cfg)
        assert len(calls) == 0


class TestChargePredictionDoped:
    """Test doped charge state prediction path."""

    def test_doped_writes_defect_in_yaml(self, tmp_path, monkeypatch):
        """When doped is available, it writes defect_in.yaml with predicted charges."""
        defect_root = tmp_path / "defect"
        defect_root.mkdir()

        # Create a minimal supercell_info.json
        sc_info = {"structure": {}, "sites": []}
        (defect_root / "supercell_info.json").write_text(json.dumps(sc_info))

        cfg = PipelineConfig(formula="GaN", root=tmp_path)
        cfg.charge_state_gen_kwargs["use_doped"] = True
        cfg.charge_state_gen_kwargs["probability_threshold"] = 0.01
        cfg.charge_state_gen_kwargs["padding"] = 2

        # Mock doped.generation module
        mock_generation = MagicMock()
        mock_generation.guess_defect_charge_states.return_value = {
            "V_Ga": [-3, -2, -1, 0, 1],
            "V_N": [-1, 0, 1, 2, 3],
        }
        mock_generation.get_vacancy_charge_states = MagicMock()

        import sys
        monkeypatch.setitem(sys.modules, "doped", MagicMock())
        monkeypatch.setitem(sys.modules, "doped.generation", mock_generation)

        _generate_defect_list(defect_root, cfg)

        defect_in = defect_root / "defect_in.yaml"
        assert defect_in.is_file()

        with open(defect_in) as f:
            data = yaml.safe_load(f)

        assert data["V_Ga"] == [-3, -2, -1, 0, 1]
        assert data["V_N"] == [-1, 0, 1, 2, 3]

        # Verify doped was called with correct kwargs
        mock_generation.guess_defect_charge_states.assert_called_once_with(
            sc_info_path=str(defect_root / "supercell_info.json"),
            probability_threshold=0.01,
            padding=2,
        )

    def test_doped_failure_falls_back_to_pydefect(self, tmp_path, monkeypatch):
        """When doped's guess_defect_charge_states raises, falls back to pydefect."""
        defect_root = tmp_path / "defect"
        defect_root.mkdir()

        sc_info = {"structure": {}, "sites": []}
        (defect_root / "supercell_info.json").write_text(json.dumps(sc_info))

        cfg = PipelineConfig(formula="GaN", root=tmp_path)
        cfg.charge_state_gen_kwargs["use_doped"] = True

        mock_generation = MagicMock()
        mock_generation.guess_defect_charge_states.side_effect = RuntimeError(
            "doped internal error"
        )
        mock_generation.get_vacancy_charge_states = MagicMock()

        import sys
        monkeypatch.setitem(sys.modules, "doped", MagicMock())
        monkeypatch.setitem(sys.modules, "doped.generation", mock_generation)

        calls = []

        def fake_run_local(cmd, **kwargs):
            calls.append(cmd)
            (defect_root / "defect_in.yaml").write_text(
                yaml.dump({"V_Ga": [-3, 0]})
            )

        monkeypatch.setattr("vasp_sop.defect.builder.run_local", fake_run_local)

        _generate_defect_list(defect_root, cfg)

        # Should have fallen back to pydefect
        assert len(calls) == 1
        assert "pydefect ds" in calls[0]

    def test_doped_missing_supercell_info_falls_back(self, tmp_path, monkeypatch):
        """When supercell_info.json is missing, falls back to pydefect ds."""
        defect_root = tmp_path / "defect"
        defect_root.mkdir()
        # No supercell_info.json

        cfg = PipelineConfig(formula="GaN", root=tmp_path)
        cfg.charge_state_gen_kwargs["use_doped"] = True

        mock_generation = MagicMock()
        mock_generation.guess_defect_charge_states = MagicMock()
        mock_generation.get_vacancy_charge_states = MagicMock()

        import sys
        monkeypatch.setitem(sys.modules, "doped", MagicMock())
        monkeypatch.setitem(sys.modules, "doped.generation", mock_generation)

        calls = []

        def fake_run_local(cmd, **kwargs):
            calls.append(cmd)
            (defect_root / "defect_in.yaml").write_text(
                yaml.dump({"V_Ga": [-3, 0]})
            )

        monkeypatch.setattr("vasp_sop.defect.builder.run_local", fake_run_local)

        _generate_defect_list(defect_root, cfg)

        assert len(calls) == 1
        assert "pydefect ds" in calls[0]
