#!/usr/bin/env python3
"""
*** 仅供参考,不参与项目运行 ***

本文件是早期版本的半自动缺陷计算脚本(由项目作者在引入自动化之前手写)。

**不要**:
- 不参与项目运行(无对应的测试/CLI 入口,依赖的 `action` 模块不在仓库)
- 不作为项目代码规范

**保留目的**:
- 参考 CPD 能量修正、复杂缺陷距离筛选等细节处理

"""

import json
import os
import re
import shutil
import logging
import asyncio

import numpy as np
import pandas as pd
import yaml
from pymatgen.core import Composition, Structure
import argparse

from action import (
    showBundle,
    vaspInputCheck,
    checkJobByPath,
    submit,
    kill,
    callback,
    rerun,
    chain,
    show,
)

# setting logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s",
    datefmt="%a, %d %b %Y %H:%M:%S",
)

# 开始一个项目需要 原胞结构文件 以及掺杂的元素种类 
# 原胞文件由用户提供或者通过mp数据库计算得到

sample_info = {
    "obj": "GaN",
    "dopant_element": [],
    "interstitial": False,
    "iindex": [],
    "complex_defect": 1,
    "remote": 5,
    "pp": ["Cr_sv_GW"],
}

mol_corr = {"Cl2": 0.614 * 2, "O2": 0.687 * 2, "F2": 0.462 * 2}

cpd_dir = "cpd"
unitcell_dir = "unitcell"
defect_path = "defect"


def run_command(command):
    logging.info(f"Running command: {command}")
    os.system(command)


def prepare_and_run(path, pp):
    if not vaspInputCheck(path):
        run_command(
            f'cd "{path}"; vise vs -x pbesol -k 2 --options set_hubbard_u True -uis NSW 50 {("--potcar " + " ".join(pp)) if pp else ""}'
        )
    if not os.path.isfile(f"{path}/status.json"):
        # run_command(f'cd "{path}"; touch "remove [status.json] to reset"; future.py vasp')
        with open(f"{path}/remove [status.json] to reset", "w") as f:
            pass
        submit(["vasp"], path)


def cpd(obj, dopant_element, root, pp, auto):
    os.chdir(root)
    os.makedirs(cpd_dir, exist_ok=True)
    os.chdir(cpd_dir)
    showBundle()

    logging.info(f"obj: {obj}")
    intrinsic_element = list(Composition(obj).as_dict().keys())
    # 需要提供目标化学式以及掺杂原子种类
    # 已经有的文件不会被覆盖
    if not os.path.isfile("mp_flag"):
        run_command(
            f"pydefect_vasp mp -e {' '.join(intrinsic_element + dopant_element)} --e_above_hull 0.0005"
        )
        # run_command('touch "remove [mp_flag] to reset" mp_flag')
        with open("mp_flag", "w") as f:
            pass
        with open("remove [mp_flag] to reset", "w") as f:
            pass

        # 替换()为[]
        for path in os.listdir():
            if os.path.isdir(path):
                if "(" in path or ")" in path:
                    shutil.move(path, path.replace("(", "[").replace(")", "]"))

    cpd_info = {}
    for path in os.listdir():
        if os.path.isdir(path):
            if "_mp-" in path:
                formula, mpid = path.split("_mp-")
            elif "mol_" in path:
                formula = path.split("mol_")[1]
                mpid = None
            else:
                continue

            composition = Composition(formula)
            element = list(composition.as_dict().keys())
            # only keep the cpd with intrinsic elements
            if len(element) == 1 or any([i in element for i in intrinsic_element]):
                cpd_info[path] = {"formula": formula, "mpid": mpid}

    target_composition = Composition(obj)
    unitcell_path = None
    for path in cpd_info:
        composition = Composition(cpd_info[path]["formula"])
        if composition == target_composition:
            unitcell_path = os.path.abspath(path)
            # obj = cpd_info[path]["formula"]
            break
    else:
        raise ValueError(f"Cannot find cpd with formula {obj} in the cpd dir.")

    to_be_calculated = list(cpd_info.keys())
    # to_be_linked = []
    # for path in cpd_info.keys():
    #     if path in os.listdir("/mnt/shared/cpd_repo"):
    #         to_be_linked.append(path)
    #     else:
    #         to_be_calculated.append(path)

    logging.info(
        {
            "unitcell_path": unitcell_path,
            # "to_be_linked": to_be_linked,
            "to_be_calculated": to_be_calculated,
            "cpd_info": cpd_info,
        }
    )

    # for path in to_be_linked:
    #     if not vaspInputCheck(path):
    #         # run_command(f"rm -r '{path}'; ln -s '/mnt/shared/cpd_repo/{path}' .")
    #         shutil.rmtree(path)
    #         # os.symlink(f"/mnt/shared/cpd_repo/{path}", path)
    #         shutil.copytree(f"/mnt/shared/cpd_repo/{path}", path)

    anyKey(auto)
    for path in to_be_calculated:
        prepare_and_run(f"{root}/{cpd_dir}/{path}", pp)

    # post-processing
    if list(showBundle().keys()) == ["totally"]:
        # for path in to_be_calculated:
        #     if path not in os.listdir("/mnt/shared/cpd_repo"):
        #         # run_command(f"mv '{path}' /mnt/shared/cpd_repo; ln -s '/mnt/shared/cpd_repo/{path}' .")
        #         # shutil.move(path, f"/mnt/shared/cpd_repo/{path}")
        #         # os.symlink(f"/mnt/shared/cpd_repo/{path}", path)
        #         shutil.copytree(path, f"/mnt/shared/cpd_repo/{path}")

        # 生成composition_energies.yaml
        # if not os.path.isfile("composition_energies.yaml"):
        if not os.path.isfile("target_vertices.yaml"):
            dir_string = " ".join(cpd_info.keys())
            dir_string = dir_string.replace("(", "\(").replace(")", "\)")
            run_command(f"pydefect_vasp mce -d {dir_string}")

            # 双原子气体能量修正
            composition_energies = yaml.safe_load(
                open("composition_energies.yaml", "r")
            )

            for formula in mol_corr:
                if formula in composition_energies:
                    composition_energies[formula]["energy"] += mol_corr[formula]

            with open("composition_energies.yaml", "w") as f:
                yaml.dump(composition_energies, f, default_flow_style=None)

        # create `relative_energies.yaml` and `standard_energies.yaml`
        if not os.path.isfile("target_vertices.yaml"):
            # if not os.path.isfile("relative_energies.yaml") or not os.path.isfile("standard_energies.yaml"):
            run_command("pydefect sre")

        # 生成端点
        if not os.path.isfile("target_vertices.yaml"):
            relative_energies = yaml.safe_load(open("relative_energies.yaml", "r"))
            # 调节不稳定化合物能量
            
            target_composition
            for composition_string in relative_energies:
                composition = Composition(composition_string)
                if composition == target_composition:
                    break
            else:
                raise


            origin_energy = relative_energies[composition_string]
            current_energy = origin_energy
            run_command(f'pydefect cv -t "{composition_string}"')
            while not os.path.isfile("chem_pot_diag.json"):
                current_energy -= 0.01
                relative_energies[composition_string] = current_energy
                with open("relative_energies.yaml", "w") as f:
                    yaml.dump(relative_energies, f, default_flow_style=None)
                run_command(f'pydefect cv -t "{composition_string}"')
            if current_energy != origin_energy:
                print(
                    f"Energy of {composition_string} is adjusted from {origin_energy} to {current_energy}."
                )
            # run_command('touch "remove [target_vertices.yaml] to reset"')
            with open("remove [target_vertices.yaml] to reset", "w") as f:
                pass

            # if not os.path.isfile("cpd.pdf"):
            run_command("pydefect pc")
        return unitcell_path
    else:
        return None


def unitcell(path, obj, root, pp, auto):
    os.chdir(root)
    # 需要提供包含原胞POSCAR的路径
    # path = f"/mnt/shared/cpd_repo/{unitcell_path}"
    os.makedirs(unitcell_dir, exist_ok=True)
    os.chdir(unitcell_dir)
    showBundle()

    # find unitcell in cpd and make link
    if not os.path.isdir("structure_opt"):
        # os.symlink(path, "structure_opt")
        shutil.copytree(path, "structure_opt")
    
    if not auto:
        input(
            "If you want to use your own structure as unitcell, please replace POSCAR in structure_opt."
        )

    prepare_and_run(f"{root}/{unitcell_dir}/structure_opt", pp)
    status = checkJobByPath("structure_opt")

    unitcell_path = os.path.basename(path)
    # if "_mp-" in unitcell_path and unitcell_path in os.listdir(
    #     "/mnt/shared/unitcell_repo"
    # ):
    #     for path in ["band", "dos", "dielectric"]:
    #         if not os.path.isdir(path):
    #             # os.symlink(f"/mnt/shared/unitcell_repo/{unitcell_path}/{path}", path)
    #             shutil.copytree(
    #                 f"/mnt/shared/unitcell_repo/{unitcell_path}/{path}", path
    #             )
    if False:
        pass
    else:
        # generate POSCAR and other input, then submit
        if status == "totally":
            vise_dict = {
                "band": "vise vs -x pbesol -t band",
                "dos": "vise vs -x pbesol -t dos -k 2 -uis LVTOT True LAECHG True KPAR 1",
                "dielectric": "vise vs -x pbesol -t dielectric_dfpt -k 2",
            }

            for task in vise_dict:
                vise_dict[
                    task
                ] += f' --options set_hubbard_u True {("--potcar " + " ".join(pp)) if pp else ""}'

            shutil.copy("structure_opt/CONTCAR", "structure_opt/POSCAR")

            anyKey(auto)
            for path in ["band", "dos", "dielectric"]:
                os.makedirs(path, exist_ok=True)
                if not vaspInputCheck(path):
                    shutil.copy("structure_opt/POSCAR", f"{path}/POSCAR")
                    shutil.copy(
                        "structure_opt/prior_info.yaml", f"{path}/prior_info.yaml"
                    )
                    run_command(f"cd {path}; {vise_dict[path]}")
                if not os.path.isfile(f"{path}/status.json"):
                    # run_command(f"cd {path}; touch 'remove [status.json] to reset'; future.py vasp")
                    with open(f"{path}/remove [status.json] to reset", "w") as f:
                        pass
                    submit(["vasp"], f"{root}/{unitcell_dir}/{path}")
            # shutil.copy("dos/POSCAR", "structure_opt/POSCAR")

    if list(showBundle().keys()) == ["totally"]:
        # if "_mp-" in unitcell_path and unitcell_path not in os.listdir("/mnt/shared/unitcell_repo"):
        #     os.makedirs(f"/mnt/shared/unitcell_repo/{unitcell_path}")
        #     for path in ["band", "dos", "dielectric"]:
        #         # run_command(f"mv {path} /mnt/shared/unitcell_repo/{unitcell_path}")
        #         shutil.move(path, f"/mnt/shared/unitcell_repo/{unitcell_path}")
        #         # run_command(f"ln -s /mnt/shared/unitcell_repo/{unitcell_path}/{path} .")
        #         # os.symlink(f"/mnt/shared/unitcell_repo/{unitcell_path}/{path}", path)
        #         shutil.copytree(f"/mnt/shared/unitcell_repo/{unitcell_path}/{path}", path)

        if not os.path.isfile("unitcell.yaml"):
            run_command("cd band; vise pb")
            run_command("cd dos; vise pd")
            run_command("cd dielectric; vise pdf")
            run_command(
                "cd dos; pydefect_vasp le -v AECCAR0 AECCAR1 AECCAR2 -i all_electron_charge"
            )
            run_command(
                f"pydefect_vasp u -vb band/vasprun.xml -ob band/OUTCAR -odc dielectric/OUTCAR -odi dielectric/OUTCAR -n '{obj}'"
            )
            # run_command("touch 'remove [unitcell.yaml] to reset'")
            with open("remove [unitcell.yaml] to reset", "w") as f:
                pass
        return True
    else:
        return None


def distance(a, b, lattice_matrix):
    d = []
    # make sure every element is between -0.5 and 0.5
    for i in np.array(a) - np.array(b):
        i -= np.round(i)
        if i > 0.5:
            i -= 1
        elif i < -0.5:
            i += 1
        d.append(i)
    d = np.array(d)
    return np.linalg.norm(np.dot(d, lattice_matrix))


# def min_distance(a, b, lattice_matrix):
#     d = []
#     for i in b:
#         d.append(distance(a, i, lattice_matrix))
#     return min(d)


async def defect(
    path,
    dopant_element,
    interstitial,
    iindex,
    complex_defect,
    remote,
    root,
    pp,
    auto,
):
    os.chdir(root)
    # 需要提供原胞或者超胞POSCAR的路径

    os.makedirs(defect_path, exist_ok=True)
    os.chdir(defect_path)
    showBundle()

    # path = f"/mnt/shared/cpd_repo/{unitcell_path}/CONTCAR"
    dos_extrema_path = "../unitcell/dos/volumetric_data_local_extrema.json"
    chem_pot_path = "../cpd/target_vertices.yaml"

    # construct supercell
    if not os.path.isfile("supercell_info.json"):
        run_command(f"pydefect s -p {path} --max_atoms 600 --min_atoms 200")

    if (
        interstitial
        and json.load(open("supercell_info.json"))["interstitials"] == []
        and dos_extrema_path
    ):
        print("The candidates of interstitials are listed below:")
        run_command(f"pydefect_print {dos_extrema_path}")
        if iindex == []:
            logging.warning(
                "Please specify the interstitials to calculate with 'iindex'."
            )
            return
        else:
            interstitial_sites = " ".join(iindex)
        run_command(
            f"pydefect_util ai --local_extrema {dos_extrema_path} -i {interstitial_sites}"
        )

    if not os.path.isfile("defect_in.yaml"):
        if dopant_element:
            run_command(f"pydefect ds -d {' '.join(dopant_element)}")
        else:
            run_command("pydefect ds")
        # run_command("touch 'remove [defect_in.yaml] to reset'")
        with open("remove [defect_in.yaml] to reset", "w") as f:
            pass

    # single defects
    defect_in = yaml.safe_load(open("defect_in.yaml"))
    print("According to defect_in.yaml")
    for defect, valence in defect_in.items():
        print(f"{defect}: {valence}")

    if not os.path.isfile("defect_generate_flag"):
        anyKey(auto)
        run_command("pydefect_vasp de")
        # run_command("touch 'remove [defect_generate_flag] to reset' defect_generate_flag")
        with open("remove [defect_generate_flag] to reset", "w") as f:
            pass
        with open("defect_generate_flag", "w") as f:
            pass

    # generate defect calculations
    for path in os.listdir():
        if os.path.isdir(path) and not vaspInputCheck(path):
            run_command(
                f"cd {path}; vise vs -x pbesol -t defect -k 0.1 --options set_hubbard_u True -uis NSW 50 SIGMA 0.02 LORBIT 11 {('--potcar ' + ' '.join(pp)) if pp else ''}"
            )

    # construct structure of complex defects
    if complex_defect > 1 and not os.path.isfile("complex_flag"):
        # 填隙只会存在于基元中，不会出现在调控单元
        motif_list = [
            defect
            for defect in defect_in.keys()
            if defect.count("+") == complex_defect - 2
        ]
        # 调控单元种类
        unit_list = list(
            set(
                [
                    re.sub(r"\d+", "", defect)
                    for defect in defect_in.keys()
                    if "+" not in defect and "_i" not in defect
                ]
            )
        )
        # 基元种类
        motif_cls_list = list(
            set([re.sub(r"\d+", "", defect) for defect in motif_list])
        )

        # 组合去重
        combination_list = []
        combination_dict = {}
        for motif in motif_cls_list:
            for unit in unit_list:
                if [motif, unit] not in combination_list and [
                    unit,
                    motif,
                ] not in combination_list:
                    combination_list.append([motif, unit])
                    if motif not in combination_dict:
                        combination_dict[motif] = []
                    combination_dict[motif].append(unit)

        for _motif in motif_list:
            _motif_cls = re.sub(r"\d+", "", _motif)
            _unit_list = combination_dict[_motif_cls]
            origin_atom = list(set(defect.split("_")[1] for defect in _unit_list))
            any_valence = defect_in[_motif][0]
            motif_path = f"{_motif}_{any_valence}"

            os.chdir(motif_path)
            motif_entry = json.load(open("defect_entry.json"))
            motif_center = motif_entry["defect_center"]
            motif_structure = Structure.from_dict(motif_entry["structure"])
            motif_structure.to(fmt="poscar", filename="POSCAR-fine")
            lattice_matrix = motif_entry["structure"]["lattice"]["matrix"]

            run_command("pydefect s -p POSCAR-fine --matrix 1 1 1")
            # pydefect generate supercell will shift atoms, we need to shift back
            sposcar = Structure.from_file("SPOSCAR")
            shift = motif_structure[0].frac_coords - sposcar[0].frac_coords
            _motif_center = np.array(motif_center) - np.array(shift)

            supercell_info = json.load(open("supercell_info.json"))
            eq_sites = supercell_info["sites"]
            sites = supercell_info["structure"]["sites"]

            # 寻找满足条件的原子
            # 1. 存在到任意中心的距离在截断半径内
            # 2. 原子类型在origin_atom中
            candidates = {}
            for name, site in eq_sites.items():
                _distance = distance(
                    sites[site["equivalent_atoms"][0]]["abc"],
                    _motif_center,
                    lattice_matrix,
                )
                if (
                    site["element"] in origin_atom
                    and _distance < remote
                    and _distance > 0.3
                ):
                    if site["element"] not in candidates:
                        candidates[site["element"]] = []
                    candidates[site["element"]].append(name)

            # 生成新的defect_in.yaml
            defect_in_new = {}
            motif_valence = defect_in[_motif]
            for unit in _unit_list:
                # get valence
                for i in defect_in.keys():
                    if unit in i and "+" not in i:
                        valence = defect_in[i]
                        break

                current, origin = unit.split("_")
                for atom in candidates[origin]:
                    defect_in_new[f"{current}_{atom}"] = list(
                        range(
                            int((valence[0] + motif_valence[0]) * 0.5),
                            int((valence[-1] + motif_valence[-1]) * 0.5) + 1,
                        )
                    )

            with open("defect_in.yaml", "w") as f:
                yaml.dump(defect_in_new, f, default_flow_style=None)

            run_command("pydefect_vasp de")

            defect_in_refresh = {}
            for defect, valence in defect_in_new.items():
                defect_in_refresh[f"{_motif}+{defect}"] = valence

            defect_in.update(defect_in_refresh)
            with open("../defect_in.yaml", "w") as f:
                yaml.dump(defect_in, f, default_flow_style=None)

            poscar = Structure.from_file("perfect/POSCAR")
            poscar.translate_sites(range(len(poscar)), shift)
            poscar.to(fmt="poscar", filename="perfect/POSCAR")

            for name in defect_in_new:
                for valence in defect_in_new[name]:
                    poscar = Structure.from_file(f"{name}_{valence}/POSCAR")
                    poscar.translate_sites(range(len(poscar)), shift)
                    poscar.to(fmt="poscar", filename=f"{name}_{valence}/POSCAR")

                    # regenerate defect entry
                    # os.remove(f"{name}_{valence}/defect_entry.json")
                    run_command(
                        f"vise vs -x pbesol -t defect -k 0.1 -d {name}_{valence} --options set_hubbard_u True -uis NSW 50 {('--potcar ' + ' '.join(pp)) if pp else ''}"
                    )
                    # run_command(f"pydefect_vasp_util de -d {name}_{valence} -p ../perfect/POSCAR -n {_motif}+{name}")
                    with open(f"{name}_{valence}/defect_entry.json", "r") as f:
                        defect_entry = json.load(f)
                    defect_entry["name"] = f"{_motif}+{name}"
                    with open(f"{name}_{valence}/defect_entry.json", "w") as f:
                        json.dump(defect_entry, f)

                    try:
                        shutil.move(
                            f"{name}_{valence}", f"../{_motif}+{name}_{valence}"
                        )
                    except:
                        pass

            shutil.rmtree("perfect")
            os.chdir("..")

        with open("remove [complex_flag] to reset", "w") as f:
            pass
        with open("complex_flag", "w") as f:
            pass

    anyKey(auto)
    if not os.path.isfile("perfect/status.json"):
        # run_command(f"cd perfect; future.py vasp")
        submit(["vasp"], f"{root}/{defect_path}/perfect")
    # run_command(f"future.py chain vasp")
    await chain(["vasp"], f"{root}/{defect_path}")

    if list(showBundle().keys()) == ["totally"]:
        if not os.path.isfile("defect_energy_summary.json"):
            run_command("pydefect_vasp cr -d *_* perfect")
            run_command(
                "pydefect efnv -d *_* -pcr perfect/calc_results.json -u ../unitcell/unitcell.yaml"
            )
            run_command("pydefect dsi -d *_*")
            run_command("pydefect_util dvf -d *_*")
            run_command("pydefect_vasp pbes -d perfect")
            run_command(
                "pydefect_vasp beoi -d *_* -pbes perfect/perfect_band_edge_state.json"
            )
            run_command(
                "pydefect bes -d *_* -pbes perfect/perfect_band_edge_state.json"
            )
            run_command(
                "pydefect dei -d *_* -pcr perfect/calc_results.json -u ../unitcell/unitcell.yaml -s ../cpd/standard_energies.yaml"
            )

            # if not os.path.isfile("defect_energy_summary.json"):
            run_command(
                f"pydefect des -d *_* -u ../unitcell/unitcell.yaml -pbes perfect/perfect_band_edge_state.json -t ../cpd/target_vertices.yaml"
            )
            # if not os.path.isfile("calc_summary.json"):
            run_command("pydefect cs -d *_* -pcr perfect/calc_results.json")
            if chem_pot_path:
                # target: SiC
                # A:
                # chem_pot:
                #     C: -0.0
                #     Si: -0.55546
                # competing_phases:
                # - C
                # impurity_phases: []
                # B:
                # chem_pot:
                #     C: -0.55546
                #     Si: -0.0
                # competing_phases:
                # - Si
                # impurity_phases: []

                target_vertices = list(yaml.safe_load(open(chem_pot_path, "r")).keys())[
                    1:
                ]
                for vertex in target_vertices:
                    run_command(
                        f"pydefect pe -d defect_energy_summary.json -l {vertex}"
                    )
            # run_command("touch 'remove [defect_energy_summary.json] to reset'")
            with open("remove [defect_energy_summary.json] to reset", "w") as f:
                pass
        return True
    else:
        return None


def anyKey(auto):
    if auto:
        pass
    else:
        input("Press Any Key to Continue ")


async def single_run(root, auto):
    logging.info(f"Current root: {root}")
    if not os.path.isfile("info.json"):
        json.dump(sample_info, open("sample_info.json", "w"), indent=4)
        # run_command("touch 'mv sample_info.json info.json to start'")
        with open("mv sample_info.json info.json to start", "w") as f:
            pass
        return

    info = json.load(open("info.json"))
    pp = info.pop("pp", [])

    unitcell_path = cpd(info["obj"], info["dopant_element"], root, pp, auto)
    if unitcell_path == None:
        logging.warning("cpd calculations are not finished.")
        return
    # unitcell_done = unitcell(f"/mnt/shared/cpd_repo/{unitcell_path}", info["obj"], root, pp)
    unitcell_done = unitcell(unitcell_path, info["obj"], root, pp, auto)
    if unitcell_done == None:
        logging.warning("unitcell calculations are not finished.")
        return
    defect_done = await defect(
        f"{unitcell_path}/CONTCAR",
        info["dopant_element"],
        info["interstitial"],
        info["iindex"],
        info["complex_defect"],
        info["remote"],
        root,
        pp,
        auto,
    )
    if defect_done == None:
        logging.warning("defect calculations are not finished.")
        return
    else:
        logging.info("All calculations are finished.")


async def loop_run():
    while True:
        for path in os.listdir():
            if os.path.isdir(path) and os.path.isfile(f"{path}/info.json"):
                abs_path = os.path.abspath(path)
                os.chdir(abs_path)
                await single_run(abs_path, True)
                os.chdir(abs_path)
                os.chdir("..")
        show()
        # rerun([],"")
        await asyncio.sleep(600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="lalala")
    parser.add_argument(
        "-l",
        "--loop",
        action="store_true",
        default=False,
        help="loop mode",
    )

    args = parser.parse_args()
    if args.loop:
        logging.info("you should activate loop mode at parent dir of target systems.")
        asyncio.run(loop_run())
    else:
        asyncio.run(single_run(os.getcwd(), False))
