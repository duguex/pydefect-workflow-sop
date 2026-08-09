"""Tests for defect-list generation via pydefect ds (#40).

doped is used only for supercell construction (``supercell.tool: doped``);
charge-state prediction always goes through ``pydefect ds`` with dopants.
"""

import yaml

from vasp_sop.core.config import PipelineConfig
from vasp_sop.defect.builder import _generate_defect_list


class TestChargePredictionConfig:
    """Test config: charge-state kwargs removed, supercell.tool retained."""

    def test_default_supercell_tool_is_doped(self):
        """doped is the default supercell tool (expansion only)."""
        cfg = PipelineConfig(formula="GaN")
        assert cfg.supercell_tool == "doped"

    def test_no_charge_state_gen_kwargs(self):
        """charge_state_gen_kwargs (doped charge prediction) is gone."""
        cfg = PipelineConfig(formula="GaN")
        assert not hasattr(cfg, "charge_state_gen_kwargs")

    def test_from_plan_parses_supercell_tool(self):
        """from_plan reads supercell.tool (pydefect or doped)."""
        plan = {
            "project": {"formula": "GaN"},
            "supercell": {"tool": "pydefect", "min_distance": 10.0},
        }
        cfg = PipelineConfig.from_plan(plan)
        assert cfg.supercell_tool == "pydefect"

    def test_from_plan_defaults_supercell_tool_doped(self):
        """from_plan defaults supercell.tool to doped when absent."""
        plan = {"project": {"formula": "GaN"}}
        cfg = PipelineConfig.from_plan(plan)
        assert cfg.supercell_tool == "doped"

    def test_to_plan_roundtrip_supercell_tool(self):
        """to_plan serializes supercell.tool and omits use_doped."""
        cfg = PipelineConfig(formula="GaN")
        plan = cfg.to_plan()
        assert plan["supercell"]["tool"] == "doped"
        assert "use_doped" not in plan["defects"]
        assert "probability_threshold" not in plan["defects"]


class TestDefectListGeneration:
    """Defect-list generation always runs pydefect ds (with dopants)."""

    def test_pydefect_ds_called_without_dopants(self, tmp_path, monkeypatch):
        """No dopants: pydefect ds is called without -d."""
        defect_root = tmp_path / "defect"
        defect_root.mkdir()

        cfg = PipelineConfig(formula="GaN", root=tmp_path)

        calls = []

        def fake_run_local(cmd, **kwargs):
            calls.append(cmd)
            (defect_root / "defect_in.yaml").write_text(
                yaml.dump({"V_Ga": [-3, -2, -1, 0, 1]})
            )

        monkeypatch.setattr(
            "vasp_sop.defect.pydefect_adapter.run_local", fake_run_local
        )

        _generate_defect_list(defect_root, cfg)

        assert len(calls) == 1
        assert "pydefect ds" in calls[0]
        assert "-d" not in calls[0]
        assert (defect_root / "defect_in.yaml").is_file()

    def test_pydefect_ds_called_with_dopants(self, tmp_path, monkeypatch):
        """Dopants: pydefect ds is called with -d <dopants> (doped defects)."""
        defect_root = tmp_path / "defect"
        defect_root.mkdir()

        cfg = PipelineConfig(formula="GaN", root=tmp_path)
        cfg.dopant_elements = ["Bi"]

        calls = []

        def fake_run_local(cmd, **kwargs):
            calls.append(cmd)
            (defect_root / "defect_in.yaml").write_text(
                yaml.dump({"Bi_Ga1": [0], "V_Ga": [-3, 0]})
            )

        monkeypatch.setattr(
            "vasp_sop.defect.pydefect_adapter.run_local", fake_run_local
        )

        _generate_defect_list(defect_root, cfg)

        assert len(calls) == 1
        assert "pydefect ds" in calls[0]
        assert "-d Bi" in calls[0]

    def test_skips_when_defect_in_exists(self, tmp_path, monkeypatch):
        """When defect_in.yaml already exists, generation is skipped."""
        defect_root = tmp_path / "defect"
        defect_root.mkdir()
        (defect_root / "defect_in.yaml").write_text("V_Ga: [-3, 0]\n")

        cfg = PipelineConfig(formula="GaN", root=tmp_path)

        calls = []
        monkeypatch.setattr(
            "vasp_sop.defect.pydefect_adapter.run_local",
            lambda cmd, **kw: calls.append(cmd),
        )

        _generate_defect_list(defect_root, cfg)
        assert len(calls) == 0
