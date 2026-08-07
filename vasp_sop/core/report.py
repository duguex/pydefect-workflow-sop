"""Evidence-based calculation report generation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

import yaml

from vasp_sop.vasp.convergence import convergence_verdict


_MARKER = "reached required accuracy - stopping structural energy minimisation"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_incar(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(errors="replace").splitlines():
        match = re.match(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.*?)\s*(?:!.*)?$", line)
        if match:
            values[match.group(1).upper()] = match.group(2).strip()
    return values


def _parse_potcar(path: Path) -> list[str]:
    titles: list[str] = []
    if not path.is_file():
        return titles
    for line in path.read_text(errors="replace").splitlines():
        if "TITEL" not in line or "=" not in line:
            continue
        value = line.split("=", 1)[1].strip()
        tokens = value.split()
        if tokens:
            titles.append(
                tokens[1] if tokens[0] == "PAW_PBE" and len(tokens) > 1 else tokens[0]
            )
    return titles


def _is_converged(path: Path) -> bool:
    outcar = path / "OUTCAR"
    if not outcar.is_file():
        return False
    try:
        text = outcar.read_text(errors="replace")
    except OSError:
        return False
    if _MARKER in text:
        return True
    try:
        return convergence_verdict(path).converged
    except Exception:
        return False


def _phase_status(path: Path) -> str:
    if (path / ".failed").is_file():
        return "failed/latest attempt"
    if _is_converged(path):
        return "completed/converged"
    if (path / "OUTCAR").is_file():
        return "output present/not converged"
    if all(
        (path / name).is_file() for name in ("INCAR", "POSCAR", "POTCAR", "KPOINTS")
    ):
        return "inputs ready"
    if (path / "POSCAR").is_file():
        return "structure only"
    return "not initialized"


def _structure_summary(path: Path) -> dict[str, Any]:
    try:
        from pymatgen.core import Structure

        structure = Structure.from_file(str(path))
        return {
            "formula": structure.composition.reduced_formula,
            "natoms": len(structure),
            "abc": [round(value, 6) for value in structure.lattice.abc],
            "angles": [round(value, 6) for value in structure.lattice.angles],
        }
    except Exception:
        return {}


def _target_entry(
    composition: dict[str, Any], target_name: str | None
) -> tuple[str, dict[str, Any]] | None:
    for key, value in composition.items():
        if isinstance(value, dict) and value.get("source") == target_name:
            return key, value
    return None


def _evidence(value: Any, source: Path | str, status: str) -> dict[str, Any]:
    return {"value": value, "source": str(source), "status": status}


def _path_evidence(path: Path, value: Any = None) -> dict[str, Any]:
    if not path.exists():
        return _evidence("—", path, "未找到")
    return _evidence(value if value is not None else str(path), path, "已读取")


def _convergence_evidence(path: Path) -> dict[str, Any]:
    outcar = path / "OUTCAR"
    if not outcar.is_file():
        return _evidence("—", outcar, "未找到")
    try:
        text = outcar.read_text(errors="replace")
    except OSError:
        return _evidence("—", outcar, "未找到")
    converged = _MARKER in text
    if not converged:
        try:
            converged = convergence_verdict(path).converged
        except Exception:
            converged = False
    value = "completed/converged" if converged else "output present/not converged"
    return _evidence(value, outcar, "已读取")


def _last_total_energy(path: Path) -> float | None:
    if not path.is_file():
        return None
    text = path.read_text(errors="replace")
    matches = re.findall(
        r"(?:free\s+energy\s+TOTEN|energy\s+without\s+entropy)\s*=\s*"
        r"([-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?)",
        text,
    )
    return float(matches[-1]) if matches else None


def _reduced_formula(formula: str) -> str:
    try:
        from pymatgen.core import Composition

        return Composition(formula).reduced_formula
    except Exception:
        return "—"


def _process_rows(
    cpd_root: Path,
    composition: dict[str, Any],
    relative: dict[str, Any],
    standard: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for composition_key, item in sorted(composition.items()):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", ""))
        outcar = cpd_root / source / "OUTCAR"
        normalized = _reduced_formula(composition_key)
        relative_key = normalized if normalized in relative else composition_key
        standard_key = normalized if normalized in standard else composition_key
        rows.append(
            {
                "composition_key": composition_key,
                "normalized_formula": normalized,
                "source": source,
                "outcar": outcar,
                "raw_toten": _last_total_energy(outcar),
                "mce_energy": item.get("energy"),
                "relative_energy": relative.get(relative_key),
                "relative_source": relative_key if relative_key in relative else None,
                "standard_energy": standard.get(standard_key),
                "standard_source": standard_key if standard_key in standard else None,
            }
        )
    return rows


def collect_report_data(system_dir: Path) -> dict[str, Any]:
    """Collect report facts from *system_dir* without submitting or modifying jobs."""
    root = system_dir.resolve()
    plan = _load_yaml(root / "plan.yaml")
    project = plan.get("project", {}) if isinstance(plan.get("project"), dict) else {}
    parameters = (
        plan.get("parameters", {}) if isinstance(plan.get("parameters"), dict) else {}
    )
    correction_policy = str(plan.get("correction_policy", "custom_molecular_reference"))
    formula = str(project.get("formula", root.name))

    cpd_root = root / "cpd"
    phase_dirs = (
        sorted(path for path in cpd_root.iterdir() if path.is_dir())
        if cpd_root.is_dir()
        else []
    )
    target_candidates = [
        path for path in phase_dirs if path.name.startswith(f"{formula}_mp-")
    ]
    target_name = target_candidates[0].name if len(target_candidates) == 1 else None
    other_phase_dirs = [path for path in phase_dirs if path.name != target_name]
    target_output = root / "unitcell" / "structure_opt"
    target_structure = _structure_summary(target_output / "CONTCAR")
    if not target_structure:
        target_structure = _structure_summary(target_output / "POSCAR")
    target_incar = _parse_incar(target_output / "INCAR")
    target_potcar = _parse_potcar(target_output / "POTCAR")

    composition_path = cpd_root / "composition_energies.yaml"
    relative_path = cpd_root / "relative_energies.yaml"
    standard_path = cpd_root / "standard_energies.yaml"
    vertices_path = cpd_root / "target_vertices.yaml"
    composition = _load_yaml(composition_path)
    relative = _load_yaml(relative_path)
    standard = _load_yaml(standard_path)
    vertices = _load_yaml(vertices_path)
    target_energy = _target_entry(composition, target_name)

    duplicate_formulas: dict[str, int] = {}
    for path in other_phase_dirs:
        phase_formula = path.name.split("_mp-", 1)[0]
        duplicate_formulas[phase_formula] = duplicate_formulas.get(phase_formula, 0) + 1
    duplicate_formulas = {
        key: value for key, value in duplicate_formulas.items() if value > 1
    }

    warnings: list[str] = []
    if target_incar.get("ISPIN") == "2" and "MAGMOM" not in target_incar:
        warnings.append(
            "未设置 MAGMOM；自旋计算使用 VASP 默认初始磁矩，磁态未充分验证。"
        )
    if target_name is None:
        warnings.append("无法唯一定位 cpd 目标目录。")
    elif not (cpd_root / target_name / "OUTCAR").is_file():
        warnings.append("cpd 目标目录缺少 OUTCAR；目标能量未交接。")
    if duplicate_formulas:
        warnings.append(
            "存在同一化学式的多个 MP 目录：" + ", ".join(sorted(duplicate_formulas))
        )
    if not composition_path.is_file():
        warnings.append("composition_energies.yaml 缺失，CPD 能量未完成。")
    if not vertices_path.is_file():
        warnings.append("target_vertices.yaml 缺失，化学势范围未完成。")

    structure_path = (
        target_output / "CONTCAR"
        if (target_output / "CONTCAR").is_file()
        else target_output / "POSCAR"
    )
    incar_path = target_output / "INCAR"
    corrections = plan.get("corrections", {})
    if not isinstance(corrections, dict):
        corrections = {}
    defect_root = root / "defect"
    defect_summary = defect_root / "defect_energy_summary.json"
    if not defect_root.is_dir():
        defect_evidence = _evidence("未执行", defect_root, "未执行")
    elif defect_summary.is_file():
        defect_evidence = _evidence("已生成", defect_summary, "已读取")
    else:
        defect_evidence = _evidence("—", defect_summary, "未找到")

    evidence: dict[str, dict[str, Any]] = {
        "plan.yaml": _path_evidence(root / "plan.yaml", "配置已读取"),
        "目标结构": _path_evidence(
            structure_path, target_structure.get("formula", "未解析")
        ),
        "主相收敛证据": _convergence_evidence(target_output),
        "INCAR": _path_evidence(incar_path, "关键参数已读取"),
        "ENCUT": _evidence(
            target_incar.get("ENCUT", "—"),
            incar_path,
            "已读取" if "ENCUT" in target_incar else "未找到",
        ),
        "KPOINTS": _path_evidence(target_output / "KPOINTS", "文件存在"),
        "POTCAR": _path_evidence(
            target_output / "POTCAR", ", ".join(target_potcar) or "未解析"
        ),
        "composition_energies.yaml": _path_evidence(composition_path, "mce 输出已读取"),
        "目标能量": (
            _evidence(target_energy, composition_path, "已读取")
            if target_energy
            else _evidence("—", composition_path, "未找到")
        ),
        "relative_energies.yaml": _path_evidence(relative_path, "sre 输出已读取"),
        "standard_energies.yaml": _path_evidence(standard_path, "sre 输出已读取"),
        "target_vertices.yaml": _path_evidence(vertices_path, "CPD 顶点已读取"),
        "化学势图谱": _path_evidence(cpd_root / "chem_pot_diag.json", "图谱数据已读取"),
        "CPD 图 PDF": _path_evidence(cpd_root / "cpd.pdf", "图文件已读取"),
        "缺陷摘要": defect_evidence,
        "git revision": _evidence("—", root / ".git", "不采集"),
    }
    for task in ("band", "dos", "dielectric"):
        task_dir = root / "unitcell" / task
        evidence[f"unitcell/{task}"] = (
            _evidence("未执行", task_dir, "未执行")
            if not task_dir.is_dir()
            else _path_evidence(task_dir, "目录存在")
        )
    process_rows = _process_rows(cpd_root, composition, relative, standard)
    process_by_key = {row["composition_key"]: row for row in process_rows}
    correction_rows = []
    for correction_name, correction_value in corrections.items():
        matched = correction_name if correction_name in composition else None
        process = process_by_key.get(matched) if matched else None
        raw_toten = process.get("raw_toten") if process else None
        mce_energy = process.get("mce_energy") if process else None
        if isinstance(raw_toten, (int, float)) and isinstance(mce_energy, (int, float)):
            applied_delta = mce_energy - raw_toten
        else:
            applied_delta = None
        if matched is None:
            status = "未找到（未应用）"
        elif applied_delta is None:
            status = "已匹配（应用状态不可核验）"
        elif abs(applied_delta - correction_value) <= 1e-6:
            status = "已应用"
        else:
            status = "已匹配（实际增量与配置不符）"
        correction_rows.append(
            {
                "configured": correction_name,
                "value": correction_value,
                "source": root / "plan.yaml",
                "matched_key": matched or "—",
                "raw_toten": raw_toten,
                "mce_energy": mce_energy,
                "applied_delta": applied_delta,
                "status": status,
            }
        )

    return {
        "root": root,
        "formula": formula,
        "target_source": project.get("poscar_src", ""),
        "functional": parameters.get("functional", "unknown"),
        "hubbard_u": parameters.get("hubbard_u", False),
        "plan_pp": parameters.get("pp", []),
        "target_name": target_name,
        "target_status": _phase_status(target_output),
        "correction_policy": correction_policy,
        "target_structure": target_structure,
        "target_incar": target_incar,
        "target_potcar": target_potcar,
        "phase_dirs": [(path.name, _phase_status(path)) for path in phase_dirs],
        "other_phase_dirs": [
            (path.name, _phase_status(path)) for path in other_phase_dirs
        ],
        "composition_path": composition_path,
        "composition": composition,
        "target_energy": target_energy,
        "relative_path": relative_path,
        "relative": relative,
        "standard_path": standard_path,
        "standard": standard,
        "vertices_path": vertices_path,
        "vertices": vertices,
        "duplicate_formulas": duplicate_formulas,
        "warnings": warnings,
        "evidence": evidence,
        "process_rows": process_rows,
        "correction_rows": correction_rows,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _render_table(rows: list[tuple[str, ...]]) -> str:
    if not rows:
        return "（无）"
    header = rows[0]
    body = rows[1:]
    text = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    text.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(text)


def render_report(data: dict[str, Any]) -> str:
    """Render collected report facts as Markdown."""
    structure = data["target_structure"]
    incar = data["target_incar"]
    potcar = ", ".join(data["target_potcar"]) or "未解析"
    phase_rows = [("竞争相目录", "状态")]
    phase_rows.extend((name, status) for name, status in data["other_phase_dirs"])

    composition_rows = [("组成键", "能量", "来源")]
    for key, value in sorted(data["composition"].items()):
        if isinstance(value, dict):
            composition_rows.append(
                (key, str(value.get("energy", "")), str(value.get("source", "")))
            )

    vertex_rows = [("顶点", "Δμ(Cl)", "Δμ(Cs)", "Δμ(Eu)", "竞争相")]
    for name, value in data["vertices"].items():
        if name == "target" or not isinstance(value, dict):
            continue
        chem_pot = value.get("chem_pot", {})
        competitors = ", ".join(value.get("competing_phases", []))
        vertex_rows.append(
            (
                name,
                str(chem_pot.get("Cl", "")),
                str(chem_pot.get("Cs", "")),
                str(chem_pot.get("Eu", "")),
                competitors,
            )
        )

    def display(value: Any) -> str:
        if value is None or value == "":
            return "—"
        if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], dict):
            key, details = value
            return (
                f"{key} = {details.get('energy', '—')} eV; "
                f"source={details.get('source', '—')}"
            )
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    evidence_rows = [("字段", "值", "source", "status")]
    for field, item in data["evidence"].items():
        evidence_rows.append(
            (
                field,
                display(item.get("value")),
                display(item.get("source")),
                display(item.get("status")),
            )
        )

    derivation_rows = [
        (
            "来源相",
            "组成键 → 归一化",
            "OUTCAR TOTEN 重解析 (eV)",
            "mce YAML (eV)",
            "relative (eV/atom)",
            "standard (eV/atom)",
        )
    ]
    for row in data["process_rows"]:
        derivation_rows.append(
            (
                row["source"],
                f"{row['composition_key']} → {row['normalized_formula']}",
                display(row["raw_toten"]),
                display(row["mce_energy"]),
                display(row["relative_energy"]),
                display(row["standard_energy"]),
            )
        )

    correction_rows = [
        (
            "配置校正",
            "配置值 (eV)",
            "匹配组成键",
            "OUTCAR raw (eV)",
            "mce (eV)",
            "实际增量 (eV)",
            "status",
            "source",
        )
    ]
    for row in data["correction_rows"]:
        correction_rows.append(
            (
                row["configured"],
                display(row["value"]),
                row["matched_key"],
                display(row["raw_toten"]),
                display(row["mce_energy"]),
                display(row["applied_delta"]),
                row["status"],
                str(row["source"]),
            )
        )

    warnings = "\n".join(f"- {warning}" for warning in data["warnings"]) or "- 无"
    structure_lines = "- 未解析"
    if structure:
        structure_lines = "\n".join(
            [
                f"- 化学式：`{structure['formula']}`",
                f"- 原子数：{structure['natoms']}",
                f"- 晶格长度：`{', '.join(f'{value:.6f} Å' for value in structure['abc'])}`",
                f"- 晶格角：`{', '.join(f'{value:.6f}°' for value in structure['angles'])}`",
            ]
        )

    target_energy_text = "未找到"
    if data["target_energy"]:
        key, value = data["target_energy"]
        target_energy_text = f"`{key}` = `{value.get('energy', 'unknown')}` eV, source=`{value.get('source', '')}`"

    return f"""# {data["formula"]} 计算报告

**报告生成时间（UTC）：** {data["generated_at"]}  
**体系目录：** `{data["root"]}`  
**报告来源：** 当前文件系统、YAML、INCAR/POTCAR、CONTCAR 与 OUTCAR；本报告不是复制已有 Markdown。

## 1. 计算范围

- 主相结构优化：{data["target_status"]}
- CPD 目标目录：{data["target_name"] or "未定位"}
- CPD 竞争相目录：{len(data["other_phase_dirs"])} 个
- 缺陷计算：未由本命令执行
- unitcell band/DOS/dielectric：未由本命令执行

## 2. 输入设置

| 项目 | 值 |
|---|---|
| 化学式 | `{data["formula"]}` |
| 结构来源 | `{data["target_source"]}` |
| 泛函 | `{data["functional"]}` |
| Hubbard U | `{data["hubbard_u"]}` |
| POTCAR | `{potcar}` |
| ISPIN | `{incar.get("ISPIN", "未设置")}` |
| LDAU | `{incar.get("LDAU", "未设置")}` |
| LDAUL | `{incar.get("LDAUL", "未设置")}` |
| LDAUU | `{incar.get("LDAUU", "未设置")}` |

## 3. 主相结构

{structure_lines}

## 4. CPD 竞争相状态

{_render_table(phase_rows)}

## 5. 计算过程 / 能量推导链

1. **VASP 输出 → mce 输入：** `pydefect_vasp mce` 使用各目录的 `OUTCAR.final_energy` 与 `CONTCAR.composition`；其组成能量写入 `composition_energies.yaml`，单位为实际结构总能量 eV。
2. **独立原始值核对：** 报告另外从 `OUTCAR` 重解析最后一个 `TOTEN`/`energy without entropy`，单位为 eV；该值仅作核对，不能未经比较就宣称等同 mce 值。
3. **组成归一化：** 使用 pymatgen `Composition(...).reduced_formula`，例如 `Cs4Eu4Cl12 → CsEuCl3`。
4. **分子校正：** 读取 `plan.yaml` 的 correction 配置；报告单独审计配置值和组成键是否实际匹配，不把未匹配校正写入 mce 能量。
5. **sre：** `CompositionEnergies.std_rel_energies` 将总能量按组成原子数归一化，再减去元素参考能，生成 `standard_energies.yaml` 与 `relative_energies.yaml`；这两类值按 pydefect 输出定义为 eV/atom。
6. **化学势约束：** 以 relative energies 中的目标相和竞争相构造相稳定性约束，生成 `target_vertices.yaml`；顶点中的 Δμ 单位为 eV。

{_render_table(derivation_rows)}

## 6. 分子校正审计

校正值来自 `plan.yaml`；“未找到（未应用）”表示配置名没有与 `composition_energies.yaml` 的组成键字面匹配，不能把它解释为已校正。
校正策略：`{data["correction_policy"]}`。
当前 `plan.yaml` 的分子校正是本项目的自定义元素参考能量平移；它把校正加到分子参考条目上，不等同于 Materials Project 2020 阴离子校正。MP2020 将校正施加到化合物中作为阴离子的元素，元素参考气体（H2、N2、O2、F2、Cl2）不施加该阴离子校正。

{_render_table(correction_rows)}

## 7. CPD 能量

目标能量：{target_energy_text}

{_render_table(composition_rows)}

## 8. 化学势范围

目标顶点文件：`{data["vertices_path"]}`

{_render_table(vertex_rows)}

## 9. 证据状态

每个“证据状态”表字段同时报告值、source 和证据状态：`已读取`、`未找到`、`不适用`、`未执行`或`不采集`。推导链表另列来源相、YAML 来源列和各阶段数值。

{_render_table(evidence_rows)}

## 10. 警告与限制

{warnings}

## 11. 关键文件

- `plan.yaml`
- `unitcell/structure_opt/CONTCAR`
- `unitcell/structure_opt/OUTCAR`
- `cpd/composition_energies.yaml`
- `cpd/relative_energies.yaml`
- `cpd/standard_energies.yaml`
- `cpd/target_vertices.yaml`
- `cpd/chem_pot_diag.json`
- `cpd/cpd.pdf`
"""


def generate_report(system_dir: Path, output: Path | None = None) -> Path:
    """Generate a report from current project artifacts and return its path."""
    root = Path(system_dir).resolve()
    if not root.is_dir():
        raise ValueError(f"System directory does not exist: {root}")
    report_path = (output or (root / "calculation_report.md")).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(collect_report_data(root)))
    return report_path
