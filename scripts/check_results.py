#!/usr/bin/env python3
"""VASP 批次结果验收机制(两支柱, 以 OUTCAR 回显为准, 通用)

支柱 1 收敛真实性(每计算):
  - reached required accuracy 权威; 失败残留(小 OUTCAR)/无 OUTCAR 为欠账
  - 豁免: 非自洽任务(band/dos/dielectric, 可配) + NELM 边缘 energy-flat(末两步 TOTEN 差 < --flat-ev)

支柱 2 体系内可比性(每体系):
  - 物理 key 强制一致(OUTCAR INCAR 回显): ENCUT/EDIFF/EDIFFG/SIGMA/LSORBIT/ISPIN/
    LDAU/LDAUU/LDAUL(LDAUU/LDAUL 按元素映射比较, 元素数不同不误报)
  - POTCAR 变体/日期组内统一
  - 零点一致性: cpd/composition_energies.yaml 来源相目录的 POTCAR 变体集合
    与体系 defect 链一致(能量参考同源)
  - 白名单(记录级不门禁): ISMEAR(金属/绝缘相物理必需), 控制 key 不查
    (NSW/IBRION/KPAR/NCORE/ISYM/ALGO/PREC 等)

报告: 体系验收表(一行/体系: 收敛✓/~ /✗ + 可比✓/✗ + 证据) + 批次汇总 + JSON
门禁: 物理 key 不一致 / POTCAR 变体混用 / 零点不一致 / 能量离群 → 体系不可信; 欠账单列
用法:
  python3 scripts/check_results.py [--root DIR] [--json OUT] [--compare PREV] [--quick]
退出码: 0=无问题, 1=有问题, 2=扫描失败
"""
import argparse
import collections
import html
import json
import re
import sys
import time
from pathlib import Path

try:
    from vasp_sop.defect import is_valid_defect_dir
except ImportError:  # 未安装包时降级: 无排除语义
    is_valid_defect_dir = None

try:
    import yaml
except ImportError:
    yaml = None  # 零点一致性检查降级跳过

DEFAULT_ROOT = Path("/mnt/shared/home/2sidesniddle/vasp/2026_undergo_spin_defect")
MIN_OUTCAR_SIZE = 4000          # 小于此视为失败残留
ENERGY_OFFSET_THRESHOLD = 8.0   # eV/atom 离群阈值
TAIL_WINDOW_KB = 64             # 首次尾部窗口
TAIL_WINDOW_KB_EXT = 512        # TOTEN 不足时扩展窗口(SOC 大文件)
ENERGY_FLAT_EV = 1e-3           # 末两步 TOTEN 差 < 此值 → energy-flat

TITEL_RE = re.compile(r"^\s*TITEL\s*=\s*(\S+)\s+(\S+)\s+(\S+)")
POTCAR_LINE_RE = re.compile(r"^\s*POTCAR:\s+(\S+)\s+(\S+)\s+(\S+)")
SEG_HEAD_RE = re.compile(r"^\s*PAW_PBE\s+(\S+)\s+(\S+)")
TOTEN_RE = re.compile(r"free\s+energy\s+TOTEN\s*=\s*([-\d.]+)")
NIONS_RE = re.compile(r"NIONS\s*=\s*(\d+)")
INCAR_KEY_RE = re.compile(r"^\s+(\w+)\s*=\s*(\S.*?)\s*$")
DEFECT_NAME_RE = re.compile(r"_\d+$")  # 缺陷目录形态: <Name>_<charge>

# 支柱 2 物理 key(决定结果数值, 强制一致); LDAUU/LDAUL 按元素映射比较
PHYSICAL_KEYS = ["ENCUT", "EDIFF", "EDIFFG", "SIGMA", "LSORBIT", "ISPIN",
                 "LDAU", "LDAUU", "LDAUL"]
# 白名单: ISMEAR 记录级(金属/绝缘相物理必需); 控制 key 不提取
RECORD_ONLY_KEYS = ["ISMEAR"]
NON_SCF_DIRS_DEFAULT = ["band", "dos", "dielectric"]


def read_tail(path: Path, window_kb: int) -> list[str]:
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


def outcar_incar_echo(head: list[str]) -> dict[str, str]:
    """OUTCAR 头部 ' INCAR:' 块 → {key: value}(执行真相)。"""
    out: dict[str, str] = {}
    in_block = False
    for ln in head:
        if ln.strip() == "INCAR:":
            in_block = True
            continue
        if in_block:
            if not ln.strip():
                break
            m = INCAR_KEY_RE.match(ln)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def lda_el_mapped(incar: dict[str, str], titel: list[tuple[str, str]]) -> dict:
    """LDAUU/LDAUL 值列表按元素序映射 → {(el, value)} 集合(元素数不同不误报)。"""
    res: dict[str, set] = {}
    els = [t for t, _ in titel]
    for key in ("LDAUU", "LDAUL"):
        v = incar.get(key)
        if v is None:
            continue
        vals = v.split()
        if len(vals) == len(els):
            try:
                res[key] = {(e, str(float(x))) for e, x in zip(els, vals)}
            except ValueError:  # 非数值(如含注释): 保留原串
                res[key + "_raw"] = {v}
        else:  # 长度不匹配: 保留原串供报告
            res[key + "_raw"] = {v}
    return res


def potcar_segments(potcar: Path) -> tuple[list[tuple[str, str]], list[str]]:
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
    """单目录检查: 收敛真实性 + 可比性证据(INCAR 回显/POTCAR 变体)。"""
    rec = {"dir": str(d), "ok": True, "issues": []}
    outcars = sorted(d.glob("OUTCAR"))
    if not outcars:
        is_defect = bool(DEFECT_NAME_RE.search(d.name)) or (d / "defect_entry.json").exists()
        if is_defect and is_valid_defect_dir is not None and not is_valid_defect_dir(d):
            rec["skipped"] = "excluded-defect"
            return rec
        rec["ok"] = False
        rec["issues"].append("无 OUTCAR")
        return rec
    outcar = outcars[-1]
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
    rec["incar"] = outcar_incar_echo(head)

    # 可比性证据 B1: POTCAR: 解析行 vs TITEL 声明行矛盾(段格式污染)
    tset, pset = set(titel), set(potcar_lines)
    if tset and pset and tset != pset:
        rec["ok"] = False
        rec["issues"].append(f"POTCAR:行{sorted(pset)} != TITEL行{sorted(tset)}(段格式污染)")

    # 可比性证据 B2: 盘面 POTCAR 段头格式 + 段数 vs POSCAR
    potcar = d / "POTCAR"
    if potcar.exists():
        segs, bad = potcar_segments(potcar)
        rec["segs"] = segs
        if bad:
            rec["ok"] = False
            rec["issues"].append(f"盘面 POTCAR 污染段: {bad}")
        species = poscar_species(d / "POSCAR")
        if species and segs and len(segs) != len(species):
            rec["ok"] = False
            rec["issues"].append(
                f"POTCAR {len(segs)} 段 != POSCAR {len(species)} 物种 {species}"
            )

    # 支柱 1: 收敛真实性
    tail = read_tail(outcar, TAIL_WINDOW_KB)
    converged = any("reached required accuracy" in ln for ln in tail)
    rec["converged"] = converged
    if not converged:
        if d.name in non_scf:
            rec["exempt"] = "non-scf"
            rec["issues"].append("非自洽任务(无收敛标记, 豁免)")
        elif outcar.stat().st_mtime > time.time() - 3600:
            rec["live"] = True
            rec["issues"].append("在跑(in-flight)")
        else:
            totens = tail_totens(tail)
            if len(totens) < 2:
                totens = tail_totens(read_tail(outcar, TAIL_WINDOW_KB_EXT))
            if len(totens) >= 2 and abs(totens[-1] - totens[-2]) < ENERGY_FLAT_EV:
                rec["energy_flat"] = True
                rec["issues"].append("NELM 边缘但能量平(energy-flat, 豁免)")
            else:
                rec["ok"] = False
                rec["issues"].append("未收敛(reached required accuracy 缺失)")

    # 能量(离群证据)
    totens = tail_totens(tail)
    if len(totens) < 2:
        totens = tail_totens(read_tail(outcar, TAIL_WINDOW_KB_EXT))
    rec["toten"] = totens[-1] if totens else None
    if rec["toten"] is not None and nions:
        rec["e_per_atom"] = rec["toten"] / nions
    return rec


def _outcar_variants(o: Path) -> set[tuple[str, str]]:
    """OUTCAR 的 (token, date) 变体集合。"""
    if not o.exists() or o.stat().st_size < MIN_OUTCAR_SIZE:
        return set()
    s: set[tuple[str, str]] = set()
    for ln in read_head(o):
        m = TITEL_RE.search(ln)
        if m:
            s.add((m.group(2), m.group(3)))
    return s


def zero_point_check(group_dir: Path) -> list[str]:
    """零点一致性: composition_energies 来源相目录的 POTCAR 变体
    vs 体系 defect/ 链的变体集合(能量参考必须同源)。"""
    if yaml is None:
        return ["yaml 不可用, 跳过"]
    comp = group_dir / "cpd" / "composition_energies.yaml"
    if not comp.exists():
        return ["无 composition_energies.yaml"]
    try:
        data = yaml.safe_load(comp.read_text(errors="ignore")) or {}
    except Exception as e:
        return [f"composition_energies 解析失败: {e}"]
    src_variants: set[tuple[str, str]] = set()
    missing_src = []
    for entry in data.values():
        src = (entry or {}).get("source")
        if not src:
            continue
        v = _outcar_variants(group_dir / "cpd" / str(src) / "OUTCAR")
        if not v:
            missing_src.append(str(src))
        src_variants |= v
    defect_variants: set[tuple[str, str]] = set()
    dd = group_dir / "defect"
    if dd.is_dir():
        for d in dd.iterdir():
            if d.is_dir():
                defect_variants |= _outcar_variants(d / "OUTCAR")
    issues = []
    if missing_src:
        issues.append(f"composition 来源缺 OUTCAR: {missing_src[:6]}")
    if src_variants and defect_variants and src_variants != defect_variants:
        only_src = src_variants - defect_variants
        only_def = defect_variants - src_variants
        issues.append(f"零点不一致: 仅composition {sorted(only_src)[:6]} 仅defect {sorted(only_def)[:6]}")
    return issues


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--json", type=Path, help="输出 JSON 报告路径")
    ap.add_argument("--report", type=Path, help="输出 HTML 验收报告路径(自包含单文件)")
    ap.add_argument("--compare", type=Path, help="与上次 JSON 对比, 列出新增问题")
    ap.add_argument("--non-scf", default=",".join(NON_SCF_DIRS_DEFAULT),
                    help="非自洽目录名逗号分隔(豁免收敛标记)")
    ap.add_argument("--flat-ev", type=float, default=ENERGY_FLAT_EV)
    ap.add_argument("--outlier-ev", type=float, default=ENERGY_OFFSET_THRESHOLD)
    ap.add_argument("--quick", action="store_true", help="跳过能量离群维度")
    args = ap.parse_args()

    root = args.root
    if not root.is_dir():
        print(f"FATAL: root 不存在 {root}", file=sys.stderr)
        return 2
    non_scf = {s.strip() for s in args.non_scf.split(",") if s.strip()}

    systems: list[dict] = []
    all_recs: list[dict] = []
    n_skipped = 0
    for group in sorted(p for p in root.iterdir()
                        if p.is_dir() and not p.name.startswith((".", "_"))):
        recs = []
        for d in sorted(p for p in group.rglob("*") if p.is_dir()):
            if any(x in str(d) for x in ("__pycache__", ".git", ".big_sc_bak", "defect_new")):
                continue
            if not (d / "OUTCAR").exists() and not (d / "POTCAR").exists():
                continue
            rec = scan_dir(d, non_scf)
            recs.append(rec)
            all_recs.append(rec)

        n_skip = sum(1 for r in recs if r.get("skipped"))
        n_skipped += n_skip
        active = [r for r in recs if not r.get("skipped")]
        n_conv = sum(1 for r in active if r.get("converged"))
        n_exempt = sum(1 for r in active if not r.get("converged") and r.get("exempt"))
        n_flat = sum(1 for r in active if r.get("energy_flat"))
        n_backlog = sum(1 for r in active if not r["ok"] and "未收敛" in r["issues"][0])
        n_noout = sum(1 for r in active if not r["ok"] and r["issues"][0] == "无 OUTCAR")
        n_failres = sum(1 for r in active if not r["ok"] and "OUTCAR 过小" in r["issues"][0])

        # ---- 支柱 2: 体系内可比性 ----
        # 2a. POTCAR 变体/日期统一
        tokens = collections.defaultdict(set)
        for r in active:
            for token, date in r.get("titel", []):
                tokens[token].add(date)
        mix_issues = []
        for tok in sorted(tokens):
            if len(tokens[tok]) > 1:
                mix_issues.append(f"{tok}: {sorted(tokens[tok])}")
        # 跨目录 token 名差异(如 Ga vs Ga_d): 按元素前缀归组
        el_tokens: dict[str, set] = collections.defaultdict(set)
        for r in active:
            for token, date in r.get("titel", []):
                el_tokens[token.split("_")[0]].add(token)
        for el in sorted(el_tokens):
            if len(el_tokens[el]) > 1:
                mix_issues.append(f"{el}: tokens={sorted(el_tokens[el])}")

        # 2b. 物理 key 一致(体系内全部互比; LDAUU/LDAUL 按元素映射)
        key_diffs: dict[str, set] = collections.defaultdict(set)
        incar_sets: dict[str, set] = collections.defaultdict(set)
        for r in active:
            inc = r.get("incar", {})
            for k in PHYSICAL_KEYS:
                if k in ("LDAUU", "LDAUL"):
                    mapped = lda_el_mapped(inc, r.get("titel", []))
                    vals = mapped.get(k) or mapped.get(k + "_raw") or {None}
                elif k in ("ENCUT", "EDIFF", "EDIFFG", "SIGMA"):
                    raw = inc.get(k)
                    try:  # 数值归一化(1e-05 == 1e-5 == 0.00001)
                        vals = {str(float(raw))} if raw is not None else set()
                    except ValueError:
                        vals = {raw} if raw is not None else set()
                else:
                    vals = {inc.get(k)} if inc.get(k) is not None else set()
                incar_sets[k].update(vals)
        for k in PHYSICAL_KEYS:
            if len(incar_sets[k]) > 1:
                key_diffs[k] = incar_sets[k]
        rec_only_diffs: dict[str, set] = {}
        for k in RECORD_ONLY_KEYS:
            s: set = set()
            for r in active:
                v = r.get("incar", {}).get(k)
                if v is not None:
                    s.add(v)
            if len(s) > 1:
                rec_only_diffs[k] = s

        # 2c. 零点一致性(体系级: cpd composition 来源 vs defect 链变体)
        zero_issues = zero_point_check(group)

        comparable = not mix_issues and not key_diffs and not zero_issues
        sys_rec = {
            "name": group.name,
            "n_dirs": len(active),
            "n_skipped": n_skip,
            "convergence": {
                "converged": n_conv,
                "exempt": n_exempt,
                "energy_flat": n_flat,
                "backlog_unconverged": n_backlog,
                "no_outcar": n_noout,
                "failed_residual": n_failres,
            },
            "comparability": {
                "ok": comparable,
                "variant_mixes": mix_issues,
                "key_diffs": {k: sorted(v, key=str) for k, v in key_diffs.items()},
                "record_only_diffs": {k: sorted(v, key=str) for k, v in rec_only_diffs.items()},
                "zero_point": zero_issues,
            },
        }
        systems.append(sys_rec)

        # 验收表行
        n_problems = n_backlog + n_noout + n_failres
        conv_tag = "✗" if n_problems > 0 else ("~" if n_flat > 0 else "✓")
        cmp_tag = "✓" if comparable else "✗"
        evidence = []
        if mix_issues:
            evidence.append("变体:" + ";".join(mix_issues))
        if key_diffs:
            evidence.append("key:" + ";".join(
                f"{k}{sorted(str(v if v is not None else '(absent)') for v in vs)}"
                for k, vs in key_diffs.items()))
        if zero_issues:
            evidence.append("零点:" + ";".join(zero_issues))
        if rec_only_diffs:
            evidence.append("记录:" + ";".join(f"{k}{sorted(v)}" for k, v in rec_only_diffs.items()))
        backlog = f"{n_backlog}未收敛/{n_noout}无Out/{n_failres}失败残留"
        print(f"{group.name:26s} 收敛[{conv_tag}] {n_conv}/{len(active)} 可比[{cmp_tag}] "
              f"{'; '.join(evidence) if evidence else '-'} 欠账[{backlog}]")

    # 批次汇总
    n_trusted = sum(1 for s in systems if s["comparability"]["ok"])
    n_untrusted = len(systems) - n_trusted
    n_sys_backlog = sum(1 for s in systems
                        if s["convergence"]["backlog_unconverged"]
                        or s["convergence"]["no_outcar"]
                        or s["convergence"]["failed_residual"])
    print(f"\n=== 批次汇总: {len(systems)} 体系, 可比可信 {n_trusted}, 不可信 {n_untrusted}, "
          f"有欠账 {n_sys_backlog}, 排除跳过 {n_skipped} ===")

    report = {
        "root": str(root),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "systems": systems,
    }
    if args.json:
        args.json.write_text(json.dumps(report, indent=1))
        print(f"JSON 报告: {args.json}")

    if args.report:
        rows = []
        for s in systems:
            cv = s["convergence"]
            if cv["backlog_unconverged"] or cv["no_outcar"] or cv["failed_residual"]:
                conv_cls, conv_txt = "bad", "✗"
            elif cv["energy_flat"]:
                conv_cls, conv_txt = "warn", "~"
            else:
                conv_cls, conv_txt = "ok", "✓"
            cmp_cls, cmp_txt = ("ok", "✓") if s["comparability"]["ok"] else ("bad", "✗")
            ev = []
            for m in s["comparability"]["variant_mixes"]:
                ev.append(f"变体 {html.escape(m)}")
            for k, vs in s["comparability"]["key_diffs"].items():
                ev.append(f"{html.escape(k)} 不一致 "
                          f"{html.escape(str(sorted(str(v if v is not None else '(absent)') for v in vs)))}")
            for z in s["comparability"]["zero_point"]:
                ev.append(f"零点 {html.escape(z)}")
            for k, vs in s["comparability"]["record_only_diffs"].items():
                ev.append(f"记录级 {html.escape(k)} {html.escape(str(sorted(vs)))}")
            backlog = (f"{cv['backlog_unconverged']}未收敛 / {cv['no_outcar']}无Out / "
                       f"{cv['failed_residual']}失败残留")
            conv_disp = (f"{conv_txt} {cv['converged']}/{s['n_dirs']}"
                         + (f" · {cv['energy_flat']} flat" if cv["energy_flat"] else "")
                         + (f" · {cv['exempt']} 豁免" if cv["exempt"] else ""))
            rows.append(
                f"<tr><td class='sys'>{html.escape(s['name'])}</td>"
                f"<td class='{conv_cls} mon'>{conv_disp}</td>"
                f"<td class='{cmp_cls}'>{cmp_txt}</td>"
                f"<td class='ev'>{'; '.join(ev) if ev else '−'}</td>"
                f"<td class='mon'>{backlog}</td></tr>"
            )
        page = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>VASP 批次结果验收报告</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Noto Sans CJK SC", sans-serif;
         margin: 2rem auto; max-width: 1100px; padding: 0 1rem; color: #222; }}
  h1 {{ font-size: 1.3rem; }}
  .meta {{ color: #666; font-size: .85rem; margin: .5rem 0 1.2rem; }}
  .summary {{ background: #f5f6f8; border-radius: 6px; padding: .6rem 1rem;
              font-size: .9rem; margin-bottom: 1.2rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .85rem; }}
  th, td {{ border: 1px solid #ddd; padding: .45rem .6rem; text-align: left;
            vertical-align: top; }}
  th {{ background: #eef0f3; white-space: nowrap; }}
  td.sys {{ font-weight: 600; white-space: nowrap; }}
  td.mon {{ font-family: ui-monospace, "SF Mono", Consolas, monospace; font-size: .78rem; }}
  td.ev {{ font-size: .8rem; }}
  .ok {{ color: #1a7f37; font-weight: 600; }}
  .warn {{ color: #b76e00; font-weight: 600; }}
  .bad {{ color: #c62828; font-weight: 600; }}
  .legend {{ margin-top: 1.2rem; font-size: .8rem; color: #555; line-height: 1.6; }}
</style></head><body>
<h1>VASP 批次结果验收报告</h1>
<div class="meta">root: <code>{html.escape(str(root))}</code> · {html.escape(report['ts'])}</div>
<div class="summary">体系 {len(systems)} · 可比可信 <b class="ok">{n_trusted}</b> ·
  不可信 <b class="bad">{n_untrusted}</b> · 有欠账 {n_sys_backlog} · 排除跳过 {n_skipped}</div>
<table>
<thead><tr><th>体系</th><th>收敛</th><th>可比</th><th>证据</th><th>欠账</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody></table>
<div class="legend">
<b>图例</b><br>
收敛: ✓ 全部收敛(含豁免) · ~ 无硬欠账但 NELM 边缘批量(energy-flat) · ✗ 有未收敛/无OUTCAR/失败残留<br>
可比: ✓ 物理 key 一致 + POTCAR 变体统一 + 零点一致 · ✗ 任一不满足(门禁)<br>
判据源: OUTCAR 回显; 白名单记录级: ISMEAR, NSW/IBRION/KPAR/NCORE/ISYM
</div></body></html>
"""
        args.report.write_text(page)
        print(f"HTML 报告: {args.report}")

    # 体系级回归对比
    new_untrusted = 0
    if args.compare and args.compare.exists():
        prev = json.loads(args.compare.read_text())
        prev_ok = {s["name"] for s in prev.get("systems", []) if s["comparability"]["ok"]}
        now_ok = {s["name"] for s in systems if s["comparability"]["ok"]}
        regressed = sorted(prev_ok - now_ok)
        fresh = sorted(now_ok - prev_ok)
        if regressed:
            print("\n=== 与上次对比: 可比性回归 ===")
            for s in regressed:
                print(f"  REGRESS {s}")
                new_untrusted += 1
        if fresh:
            print("\n=== 与上次对比: 恢复可信 ===")
            for s in fresh:
                print(f"  FIXED {s}")
        if not regressed and not fresh:
            print("\n=== 与上次对比: 无体系级变化 ===")
    bad = n_untrusted or new_untrusted
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
