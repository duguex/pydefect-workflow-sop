#!/usr/bin/env python3
"""VASP 计算树结果检查机制(通用, 以 OUTCAR 回显为准)

覆盖已知事故类:
  A. POTCAR 变体/日期混用(同体系同元素多变体, 如 Ga vs Ga_d)
  B. POTCAR 段格式污染(TITEL= 前缀段 → VASP 解析错位 → 能量荒谬;
     判据: OUTCAR `POTCAR:` 解析行 vs `TITEL` 声明行矛盾, 或盘面段头非 PAW_PBE 行首)
  C. 收敛状态(缺 OUTCAR / 失败残留 / 未收敛; band/dos/dielectric 非自洽任务豁免;
     NELM 边缘但能量平(energy-flat)豁免)
  D. 能量离群(每原子 TOTEN 与同根下子目录组中位数差超阈值)
  E. POTCAR 段数 vs POSCAR 物种数不匹配

通用化约定:
  - 扫描 --root 下每个直接子目录为一组(组内做变体/离群判定)
  - 排除: .git/__pycache__/.big_sc_bak/defect_new 及隐藏目录
  - 缺陷目录(名字含 _<charge> 或含 defect_entry.json)无 OUTCAR 时,
    若安装 vasp_sop 包则过 is_valid_defect_dir 排除门(ADR 0013 反位), 否则报欠账
  - 非自洽目录豁免: --non-scf 指定(默认 band/dos/dielectric)

周期监控用法(与上次报告对比, 新问题告警):
  python3 scripts/check_results.py --root DIR --json /tmp/check_latest.json --compare /tmp/check_prev.json
  对比: 上次 OK/缺席 → 本次 BAD 的目录 = 新增问题(打印 + exit 1)
  systemd: 见 scripts/systemd/vasp-sop-check.service + .timer

退出码: 0=无问题/无新增, 1=有问题或新增, 2=扫描本身失败
"""
import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path

try:
    from vasp_sop.defect import is_valid_defect_dir
except ImportError:  # 未安装包时降级: 无排除语义
    is_valid_defect_dir = None

DEFAULT_ROOT = Path("/mnt/shared/home/2sidesniddle/vasp/2026_undergo_spin_defect")
MIN_OUTCAR_SIZE = 4000          # 小于此视为失败残留
ENERGY_OFFSET_THRESHOLD = 8.0   # eV/atom 离群阈值(捕获 ~800 eV/atom 解析错位, 不漏正常磁态)
TAIL_WINDOW_KB = 64             # 首次尾部窗口
TAIL_WINDOW_KB_EXT = 512        # TOTEN 不足时扩展窗口(SOC 大文件电子步在窗口外)
ENERGY_FLAT_EV = 1e-3           # 最后两步 TOTEN 差小于此 → 能量可用

TITEL_RE = re.compile(r"^\s*TITEL\s*=\s*(\S+)\s+(\S+)\s+(\S+)")
POTCAR_LINE_RE = re.compile(r"^\s*POTCAR:\s+(\S+)\s+(\S+)\s+(\S+)")
SEG_HEAD_RE = re.compile(r"^\s*PAW_PBE\s+(\S+)\s+(\S+)")
TOTEN_RE = re.compile(r"free\s+energy\s+TOTEN\s*=\s*([-\d.]+)")
NIONS_RE = re.compile(r"NIONS\s*=\s*(\d+)")
DEFECT_NAME_RE = re.compile(r"_\d+$")  # 缺陷目录形态: <Name>_<charge>


def read_tail(path: Path, window_kb: int) -> list[str]:
    """读 OUTCAR 尾部 window_kb 大小的行(截断安全, crisp 截断只影响尾)。"""
    size = path.stat().st_size
    if size == 0:
        return []
    with open(path, errors="ignore") as f:
        f.seek(max(0, size - window_kb * 1024))
        return f.read().splitlines()


def read_head(path: Path, n: int = 2000) -> list[str]:
    with open(path, errors="ignore") as f:
        return f.read().splitlines()[:n]


def tail_totens(tail: list[str]) -> list[float]:
    return [float(m.group(1)) for ln in tail for m in [TOTEN_RE.search(ln)] if m]


def potcar_segments(potcar: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """盘面 POTCAR 段: 返回 [(元素,日期)] 与污染段标记列表。"""
    segs: list[tuple[str, str]] = []
    bad: list[str] = []
    try:
        text = potcar.read_text(errors="ignore")
    except OSError:
        return [], ["POTCAR 不可读"]
    for seg in text.split("End of Dataset"):
        seg = seg.strip()
        if not seg:
            continue
        m = SEG_HEAD_RE.match(seg)
        if m:
            segs.append((m.group(1), m.group(2)))
        else:
            bad.append(seg.splitlines()[0][:80] if seg.splitlines() else "(empty)")
    return segs, bad


def poscar_species(poscar: Path) -> list[str]:
    try:
        lines = poscar.read_text(errors="ignore").splitlines()
        if len(lines) < 7:
            return []
        return lines[5].split()
    except OSError:
        return []


def scan_dir(d: Path, non_scf: set[str]) -> dict:
    """单个计算目录的检查结果。"""
    rec = {"dir": str(d), "ok": True, "issues": []}
    outcars = sorted(d.glob("OUTCAR"))
    if not outcars:
        # 无 OUTCAR: defect 目录先过排除门(ADR 0013 反位/junk), 排除目录不算问题
        is_defect = bool(DEFECT_NAME_RE.search(d.name)) or (d / "defect_entry.json").exists()
        if is_defect and is_valid_defect_dir is not None and not is_valid_defect_dir(d):
            rec["skipped"] = "excluded-defect"
            return rec
        rec["ok"] = False
        rec["issues"].append("无 OUTCAR")
        return rec
    outcar = outcars[-1]  # 以最新为准
    if outcar.stat().st_size < MIN_OUTCAR_SIZE:
        rec["ok"] = False
        rec["issues"].append(f"OUTCAR 过小({outcar.stat().st_size}B, 失败残留)")
        return rec
    head = read_head(outcar)

    titel: list[tuple[str, str]] = []
    potcar_lines: list[tuple[str, str]] = []
    nions: int | None = None
    for ln in head:
        m = TITEL_RE.search(ln)
        if m:
            titel.append((m.group(2), m.group(3)))
        m = POTCAR_LINE_RE.search(ln)
        if m:
            potcar_lines.append((m.group(2), m.group(3)))
        m = NIONS_RE.search(ln)
        if m:
            nions = int(m.group(1))
    rec["titel"] = titel
    rec["nions"] = nions

    # B1: POTCAR: 解析行 vs TITEL 声明行矛盾(格式污染特征)
    tset, pset = set(titel), set(potcar_lines)
    if tset and pset and tset != pset:
        rec["ok"] = False
        rec["issues"].append(f"POTCAR:行{sorted(pset)} != TITEL行{sorted(tset)}(段格式污染)")

    # B2: 盘面 POTCAR 段头格式
    potcar = d / "POTCAR"
    if potcar.exists():
        segs, bad = potcar_segments(potcar)
        rec["segs"] = segs
        if bad:
            rec["ok"] = False
            rec["issues"].append(f"盘面 POTCAR 污染段: {bad}")

        # E: 段数 vs POSCAR 物种数
        species = poscar_species(d / "POSCAR")
        if species and segs and len(segs) != len(species):
            rec["ok"] = False
            rec["issues"].append(
                f"POTCAR {len(segs)} 段 != POSCAR {len(species)} 物种 {species}"
            )
        elif segs and not species:
            rec["issues"].append("POSCAR 不可读, 跳过段数核对")

    # C: 收敛 (非自洽任务豁免; energy-flat 豁免)
    tail = read_tail(outcar, TAIL_WINDOW_KB)
    converged = any("reached required accuracy" in ln for ln in tail)
    rec["converged"] = converged
    if not converged:
        if d.name in non_scf:
            rec["issues"].append("非自洽任务(无收敛标记, 豁免)")
        elif outcar.stat().st_mtime > time.time() - 3600:
            rec["live"] = True  # OUTCAR 1 小时内更新过 → 在跑, 不算问题
            rec["issues"].append("在跑(in-flight)")
        else:
            # TOTEN 不足时扩展窗口(SOC 大文件电子步在 64KB 窗口外)
            totens = tail_totens(tail)
            if len(totens) < 2:
                totens = tail_totens(read_tail(outcar, TAIL_WINDOW_KB_EXT))
            if len(totens) >= 2 and abs(totens[-1] - totens[-2]) < ENERGY_FLAT_EV:
                rec["energy_flat"] = True
                rec["issues"].append("NELM 边缘但能量平(energy-flat, 豁免)")
            else:
                rec["ok"] = False
                rec["issues"].append("未收敛(reached required accuracy 缺失)")

    # D: 能量
    tail_full = tail
    toten = tail_totens(tail_full)
    if len(toten) < 2:
        toten = tail_totens(read_tail(outcar, TAIL_WINDOW_KB_EXT))
    rec["toten"] = toten[-1] if toten else None
    if rec["toten"] is not None and nions:
        rec["e_per_atom"] = rec["toten"] / nions
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--json", type=Path, help="输出 JSON 报告路径")
    ap.add_argument("--compare", type=Path, help="与上次 JSON 报告对比, 列出新增问题")
    ap.add_argument("--non-scf", default="band,dos,dielectric",
                    help="非自洽目录名逗号分隔(豁免收敛标记, 默认 band,dos,dielectric)")
    ap.add_argument("--quick", action="store_true", help="跳过能量维度")
    args = ap.parse_args()

    root = args.root
    if not root.is_dir():
        print(f"FATAL: root 不存在 {root}", file=sys.stderr)
        return 2
    non_scf = {s.strip() for s in args.non_scf.split(",") if s.strip()}

    # 组内变体/日期混用 (维度 A)
    variants: dict[str, dict[str, set]] = collections.defaultdict(
        lambda: collections.defaultdict(set)
    )
    all_recs: list[dict] = []
    for group in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        group_recs = []
        for d in sorted(p for p in group.rglob("*") if p.is_dir()):
            if any(x in str(d) for x in ("__pycache__", ".git", ".big_sc_bak", "defect_new")):
                continue
            if not (d / "OUTCAR").exists() and not (d / "POTCAR").exists():
                continue  # 非计算目录(如产物/状态目录)
            rec = scan_dir(d, non_scf)
            group_recs.append(rec)
            all_recs.append(rec)
            for token, date in rec.get("titel", []):
                variants[group.name][token].add((token, date))
        if group_recs:
            print(f"\n=== {group.name}: {len(group_recs)} 目录 ===")
            for rec in group_recs:
                if rec.get("skipped"):
                    print(f"  -- {Path(rec['dir']).name:28s} skipped({rec['skipped']})")
                    continue
                tag = "OK " if rec["ok"] else "!! "
                e = (
                    f"{rec['e_per_atom']:8.3f} eV/atom" if rec.get("e_per_atom") is not None
                    else "n/a"
                )
                print(f"  {tag}{Path(rec['dir']).name:28s} conv={rec.get('converged')} {e}")
                for iss in rec["issues"]:
                    print(f"       - {iss}")

    # A: 变体/日期混用汇总
    print("\n=== A. POTCAR 变体/日期混用(组内) ===")
    mix_issues = []
    for s in sorted(variants):
        for el in sorted(variants[s]):
            toks = sorted({t for t, _ in variants[s][el]})
            dates = sorted({dt for _, dt in variants[s][el]})
            if len(toks) > 1 or len(dates) > 1:
                mix_issues.append(f"{s}: {el} -> tokens={toks} dates={dates}")
    if mix_issues:
        for m in mix_issues:
            print(f"  MIX {m}")
    else:
        print("  无混用")

    # D: 能量离群(按组)
    if not args.quick:
        print("\n=== D. 能量离群(组内每原子 TOTEN vs 中位数) ===")
        by_group: dict[str, list[dict]] = collections.defaultdict(list)
        for rec in all_recs:
            if rec.get("e_per_atom") is not None:
                by_group[str(Path(rec["dir"]).parent.name)].append(rec)
        outliers = []
        for s, recs in by_group.items():
            if len(recs) < 3:
                continue
            med = sorted(r["e_per_atom"] for r in recs)[len(recs) // 2]
            for rec in recs:
                if abs(rec["e_per_atom"] - med) > ENERGY_OFFSET_THRESHOLD:
                    outliers.append((s, Path(rec["dir"]).name, rec["e_per_atom"], med))
        if outliers:
            for s, name, e, med in sorted(outliers):
                print(f"  OUTLIER {s}/{name}: {e:.3f} eV/atom (组中位 {med:.3f})")
        else:
            print("  无离群")

    n_bad = sum(1 for r in all_recs if not r["ok"])
    n_conv = sum(1 for r in all_recs if r.get("converged"))
    n_skip = sum(1 for r in all_recs if r.get("skipped"))
    print(f"\n=== 汇总: {len(all_recs)} 目录, 收敛 {n_conv}, 问题 {n_bad}, 排除跳过 {n_skip} ===")

    report = {
        "root": str(root),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "n_dirs": len(all_recs),
        "n_converged": n_conv,
        "n_issues": n_bad,
        "n_skipped": n_skip,
        "variant_mixes": mix_issues,
        "dirs": all_recs,
    }
    if args.json:
        args.json.write_text(json.dumps(report, indent=1))
        print(f"JSON 报告: {args.json}")

    # 周期监控: 与上次报告对比, 新增问题告警
    new_problems = 0
    if args.compare and args.compare.exists():
        prev = json.loads(args.compare.read_text())
        prev_bad = {r["dir"] for r in prev["dirs"] if not r["ok"]}
        prev_ok = {r["dir"] for r in prev["dirs"] if r["ok"]}
        now_bad = {r["dir"] for r in all_recs if not r["ok"]}
        fresh = sorted(now_bad - prev_bad)          # 本次新问题
        regressed = sorted(now_bad & prev_ok)       # 上次 OK → 本次 BAD(回归)
        if fresh or regressed:
            print("\n=== 与上次对比: 新增问题 ===")
            for d in fresh:
                print(f"  NEW  {d}")
                new_problems += 1
            for d in regressed:
                print(f"  REGRESS {d}")
                new_problems += 1
        else:
            print("\n=== 与上次对比: 无新增问题 ===")
    return 1 if (n_bad or mix_issues or new_problems) else 0


if __name__ == "__main__":
    sys.exit(main())
