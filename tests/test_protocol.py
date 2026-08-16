"""Tests for vasp_sop.vasp.protocol — the single source of protocol truth
(ADR 0024).

Guards the contract: every leg's VASP parameters, the ENCUT rule, and the
initial-moment table live here and nowhere else.
"""

import pytest

from vasp_sop.vasp.protocol import (
    INITIAL_MAGMOM,
    LEG_PROTOCOL,
    TASK_TO_LEG,
    U_TABLE,
    effective_encut,
    encut_for_potcar,
    magmom_values,
    protocol_tags,
)


class FakeConfig:
    """Mirror of PipelineConfig's encut attribute (no vasp_sop import)."""

    def __init__(self, encut=None):
        self.encut = encut


class TestLegProtocol:
    def test_relax_legs_declare_ediiffg(self):
        # 离子弛豫腿必须声明 EDIFFG(-0.01, 2026-08-11 决定)——缺失=回退
        # vise 模板 -0.005(批次漂移根因)。
        for leg in ("defect", "structure_opt", "cpd"):
            assert leg in LEG_PROTOCOL
            assert LEG_PROTOCOL[leg]["EDIFFG"] == -0.01, leg

    def test_defect_leg_is_the_only_sigma_lorbit_leg(self):
        assert LEG_PROTOCOL["defect"]["SIGMA"] == 0.02
        assert LEG_PROTOCOL["defect"]["LORBIT"] == 11

    def test_singlepoint_legs_do_not_declare_relax_tags(self):
        # band/dos/dielectric 单点/DFPT:不声明 NSW 与 EDIFFG(留给模板),
        # 只约束电子层——避免伪装成弛豫腿。
        for leg in ("band", "dos", "dielectric"):
            assert "EDIFFG" not in LEG_PROTOCOL[leg], leg
            assert LEG_PROTOCOL[leg]["EDIFF"] == "1e-4", leg

    def test_task_mapping_defaults_to_structure_opt(self):
        # prepare_inputs 无 task_type 时的默认 = 弛豫腿(unitcell structure_opt)。
        assert TASK_TO_LEG[""] == "structure_opt"
        assert TASK_TO_LEG["defect"] == "defect"
        assert TASK_TO_LEG["dielectric"] == "dielectric"

    def test_protocol_tags_respects_leg_map(self):
        assert protocol_tags("defect") == LEG_PROTOCOL["defect"]
        assert protocol_tags("") == LEG_PROTOCOL["structure_opt"]
        # 未知名 → 原样(空协议 → 无覆盖)。
        assert protocol_tags("unknown_leg") == {}


class TestUTable:
    def test_ti_is_present(self):
        # libs/vise fork 缺 Ti——补丁必须兜底(2026 批次统一 U=4)。
        assert U_TABLE["Ti"] == (4.0, 2)

    def test_gd_is_5_0_l3(self):
        assert U_TABLE["Gd"] == (5.0, 3)

    def test_initial_moments_cover_u_magnetic_elements(self):
        # 真磁性 U 元素应有初始磁矩(Fe 沿用 5.0;Gd=7 修 #151 塌缩)。
        assert INITIAL_MAGMOM["Fe"] == 5.0
        assert INITIAL_MAGMOM["Gd"] == 7.0
        assert INITIAL_MAGMOM["Mn"] == 5.0
        # Ti(d0)/Cu(d9 弱)/Zn(d10) 不进磁矩表——写了会误导 SCF。
        for el in ("Ti", "Cu", "Zn"):
            assert el not in INITIAL_MAGMOM, el


class TestEncut:
    def test_encut_for_potcar_takes_13x_max_enmax(self, tmp_path):
        potcar = tmp_path / "POTCAR"
        # 两段:第一段 ENMAX=200,第二段 ENMAX=400 → 取大的 ×1.3。
        potcar.write_text(
            "ENMAX = 200.0\n" + "x" * 80 + "\n" + "ENMAX = 400.0\n"
        )
        assert encut_for_potcar(potcar) == pytest.approx(520.0)

    def test_encut_for_missing_potcar_none(self, tmp_path):
        assert encut_for_potcar(tmp_path / "nope") is None

    def test_effective_encut_prefers_config(self, tmp_path):
        potcar = tmp_path / "POTCAR"
        potcar.write_text("ENMAX = 400.0\n")
        assert effective_encut(FakeConfig(encut=500.0), tmp_path) == 500.0

    def test_effective_encut_falls_back_to_dir_potcar(self, tmp_path):
        potcar = tmp_path / "POTCAR"
        potcar.write_text("ENMAX = 400.0\n")
        assert effective_encut(FakeConfig(), tmp_path) == pytest.approx(520.0)

    def test_effective_encut_no_potcar_none(self, tmp_path):
        assert effective_encut(FakeConfig(), tmp_path) is None


class TestMagmom:
    def test_values_in_poscar_order(self):
        # 每原子一个值,非磁元素 0.0(与 POSCAR 行序对应,VASP 要求)。
        assert magmom_values(["Gd", "O", "Gd"]) == [7.0, 0.0, 7.0]
        assert magmom_values(["Fe", "O"]) == [5.0, 0.0]

    def test_no_magnetic_element_returns_none(self):
        assert magmom_values([]) is None
        assert magmom_values(["O", "Ba", "Si"]) is None

    def test_3d_unified_at_5(self):
        # 3d 统一高自旋 5(2026-08-16 决策)——初始磁矩是 SCF 起点,
        # Mn/Fe/Cr/Co/Ni 全部 5;Ti(d⁰)/Cu(d⁹ 弱)/Zn(d¹⁰) 不进表。
        for el in ("Cr", "Mn", "Fe", "Co", "Ni"):
            assert INITIAL_MAGMOM[el] == 5.0, el
        assert magmom_values(["Cr", "O"]) == [5.0, 0.0]
        for el in ("Ti", "Cu", "Zn"):
            assert el not in INITIAL_MAGMOM, el

    def test_ti_alone_no_moment(self):
        # Ti4+ d0 无磁——magmom_values 不得因 Ti 返回非 None。
        assert magmom_values(["Ti", "O"]) is None
