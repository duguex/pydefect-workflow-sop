"""单一事实源:计算协议声明。

每条腿(defect / cpd / structure_opt / band / dos / dielectric)的 VASP
参数、ENCUT 规则与初始磁矩在此单点定义。生成器所有路径(vise CLI /
vise API / UC 直拼)与 :mod:`scripts.check_results` 的协议基线检查都从
这里取数,不再散落各调用点。

背景:2026-08-15 批次验收检出 13/13 体系 ENCUT/EDIFF/EDIFFG/SIGMA
漂移——根因是协议值散落 io.py CLI 字符串、API overrides、builder.py
extra_uis 与 _MAGMOM_TABLE(仅 Fe),三路径靠注释互相镜像;vise 模板
默认在腿间不一致(unitcell structure_opt=520 vs band/dos/dielectric=400),
生成器未强制 ENCUT 时漂移被固化。本模块为这些值的唯一权威(ADR 0024)。
"""

from __future__ import annotations

import re
from pathlib import Path

# ── 每腿协议参数 ────────────────────────────────────────────────────────
# 值来源:2026-08-11 批次协议决定(见各注释;历史散落值已收敛于此)。
# defect 是唯一有 SIGMA/LORBIT 声明的腿——占据展宽只对缺陷占据敏感。
LEG_PROTOCOL: dict[str, dict[str, str | float | bool]] = {
    # 电子步预算 cap:典型缺陷胞 16-30 步收敛,vise 默认 100 浪费计算
    # (ADR 0016 门检 NELM 耗尽)。EDIFFG=-0.01 与 cpd 弛豫腿统一。
    "defect": {
        "NSW": 100,
        "NELM": 30,
        "EDIFF": "1e-4",
        "EDIFFG": -0.01,
        "SIGMA": 0.02,
        "LORBIT": 11,
    },
    # cpd 相与 unitcell structure_opt:离子弛豫,离子精度由 EDIFFG 管,
    # NELM=50 电子预算(2026-08-11;vise 模板 1e-7 烧 ~2x 电子步无精度增益)。
    "structure_opt": {
        "NSW": 50,
        "NELM": 50,
        "EDIFF": "1e-4",
        "EDIFFG": -0.01,
    },
    "cpd": {
        "NSW": 50,
        "NELM": 50,
        "EDIFF": "1e-4",
        "EDIFFG": -0.01,
    },
    # 单点/DFPT 腿:只声明电子层(不 relax,EDIFFG/NSW 无意义,留给模板;
    # dielectric NSW=1 防 DFPT 重算线性响应,见 _apply_soc_tags)。
    "band": {"NELM": 50, "EDIFF": "1e-4"},
    "dos": {"NELM": 50, "EDIFF": "1e-4"},
    "dielectric": {"NSW": 1, "NELM": 50, "EDIFF": "1e-4"},
}

# 生成器/检查器把任务名映射到腿名(与 io.py 的 _VISE_TASK_MAP 一致)。
TASK_TO_LEG: dict[str, str] = {
    "defect": "defect",
    "structure_opt": "structure_opt",
    "cpd": "cpd",
    "band": "band",
    "dos": "dos",
    "dielectric": "dielectric",
    "": "structure_opt",  # prepare_inputs 无 task_type 的默认 = 弛豫腿
}

# ── 初始自旋矩(μB,高自旋)───────────────────────────────────────────────
# VASP 推荐高自旋值(MP 惯例)。覆盖 U 表磁性元素(3d Mn-Ni 与 4f 镧系
# M³⁺,取 f 电子未配对近似);Fe=5.0 沿用历史。Ti(d⁰)/Cu(d⁹ 弱)/Zn(d¹⁰)
# 无可靠磁矩不写(VASP 默认 1.0/site 足够推开非磁鞍点)——塌缩敏感
# 元素子集与 check_results 一致。Gd=7.0 修复 SOC 无 MAGMOM 初始化导致
# 的 Gd³⁺ 4f 磁矩塌缩(~0 μB,issue #151)。
INITIAL_MAGMOM: dict[str, float] = {
    # 3d 统一高自旋 5(2026-08-16 决策:初始磁矩是 SCF 起点,统一 5 简化;
    # Mn²⁺/Fe³⁺ d⁵ 的 2S=5 是 3d 高自旋上限——起跑点足够把 SCF 推离
    # 非磁鞍点,最终值由 SCF 收敛;Co³⁺ d⁶/Ni²⁺ d⁸ 略高但无害)
    "Cr": 5.0,
    "Mn": 5.0,
    "Fe": 5.0,
    "Co": 5.0,
    "Ni": 5.0,
    "Ce": 1.0, "Pr": 2.0, "Nd": 3.0, "Sm": 5.0, "Eu": 6.0, "Gd": 7.0,
    "Tb": 6.0, "Dy": 5.0, "Ho": 4.0, "Er": 3.0, "Tm": 2.0, "Yb": 1.0,
}

_RE_ENMAX = re.compile(r"ENMAX\s*=\s*([\d.]+)")

# ── DFT+U 表(元素 → (U [eV], L 量子数))───────────────────────────────
# 单一事实源:io.patch_incar_u 与 check_results 的 ISPIN 预期同源。
# Ti (U=4) 官方 vise 表有但 libs/vise fork 缺——fork 的 set_hubbard_u 会
# 静默丢 Ti 标签,这里补丁保持 cpd/defect/band/dos 一致(ADR 0012)。
U_TABLE: dict[str, tuple[float, int]] = {
    "Ti": (4.0, 2),
    "Cr": (3.0, 2),  # 3d 过渡金属惯例(与 Mn/Fe 一致);2026-08-16 Li2ZnGe3O8 批决定
    "Mn": (3.0, 2), "Fe": (3.0, 2), "Co": (3.0, 2), "Ni": (3.0, 2),
    "Cu": (5.0, 2), "Zn": (5.0, 2),
    "Ce": (5.0, 3), "Pr": (5.0, 3), "Nd": (5.0, 3), "Sm": (5.0, 3),
    "Eu": (5.0, 3), "Gd": (5.0, 3), "Tb": (5.0, 3), "Dy": (5.0, 3),
    "Ho": (5.0, 3), "Er": (5.0, 3), "Tm": (5.0, 3), "Yb": (5.0, 3),
    "Lu": (5.0, 3),
}


def encut_for_potcar(potcar: Path) -> float | None:
    """协议 ENCUT = 1.3 × max(全块 ENMAX)(VASP 保守惯例)。

    同一体系 defect/unitcell 的 POTCAR 同为宿主组成 → 检测值一致(组内
    单一 ENCUT);cpd 竞争相按各自组成得到 per-相值(合法分区豁免)。
    """
    if not Path(potcar).is_file():
        return None
    max_enmax = 0.0
    try:
        text = Path(potcar).read_text()
        for enmax in _RE_ENMAX.findall(text):
            max_enmax = max(max_enmax, float(enmax))
    except OSError:
        return None
    return round(max_enmax * 1.3, 1) if max_enmax > 0 else None


def effective_encut(config, work_dir: Path | str) -> float | None:
    """生成期有效 ENCUT:plan/config 显式值优先,否则目录 POTCAR 检测。

    保证 ENCUT 永不落入 vise 模板/调用点默认(漂移来源)。目录无
    POTCAR 时返回 None(调用点自行决定兜底)。
    """
    cfg_encut = getattr(config, "encut", None)
    if cfg_encut:
        return float(cfg_encut)
    return encut_for_potcar(Path(work_dir) / "POTCAR")


def protocol_tags(task_type: str = "") -> dict[str, str | float | bool]:
    """任务类型 → 该腿协议的 INCAR 覆盖字典(生成器组装用)。"""
    leg = TASK_TO_LEG.get(task_type, task_type)
    return dict(LEG_PROTOCOL.get(leg, {}))


def magmom_values(species: list[str]) -> list[float]:
    """POSCAR 原子序对应的初始磁矩(非磁性元素 0.0)。

    只对含磁性元素的目录返回非空;全非磁返回 None(不写 MAGMOM)。
    """
    if not species or not any(s in INITIAL_MAGMOM for s in species):
        return None
    return [INITIAL_MAGMOM.get(s, 0.0) for s in species]


# ── 两阶段 DFT+U(ADR 0025)────────────────────────────────────────────
# 与 SOC 两阶段同构:弛豫腿 stage1 不加 LDAU(自旋保留, 见
# io.patch_incar_u apply_u=False), 收敛后 stage2 一次补充最终协议
# (LSORBIT? + LDAU)——合并策略(grill 2026-08-16 Q6)。
# 单点腿(band/dos/dielectric)带 U 不带 SOC(Q7)——不参与两阶段。
SINGLEPOINT_LEGS = frozenset({"band", "dos", "dielectric"})


def needs_u(species: list[str]) -> bool:
    """目录是否需 DFT+U(含 U 表元素)——stage2 U 补充的触发。"""
    return any(s in U_TABLE for s in species)


def is_singlepoint(task_type: str) -> bool:
    """任务类型是否为单点腿(band/dos/dielectric)——带 U 无 SOC、不两阶段。"""
    return task_type in SINGLEPOINT_LEGS


def needs_final_soc(config) -> bool:
    """体系最终协议是否需要 LSORBIT(soc=true 恒需要——stage2 补充)。

    stage2_soc=false 的显式单阶段口子:生成时已带 LSORBIT,最终协议
    判定仍返回 True(已满足, pending 检查读 INCAR 见已含)。
    """
    return bool(getattr(config, "soc", False))