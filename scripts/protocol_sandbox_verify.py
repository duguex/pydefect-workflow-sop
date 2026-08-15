"""协议化 sandbox 验收:逐目录 INCAR 对照协议单一事实源(ADR 0024)。

读 sandbox 里生成的 INCAR(POSCAR/POTCAR 为参照),逐 key 对照
LEG_PROTOCOL / U_TABLE / INITIAL_MAGMOM / ENCUT 规则。硬门:任何
不符合 → 退出码 1。

豁免(显式声明):
- cpd 相 ENCUT = per-目录 1.3×max(ENMAX)(分区合法)
- cpd mol_* 相:固定 cell 协议独立,只查电子层(NELM/EDIFF)
- dielectric:DFPT 单步(NSW=1) + LREAL=.FALSE.(SOC 层协议)
- 目录无 POTCAR → 不查 ENCUT(记录)

用法:
    python3 scripts/protocol_sandbox_verify.py <sandbox_root>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vasp_sop.vasp.protocol import (
    INITIAL_MAGMOM,
    LEG_PROTOCOL,
    U_TABLE,
    encut_for_potcar,
    magmom_values,
)

_NUM = {"NSW", "NELM", "EDIFFG", "SIGMA", "LORBIT", "EDIFF", "ENCUT"}


def _norm(k: str, v: str) -> str:
    if k in _NUM:
        try:
            return f"{float(v):g}"
        except (TypeError, ValueError):
            return v.strip().lower()
    return v.strip().lower()


def _read_incar(d: Path) -> dict[str, str]:
    from vasp_sop.vasp.io import read_incar
    return read_incar(d)


def _species(d: Path) -> list[str]:
    from vasp_sop.vasp.io import _poscar_species
    return _poscar_species(d / "POSCAR") or []


def _kind(d: Path) -> str:
    parts = d.parts
    if "unitcell" in parts:
        i = parts.index("unitcell")
        return f"unitcell/{parts[i + 1]}" if i + 1 < len(parts) else "unitcell"
    for k in ("cpd", "defect"):
        if k in parts:
            return k
    return "other"


def _leg_of(kind: str) -> str | None:
    return {
        "cpd": "cpd",
        "defect": "defect",
        "unitcell/structure_opt": "structure_opt",
        "unitcell/band": "band",
        "unitcell/dos": "dos",
        "unitcell/dielectric": "dielectric",
    }.get(kind)


def _verify_dir(d: Path) -> list[str]:
    """返回违例描述列表(空 = 通过)。"""
    problems: list[str] = []
    incar = _read_incar(d)
    if not incar:
        return [f"无 INCAR(或不可读)"]
    name = d.name
    kind = _kind(d)
    leg = _leg_of(kind)
    species = _species(d)
    els = set(species)
    is_mol = kind == "cpd" and name.startswith("mol_")

    # ── ENCUT:每目录 1.3×max ENMAX(无 POTCAR 跳过)─────────────────
    potcar = d / "POTCAR"
    if potcar.is_file():
        want_encut = encut_for_potcar(potcar)
        got = incar.get("ENCUT")
        if want_encut and got is None:
            problems.append(f"ENCUT 缺失(应 {want_encut:g})")
        elif want_encut and _norm("ENCUT", got) != f"{want_encut:g}":
            problems.append(f"ENCUT={got}(应 {want_encut:g})")

    # ── 腿协议键(defect/cpd/structure_opt/band/dos/dielectric)──────
    if leg and leg in LEG_PROTOCOL:
        want_tags = LEG_PROTOCOL[leg]
        if is_mol:
            want_tags = {k: v for k, v in want_tags.items()
                         if k in ("NELM", "EDIFF")}
        for k, want in want_tags.items():
            got = incar.get(k)
            if got is None:
                problems.append(f"{k} 缺失(应 {want})")
            elif _norm(k, got) != _norm(k, want):
                problems.append(f"{k}={got}(应 {want})")
        if leg == "dielectric" and incar.get("LREAL") == "Auto":
            # SOC 层应已强制 .FALSE.(DFPT 协议);Auto 视为违例。
            problems.append(f"LREAL={incar.get('LREAL')}(应 .FALSE.)")

    # ── ISPIN:U 元素 / defect / SOC → 2──────────────────────────────
    expect_spin = (bool(els & set(U_TABLE)) or leg == "defect"
                   or str(incar.get("LSORBIT", "")).lower() == ".true.")
    if expect_spin and incar.get("ISPIN") not in ("2",):
        problems.append(f"ISPIN={incar.get('ISPIN')}(应 2:U/SOC/defect)")

    # ── MAGMOM:真磁性元素 + ISPIN=2 → 初始磁矩(高自旋)─────────────
    if els & set(INITIAL_MAGMOM) and incar.get("ISPIN") == "2":
        want = magmom_values(species)
        got = incar.get("MAGMOM")
        if got is None:
            problems.append(f"MAGMOM 缺失, 元素 "
                        f"{sorted(els & set(INITIAL_MAGMOM))})")
        elif want:
            got_vals = [float(x) for x in str(got).split()]
            if [f"{v:g}" for v in got_vals] != [f"{v:g}" for v in want]:
                problems.append(f"MAGMOM={got}(应 {' '.join(f'{v:g}' for v in want)})")

    # ── LDAU:U 元素 → LDAUU/LDAUL 按元素序匹配协议表───────────────
    if els & set(U_TABLE):
        if str(incar.get("LDAU", "")).lower() not in (".true.", "true"):
            problems.append(f"LDAU={incar.get('LDAU')}(应 .TRUE.:U 元素 {sorted(els & set(U_TABLE))})")
        else:
            ordered = list(dict.fromkeys(species))
            uu_want = [str(U_TABLE[s][0]) if s in U_TABLE else "0" for s in ordered]
            ul_want = [str(U_TABLE[s][1]) if s in U_TABLE else "-1" for s in ordered]
            for key, want in (("LDAUU", uu_want), ("LDAUL", ul_want)):
                want_norm = [float(x) for x in want]
                got_vals = [float(x) for x in str(incar.get(key, "")).split()]
                if got_vals != want_norm:
                    problems.append(
                        f"{key}={incar.get(key)}(应 {' '.join(f'{x:g}' for x in want_norm)})")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sandbox_root", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    root = args.sandbox_root.resolve()

    results: dict[str, list[str]] = {}
    for incar in sorted(root.rglob("INCAR")):
        d = incar.parent
        rel = str(d.relative_to(root))
        problems = _verify_dir(d)
        if problems:
            results[rel] = problems

    # ── NELECT(defect 链;hard gate)───────────────────────────────────
    from vasp_sop.core.config import PipelineConfig
    from vasp_sop.defect.builder import verify_nelect
    nelect_problems: list[str] = []
    defect_root = root / "defect"
    if defect_root.is_dir():
        plan = root / "plan.yaml"
        if plan.is_file():
            cfg = PipelineConfig.from_yaml(plan, root=root)
            nelect_problems = verify_nelect(defect_root, cfg)
            for p in nelect_problems:
                results.setdefault(f"defect/NELECT[{p[:60]}]", []).append(p)

    n_bad = len(results)
    for rel in sorted(results):
        print(f"[FAIL] {rel}")
        for p in results[rel]:
            print(f"       - {p}")
    print(f"verify: {n_bad} 违例目录"
          f"{' + NELECT ' + str(len(nelect_problems)) if nelect_problems else ''}")
    if args.json:
        import json
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
