"""协议生成测试(ADR 0024):生成器按 LEG_PROTOCOL 产出 INCAR。

覆盖 CLI(io.prepare_inputs 命令行)与 API(defect charge)两条生成路径,
断言落盘/传给 vise 的参数与协议单一事实源一致——不运行 VASP。
"""

from pathlib import Path
from types import SimpleNamespace

from pymatgen.core import Lattice, Structure

from vasp_sop.vasp import io as io_mod
from vasp_sop.vasp.protocol import LEG_PROTOCOL


# 一条含磁性 + U 元素的 POSCAR(宿主 SrFeO3 简化),用于 CLI/API 生成。
def _magnetic_structure(tmp_path: Path) -> None:
    struct = Structure(Lattice.cubic(5.0), ["Sr", "Fe", "O"],
                       [[0, 0, 0], [0.5, 0.5, 0.5], [0.25, 0.25, 0.25]])
    struct.to(filename=str(tmp_path / "POSCAR"))


def _gd_structure(tmp_path: Path) -> None:
    struct = Structure(Lattice.cubic(5.0), ["O", "Gd", "O"],
                       [[0, 0, 0], [0.5, 0.5, 0.5], [0.25, 0.25, 0.25]])
    struct.to(filename=str(tmp_path / "POSCAR"))


def _cfg(**kw) -> SimpleNamespace:
    base = dict(soc=False, stage2_soc=False, functional="pbesol",
                potcar_overrides=[], encut=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _run_local_capture(monkeypatch) -> list[str]:
    """捕获 prepare_inputs CLI 路径拼给 vise 的完整命令。"""
    calls: list[str] = []

    def fake_run_local(cmd, **kw):
        calls.append(cmd)

    monkeypatch.setattr(io_mod, "run_local", fake_run_local)
    return calls


class TestCliProtocolTags:
    """CLI 路径:准备的 -uis 串与落盘 fallback 都来自协议表。"""

    def test_structure_opt_uis_has_full_leg(self, tmp_path, monkeypatch):
        _magnetic_structure(tmp_path)
        (tmp_path / "INCAR").write_text("NSW = 50\nNELM = 100\n")
        for f in ("POSCAR", "POTCAR", "KPOINTS"):
            if not (tmp_path / f).exists():
                (tmp_path / f).write_text("x\n")
        monkeypatch.setattr(io_mod, "input_ready", lambda d: False)
        calls = _run_local_capture(monkeypatch)
        io_mod.prepare_inputs(tmp_path, _cfg(), task_type="structure_opt")
        uis = next(c for c in calls if "-uis" in c).split("-uis ", 1)[1]
        tags = LEG_PROTOCOL["structure_opt"]
        assert "NSW 50" in uis and "NELM 50" in uis
        assert "EDIFF 1e-4" in uis and "EDIFFG -0.01" in uis
        # fallback:落盘 INCAR 也一致(即便 mocked vise 不写)。
        txt = (tmp_path / "INCAR").read_text()
        for k, v in tags.items():
            assert f"{k} = {v}" in txt, (k, v)

    def test_empty_task_type_is_structure_opt(self, tmp_path, monkeypatch):
        # cpd / unitcell-structure_opt 走 task_type="" 或显式 structure_opt,
        # 必须落到同一弛豫腿协议。
        _magnetic_structure(tmp_path)
        (tmp_path / "INCAR").write_text("NSW = 50\nNELM = 100\n")
        for f in ("POSCAR", "POTCAR", "KPOINTS"):
            if not (tmp_path / f).exists():
                (tmp_path / f).write_text("x\n")
        monkeypatch.setattr(io_mod, "input_ready", lambda d: False)
        calls = _run_local_capture(monkeypatch)
        io_mod.prepare_inputs(tmp_path, _cfg(), task_type="")
        uis = next(c for c in calls if "-uis" in c)
        assert "EDIFFG -0.01" in uis

    def test_singlepoint_legs_do_not_inject_ediffg(self, tmp_path, monkeypatch):
        # band/dos 单点:协议表不声明 EDIFFG——生成器不得把弛豫腿的
        # EDIFFG 灌注给它们(历史 -uis 无条件拼接 bug 的回归守门)。
        for leg in ("band", "dos"):
            _magnetic_structure(tmp_path)
            (tmp_path / "INCAR").write_text("NSW = 0\n")
            for f in ("POSCAR", "POTCAR", "KPOINTS"):
                if not (tmp_path / f).exists():
                    (tmp_path / f).write_text("x\n")
            monkeypatch.setattr(io_mod, "input_ready", lambda d: False)
            calls = _run_local_capture(monkeypatch)
            io_mod.prepare_inputs(tmp_path, _cfg(), task_type=leg)
            uis = next(c for c in calls if "-uis" in c)
            assert "EDIFFG" not in uis, leg
            assert "NELM 50" in uis and "EDIFF 1e-4" in uis

    def test_gd_cli_path_writes_magmom7(self, tmp_path, monkeypatch):
        # 协议化 MAGMOM(Gd=7, issue #151):CLI 生成落盘应有初始磁矩。
        _gd_structure(tmp_path)
        (tmp_path / "INCAR").write_text("NSW = 50\nNELM = 100\nISPIN = 2\n")
        for f in ("POSCAR", "POTCAR", "KPOINTS"):
            if not (tmp_path / f).exists():
                (tmp_path / f).write_text("x\n")
        monkeypatch.setattr(io_mod, "input_ready", lambda d: False)
        _run_local_capture(monkeypatch)
        io_mod.prepare_inputs(tmp_path, _cfg(), task_type="structure_opt")
        txt = (tmp_path / "INCAR").read_text()
        assert "MAGMOM = 0.0 7.0 0.0" in txt, txt

    def test_encut_injected_from_dir_potcar(self, tmp_path, monkeypatch):
        # ENCUT 永不落入 vise 模板默认:目录 POTCAR 的 1.3×max(ENMAX)。
        _magnetic_structure(tmp_path)
        (tmp_path / "POTCAR").write_text("ENMAX = 400.0\n")
        (tmp_path / "INCAR").write_text("NSW = 50\nNELM = 100\n")
        (tmp_path / "KPOINTS").write_text("x\n")
        monkeypatch.setattr(io_mod, "input_ready", lambda d: False)
        calls = _run_local_capture(monkeypatch)
        io_mod.prepare_inputs(tmp_path, _cfg(), task_type="structure_opt")
        uis = next(c for c in calls if "-uis" in c)
        assert "ENCUT 520.0" in uis, uis


class TestDefectApiProtocol:
    """API 路径(defect charge):overrides 全来自 defect 腿协议表。"""

    def test_api_incar_matches_defect_leg(self, tmp_path):
        _magnetic_structure(tmp_path)
        io_mod.prepare_inputs(tmp_path, _cfg(), kspacing=0.1,
                              task_type="defect", charge=0.0)
        txt = (tmp_path / "INCAR").read_text()
        tags = LEG_PROTOCOL["defect"]
        for k, v in tags.items():
            # LORBIT 数值类型(str 化后比较宽松)。
            assert f"{k} = {v}" in txt or f"{k} = {float(v):g}" in txt, (k, v, txt)
        # 新统一 EDIFFG(此前 defect 无——协议化消除与 cpd 的不一致)。
        assert "EDIFFG = -0.01" in txt
        assert "SIGMA = 0.02" in txt and "LORBIT = 11" in txt

    def test_api_encut_from_potcar(self, tmp_path):
        _magnetic_structure(tmp_path)
        (tmp_path / "POTCAR").write_text("ENMAX = 400.0\n")
        io_mod.prepare_inputs(tmp_path, _cfg(), kspacing=0.1,
                              task_type="defect", charge=0.0)
        txt = (tmp_path / "INCAR").read_text()
        assert "ENCUT = 520.0" in txt, txt

    def test_charged_defect_gets_nelect(self, tmp_path):
        _magnetic_structure(tmp_path)
        io_mod.prepare_inputs(tmp_path, _cfg(), kspacing=0.1,
                              task_type="defect", charge=-1.0)
        txt = (tmp_path / "INCAR").read_text()
        assert "NELECT" in txt, txt
        # poscar/potcar 不全时 vise 用默认 ZVAL——只守「charged 必写」契约。
        elect = next(ln for ln in txt.splitlines() if "NELECT" in ln)
        val = float(elect.split("=")[1])
        assert val > 0
