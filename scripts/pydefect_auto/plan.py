"""plan.yaml 生成、校验、读写"""

import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

import yaml

logger = logging.getLogger("pydefect_auto")

PLAN_FILENAME = "plan.yaml"

DEFAULT_PLAN = {
    "project": {
        "obj": "",
        "dopant_elements": [],
        "poscar_src": "",
        "poscar": "unitcell/structure_opt/POSCAR",
    },
    "parameters": {
        "functional": "pbesol",
        "encut": None,
        "hubbard_u": False,
        "pp": [],
    },
    "supercell": {"max_atoms": 600, "min_atoms": 200},
    "defects": {
        "vacancies": [],
        "substitutionals": [],
        "interstitials": False,
        "iindex": [],
        "charge_states": [-2, -1, 0, 1, 2],
        "complex_n": 1,
        "max_remote": 5.0,
    },
    "cpd": {
        "gas_corrections": {"O2": 1.374, "Cl2": 1.228, "F2": 0.924},
    },
    "crisp": {"cluster": None},
    "stages": {
        "unitcell": True,
        "cpd": True,
        "defect_gen": True,
        "submit": True,
        "postproc": True,
        "doping": False,
        "complex": False,
    },
}


def generate_plan(project_dir, obj, dopant_elements=None, poscar_src=None,
                   functional="pbesol", **kwargs):
    root = Path(project_dir)
    plan = _deep_copy(DEFAULT_PLAN)

    plan["project"]["obj"] = obj
    plan["project"]["dopant_elements"] = dopant_elements or []
    plan["parameters"]["functional"] = functional

    # ① POSCAR
    poscar_dst = root / "unitcell" / "structure_opt" / "POSCAR"
    poscar_dst.parent.mkdir(parents=True, exist_ok=True)

    available_phases = []

    if poscar_src:
        src = Path(poscar_src)
        if not src.exists():
            raise FileNotFoundError(f"POSCAR not found: {poscar_src}")
        shutil.copy2(str(src), str(poscar_dst))
        plan["project"]["poscar_src"] = f"local: {src.resolve()}"
        _report_poscar(poscar_dst)
    else:
        logger.info("Querying MP for %s phases...", obj)
        available_phases = _query_mp_phases(obj, root, poscar_dst)
        if available_phases:
            chosen = available_phases[0]
            plan["project"]["poscar_src"] = f"MP mp-{chosen['mpid']}"
            _report_poscar(poscar_dst)
            logger.info("Default: %s (%s, E_form=%.3f eV/atom)",
                        chosen["mpid"], chosen["spg"], chosen["energy"])
        else:
            logger.warning("MP download failed. Place POSCAR manually at %s", poscar_dst)

    plan["project"]["poscar"] = "unitcell/structure_opt/POSCAR"

    # ② ENCUT
    encut = kwargs.get("encut")
    if not encut and poscar_dst.exists():
        encut = _detect_encut(poscar_dst, plan["parameters"]["functional"],
                               plan["parameters"]["hubbard_u"],
                               plan["parameters"]["pp"])
    plan["parameters"]["encut"] = encut

    # ③ Auto-detect DFT+U
    if poscar_dst.exists() and not plan["parameters"]["hubbard_u"]:
        try:
            from vise.input_set.datasets.dataset_util import LDAU
            from pymatgen.core import Structure
            symbols = [s.species_string for s in Structure.from_file(str(poscar_dst))]
            if LDAU(symbols).is_ldau_needed:
                plan["parameters"]["hubbard_u"] = True
                logger.info("DFT+U auto-enabled (elements: %s)", ", ".join(symbols))
        except ImportError:
            pass
        except Exception as e:
            logger.warning("DFT+U detection failed: %s", e)

    # ④ Infer defects
    intrinsic = _extract_elements(obj)
    dopants = plan["project"]["dopant_elements"]
    plan["defects"]["vacancies"] = intrinsic
    sub_list = []
    for d in dopants:
        for host in intrinsic:
            sub_list.append({"impurity": d, "site": host})
    plan["defects"]["substitutionals"] = sub_list

    # ⑤ Apply overrides from kwargs
    for k, v in kwargs.items():
        if k in ("encut",):
            continue
        _set_nested(plan, k, v)

    return plan, available_phases


def _report_poscar(poscar_path):
    from pymatgen.core import Structure
    s = Structure.from_file(str(poscar_path))
    logger.info("POSCAR: %s (%.3f %.3f %.3f, %d atoms, %s)",
                s.composition.reduced_formula,
                s.lattice.a, s.lattice.b, s.lattice.c,
                s.num_sites, s.get_space_group_info()[0])


def _query_mp_phases(obj, root, dst_path):
    """Query all MP phases matching obj. Download only the default POSCAR."""
    dst_path = Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    from mp_api.client import MPRester
    with MPRester() as mpr:
        docs = mpr.materials.summary.search(
            formula=obj,
            fields=["material_id", "formation_energy_per_atom", "symmetry",
                    "structure", "energy_above_hull"],
        )

    phases = []
    for d in docs:
        spg = d.symmetry.symbol if d.symmetry else "?"
        s = d.structure
        phases.append({
            "mpid": d.material_id.replace("mp-", ""),
            "spg": spg,
            "formula": s.composition.reduced_formula,
            "energy": round(d.formation_energy_per_atom, 4) if d.formation_energy_per_atom else 999.0,
            "poscar": None,
            "n_atoms": s.num_sites,
            "a": round(s.lattice.a, 3),
            "b": round(s.lattice.b, 3),
            "c": round(s.lattice.c, 3),
        })

    if not phases:
        logger.warning("No phases found for %s on MP", obj)
        return []

    phases.sort(key=lambda p: p["energy"])

    # Download the most stable phase's POSCAR via pydefect_vasp mp
    chosen = phases[0]
    tmp_root = Path(tempfile.mkdtemp(dir=str(root)))
    try:
        cmd = f"pydefect_vasp mp -e {_obj_to_elements(obj)} --e_above_hull 0.0005"
        _run_cmd(cmd, cwd=str(tmp_root))
        for p in tmp_root.iterdir():
            if p.is_dir() and chosen["mpid"] in p.name:
                for f in list(p.glob("POSCAR*")) + list(p.glob("CONTCAR*")):
                    shutil.copy2(str(f), str(dst_path))
                    break
    finally:
        shutil.rmtree(str(tmp_root), ignore_errors=True)

    return phases





def _obj_to_elements(obj):
    return " ".join(_extract_elements(obj))


def _extract_elements(formula):
    return re.findall(r'[A-Z][a-z]?', formula)


def _detect_encut(poscar_path, functional, hubbard_u, pp):
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        shutil.copy(str(poscar_path), str(tmpd / "POSCAR"))
        cmd = f"vise vasp_set -x {functional}"
        if hubbard_u:
            cmd += " --options set_hubbard_u True"
        if pp:
            cmd += " " + " ".join(f"--potcar {p}" for p in pp)
        r = _run_cmd(cmd, cwd=str(tmpd))
        if r != 0:
            return None
        potcar_path = tmpd / "POTCAR"
        if not potcar_path.exists():
            return None
        enmax = _read_enmax(str(potcar_path))
        if enmax:
            encut = 1.3 * enmax
            logger.info("ENCUT = 1.3 × max(ENMAX) = 1.3 × %.1f = %.0f", enmax, encut)
            return round(encut)
    return None


def _read_enmax(potcar_path):
    max_enmax = 0.0
    with open(potcar_path) as f:
        for line in f:
            if "ENMAX" in line:
                for part in line.strip().split(";"):
                    if "ENMAX" in part:
                        val = float(part.split("=")[-1].strip())
                        max_enmax = max(max_enmax, val)
    return max_enmax if max_enmax > 0 else None


def _run_cmd(cmd, cwd=None):
    import subprocess
    r = subprocess.run(cmd, shell=True, cwd=cwd,
                       capture_output=True, text=True)
    if r.returncode != 0:
        logger.warning("Command failed: %s\n%s", cmd, r.stderr[:200])
    return r.returncode


def write_plan(project_dir, plan, available_phases=None):
    path = Path(project_dir) / PLAN_FILENAME
    yaml_str = yaml.dump(plan, default_flow_style=None, sort_keys=False,
                         allow_unicode=True)

    if available_phases:
        # Insert phase list as comment right after poscar_src line
        comment_lines = ["# Available phases from MP:\n"]
        for i, p in enumerate(available_phases):
            default = " (default)" if i == 0 else ""
            energy_str = f"E_form={p['energy']:.3f} eV/atom" if p['energy'] < 990 else "energy=N/A"
            comment_lines.append(
                f"# - mp-{p['mpid']}: {p['spg']}, {energy_str}, "
                f"a={p['a']:.3f} b={p['b']:.3f} c={p['c']:.3f}{default}\n"
            )
        comment_lines.append("# To use a different phase, change poscar_src:\n")
        comment_lines.append('#   poscar_src: "MP mp-xxx"  (use MP phase)\n')
        comment_lines.append('#   poscar_src: "./path/to/POSCAR"  (use local file)\n')

        # Find the poscar_src line and its indentation
        lines = yaml_str.splitlines(keepends=True)
        insert_at = None
        indent = ""
        for i, line in enumerate(lines):
            if line.strip().startswith("poscar_src:"):
                insert_at = i + 1
                indent = line[:len(line) - len(line.lstrip())]
                break
        # Indent comment to match the project block
        comment_lines = [indent + cl if not cl.startswith(indent) else cl for cl in comment_lines]
        if insert_at is not None:
            for cl in reversed(comment_lines):
                lines.insert(insert_at, cl)
        yaml_str = "".join(lines)

    with open(path, "w") as f:
        f.write(yaml_str)
    return path


def read_plan(project_dir):
    path = Path(project_dir) / PLAN_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"{PLAN_FILENAME} not found in {project_dir}")
    with open(path) as f:
        return yaml.safe_load(f)


def print_summary(plan):
    lines = []
    lines.append("")
    lines.append(f"  材料: {plan['project']['obj']}")
    lines.append(f"  掺杂: {', '.join(plan['project']['dopant_elements']) or '无'}")
    lines.append(f"  POSCAR: {plan['project'].get('poscar_src', '未指定')}")
    lines.append("")
    p = plan["parameters"]
    lines.append("  计算参数:")
    lines.append(f"    泛函: {p['functional']}")
    lines.append(f"    ENCUT: {p['encut'] or '自动检测'} eV")
    lines.append(f"    DFT+U: {'开启' if p['hubbard_u'] else '关闭'}")
    if p["pp"]:
        lines.append(f"    额外 POTCAR: {' '.join(p['pp'])}")
    lines.append("")
    d = plan["defects"]
    lines.append("  缺陷:")
    if d["vacancies"]:
        lines.append(f"    空位: {', '.join('V_' + v for v in d['vacancies'])}")
    for s in d["substitutionals"]:
        lines.append(f"    替代: {s['impurity']}_{s['site']}")
    lines.append(f"    电荷态: {d['charge_states']}")
    lines.append(f"    间隙位: {'是' if d['interstitials'] else '否'}")
    lines.append(f"    复合缺陷 N_max: {d['complex_n']}")
    lines.append("")
    lines.append("  各阶段:")
    stage_names = {
        "unitcell": "1 完美晶胞", "cpd": "2 竞争相",
        "defect_gen": "3 缺陷生成", "submit": "4 VASP 提交",
        "postproc": "5 后处理", "doping": "6 增量掺杂",
        "complex": "7 复合缺陷",
    }
    for key, label in stage_names.items():
        enabled = plan["stages"].get(key, True)
        lines.append(f"    {label}: {'✓' if enabled else '—'}")
    lines.append("")
    return "\n".join(lines)


def validate(plan):
    errors = []
    if not plan.get("project", {}).get("obj"):
        errors.append("project.obj: 必填")
    p = plan.get("parameters", {})
    if p.get("encut") and (not isinstance(p["encut"], (int, float)) or p["encut"] <= 0):
        errors.append("parameters.encut: 必须为正数")
    d = plan.get("defects", {})
    cs = d.get("charge_states", [])
    if not isinstance(cs, list) or any(not isinstance(c, int) for c in cs):
        errors.append("defects.charge_states: 必须为整数列表")
    stages = plan.get("stages", {})
    valid_stages = {"unitcell", "cpd", "defect_gen", "submit", "postproc", "doping", "complex"}
    for k in stages:
        if k not in valid_stages:
            errors.append(f"stages.{k}: 未知的阶段名称")
    return errors


def _deep_copy(d):
    import copy
    return copy.deepcopy(d)


def _set_nested(d, key, value):
    parts = key.split(".")
    for p in parts[:-1]:
        if p not in d:
            d[p] = {}
        d = d[p]
    d[parts[-1]] = value
