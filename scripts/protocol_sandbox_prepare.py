"""协议化 sandbox 生成:nothing 计算,只生成计算输入文件(真实 vise)。

把源体系树拷贝到隔离目录(排除输出产物),清掉 INCAR,逐目录按协议表
(ADR 0024)重新生成输入——验证新生成器产出协议一致的 INCAR。

用法:
    python3 scripts/protocol_sandbox_prepare.py <src_sys_dir> <sandbox_root>

sandbox_root 可以是已存在目录(幂等:只对缺 INCAR 的目录生成)。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# 输出产物/状态文件——不进 sandbox。
EXCLUDE_FILES = {
    "OUTCAR", "OSZICAR", "vasprun.xml", "CONTCAR", "CHGCAR", "CHG",
    "WAVECAR", "PROCAR", "EIGENVAL", "DOSCAR", "XDATCAR", "IBZKPT",
    "PCDAT", "LOCPOT", "STOPCAR", "REPORT", ".failed",
    "formation_energy_interactive.html",
}
EXCLUDE_DIRS = {".big_sc_bak", ".dup_bak", "_excluded", "combos"}
# 输入白名单(除 INCAR 由生成器重建外,其余结构/势文件必须保留)。
INPUT_FILES = ("POSCAR", "POTCAR", "KPOINTS", "INCAR", "plan.yaml",
               "defect_in.yaml", "unitcell.yaml")


def _copy_inputs(src: Path, dst: Path) -> None:
    """拷贝一个计算目录的输入文件(POSCAR/POTCAR/KPOINTS/…)。"""
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_dir():
            if f.name not in EXCLUDE_DIRS:
                shutil.copytree(f, dst / f.name, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns(*sorted(EXCLUDE_FILES)))
            continue
        if f.name in EXCLUDE_FILES or f.suffix == ".log":
            continue
        if f.name == "INCAR":
            continue  # 清 INCAR——由 prepare_inputs 按协议重建
        shutil.copy2(f, dst / f.name)


def _charge_from_name(name: str) -> float:
    import re
    m = re.search(r"_(-?\d+)$", name)
    return float(m.group(1)) if m else 0.0


def _prepare_leg(d: Path, config, task_type: str, charge: float | None = None,
                 *, log) -> str:
    from vasp_sop.vasp.io import input_ready, prepare_inputs
    if input_ready(d):
        log.info("  SKIP %s: inputs ready (INCAR 已存在)", d.name)
        return "skip"
    prepare_inputs(d, config, kspacing=0.1 if charge is not None else 2.0,
                   task_type=task_type, charge=charge)
    return "generated"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src_sys", type=Path, help="源体系目录(含 plan.yaml)")
    ap.add_argument("sandbox_root", type=Path)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="每腿最多生成数(0=全部)")
    args = ap.parse_args()
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("protocol-sandbox")

    src = args.src_sys.resolve()
    plan = src / "plan.yaml"
    if not plan.is_file():
        log.error("no plan.yaml in %s", src)
        return 2
    from vasp_sop.core.config import PipelineConfig
    sandbox = args.sandbox_root.resolve()
    config = PipelineConfig.from_yaml(plan, root=src)

    counts = {"generated": 0, "skip": 0, "fail": 0}
    failures: list[str] = []

    def _one(d: Path, task_type: str, charge: float | None = None) -> None:
        _copy_inputs(d, sandbox / d.relative_to(src))
        dst = sandbox / d.relative_to(src)
        try:
            state = _prepare_leg(dst, config, task_type, charge, log=log)
            counts[state if state in counts else "generated"] += 1
        except Exception as exc:  # noqa: BLE001 —— per-dir 失败不中断
            counts["fail"] += 1
            failures.append(f"{d.relative_to(src)}: {type(exc).__name__}: {exc}")

    # ── cpd 相(CLI structure_opt 腿)───────────────────────────────────
    cpd = src / "cpd"
    if cpd.is_dir():
        n = 0
        for pd in sorted(p for p in cpd.iterdir() if p.is_dir()):
            if pd.name in EXCLUDE_DIRS:
                continue
            _one(pd, "")
            n += 1
            if args.limit and n >= args.limit:
                break
    # ── defect 链(API 腿,charge 从目录名)──────────────────────────────
    defect = src / "defect"
    if defect.is_dir():
        n = 0
        for dd in sorted(p for p in defect.iterdir() if p.is_dir()):
            _one(dd, "defect", _charge_from_name(dd.name))
            n += 1
            if args.limit and n >= args.limit:
                break
    # ── unitcell 四腿─────────────────────────────────────────────────
    uc = src / "unitcell"
    if uc.is_dir():
        for task in ("structure_opt", "band", "dos", "dielectric"):
            td = uc / task
            if not td.is_dir():
                continue
            _one(td, task)
    # plan.yaml 落到 sandbox 根(供 verify 读 config)
    if not (sandbox / "plan.yaml").is_file():
        shutil.copy2(plan, sandbox / "plan.yaml")
    shutil.copy2(plan, sandbox / "plan.yaml")

    log.info("done: generated=%d skip=%d fail=%d", counts["generated"],
             counts["skip"], counts["fail"])
    for f in failures[:20]:
        log.error("  FAIL %s", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
