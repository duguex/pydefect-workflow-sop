#!/usr/bin/env python
"""读取DFT弛豫路径数据（能量、力、结构）"""

import os
import numpy as np
from pathlib import Path
import re


def parse_outcar(outcar_path):
    """
    从OUTCAR中提取能量和力
    
    Returns:
        dict: {
            'energy': float,  # 最终能量
            'energies': list,  # 所有SCF步的能量
            'forces': np.array,  # 原子力 (n_atoms, 3)
            'positions': np.array,  # 原子位置 (n_atoms, 3)
        }
    """
    data = {
        'energy': None,
        'energies': [],
        'forces': None,
        'positions': None
    }
    
    with open(outcar_path, 'r') as f:
        lines = f.readlines()
    
    # 提取所有能量值
    for line in lines:
        if 'energy without entropy' in line:
            # 格式: energy without entropy =   -1912.16961555  energy(sigma->0) =   -1912.17807839
            match = re.search(r'energy without entropy\s*=\s*([\d\.\-]+)', line)
            if match:
                data['energies'].append(float(match.group(1)))
    
    # 取最后一个能量作为最终能量
    if data['energies']:
        data['energy'] = data['energies'][-1]
    
    # 提取力
    # 格式:
    # POSITION                                       TOTAL-FORCE (eV/Angst)
    #      0.00000      0.00000      0.00000         0.000000      0.000000      0.000000
    for i, line in enumerate(lines):
        if 'TOTAL-FORCE' in line:
            forces = []
            positions = []
            # 跳过标题行和空行
            j = i + 2
            while j < len(lines):
                parts = lines[j].split()
                if len(parts) == 6:
                    try:
                        positions.append([float(parts[0]), float(parts[1]), float(parts[2])])
                        forces.append([float(parts[3]), float(parts[4]), float(parts[5])])
                        j += 1
                    except ValueError:
                        break
                else:
                    break
            
            # 更新力（取最后一次出现的力）
            if forces:
                data['forces'] = np.array(forces)
                data['positions'] = np.array(positions)
    
    return data


def parse_contcar(contcar_path):
    """
    从CONTCAR/POSCAR中提取结构信息
    
    Returns:
        dict: {
            'lattice': np.array,  # 晶格向量 (3, 3)
            'positions': np.array,  # 原子位置 (n_atoms, 3)
            'elements': list,  # 元素列表
            'numbers': list,  # 每个元素的原子数
            'symbols': list,  # 每个原子的元素符号
        }
    """
    data = {
        'lattice': None,
        'positions': None,
        'elements': [],
        'numbers': [],
        'symbols': []
    }
    
    with open(contcar_path, 'r') as f:
        lines = f.readlines()
    
    # 第一行：注释
    # 第二行：缩放因子
    scaling = float(lines[1].strip())
    
    # 第3-5行：晶格向量
    lattice = []
    for i in range(2, 5):
        lattice.append([float(x) for x in lines[i].split()])
    data['lattice'] = np.array(lattice) * scaling
    
    # 第6行：元素
    data['elements'] = lines[5].split()
    
    # 第7行：每个元素的原子数
    data['numbers'] = [int(x) for x in lines[6].split()]
    
    # 生成每个原子的元素符号
    for elem, num in zip(data['elements'], data['numbers']):
        data['symbols'].extend([elem] * num)
    
    # 第8行：Direct或Cartesian
    coord_type = lines[7].strip()[0].upper()
    
    # 提取原子位置
    positions = []
    start_line = 8 if 'Selective' not in lines[7] else 9
    
    for i in range(start_line, start_line + sum(data['numbers'])):
        parts = lines[i].split()
        positions.append([float(parts[0]), float(parts[1]), float(parts[2])])
    
    data['positions'] = np.array(positions)
    
    # 如果是Direct坐标，转换为Cartesian
    if coord_type == 'D':
        data['positions'] = data['positions'] @ data['lattice']
    
    return data


def read_dft_step(step_dir):
    """
    读取一个DFT步骤的完整数据
    
    Args:
        step_dir: 步骤目录路径
    
    Returns:
        dict: {
            'energy': float,
            'forces': np.array,
            'positions': np.array,
            'lattice': np.array,
            'symbols': list,
        }
    """
    step_dir = Path(step_dir)
    
    # 优先读取scf子目录，如果没有则读取当前目录
    scf_dir = step_dir / 'scf'
    if not scf_dir.exists():
        scf_dir = step_dir
    
    # 读取OUTCAR
    outcar_path = scf_dir / 'OUTCAR'
    if outcar_path.exists():
        outcar_data = parse_outcar(outcar_path)
    else:
        outcar_data = {}
    
    # 读取CONTCAR或POSCAR
    contcar_path = scf_dir / 'CONTCAR'
    if not contcar_path.exists():
        contcar_path = scf_dir / 'POSCAR'
    
    if contcar_path.exists():
        contcar_data = parse_contcar(contcar_path)
    else:
        contcar_data = {}
    
    # 合并数据
    result = {
        'energy': outcar_data.get('energy'),
        'energies': outcar_data.get('energies', []),
        'forces': outcar_data.get('forces'),
        'positions': contcar_data.get('positions') if contcar_data.get('positions') is not None else outcar_data.get('positions'),
        'lattice': contcar_data.get('lattice'),
        'symbols': contcar_data.get('symbols', []),
        'elements': contcar_data.get('elements', []),
        'numbers': contcar_data.get('numbers', []),
    }
    
    return result


def read_relaxation_path(base_dir, step_pattern='step{}_results'):
    """
    读取整个弛豫路径的数据
    
    Args:
        base_dir: 基础目录
        step_pattern: 步骤目录的模式，用{}代替步骤编号
    
    Returns:
        list: 每个步骤的数据字典列表
    """
    base_dir = Path(base_dir)
    steps = []
    
    # 查找所有匹配的步骤目录
    step_dirs = sorted(base_dir.glob(step_pattern.format('*')))
    
    for step_dir in step_dirs:
        # 从目录名中提取步骤编号
        step_name = step_dir.name
        # 提取数字部分
        import re
        match = re.search(r'step(\d+)', step_name)
        if match:
            step_idx = int(match.group(1))
        else:
            continue
        
        step_data = read_dft_step(step_dir)
        step_data['step'] = step_idx
        steps.append(step_data)
    
    # 按步骤编号排序
    steps.sort(key=lambda x: x['step'])
    
    return steps


def parse_outcar_relaxation(outcar_path):
    """
    从OUTCAR中提取完整的弛豫轨迹（多个离子步骤）
    
    Returns:
        list: 每个离子步骤的数据字典列表，每个字典包含:
            'energy': float,  # 该步骤的能量
            'forces': np.array,  # 原子力 (n_atoms, 3)
            'positions': np.array,  # 原子位置 (n_atoms, 3)
    """
    with open(outcar_path, 'r') as f:
        lines = f.readlines()
    
    trajectory = []
    
    # 查找所有TOTAL-FORCE块，并提取每个块之后最近的能量
    i = 0
    while i < len(lines):
        if 'TOTAL-FORCE' in lines[i]:
            step_data = {
                'energy': None,
                'forces': None,
                'positions': None
            }
            
            # 提取力和位置
            forces = []
            positions = []
            j = i + 2  # 跳过标题行和空行
            
            while j < len(lines):
                parts = lines[j].split()
                if len(parts) == 6:
                    try:
                        positions.append([float(parts[0]), float(parts[1]), float(parts[2])])
                        forces.append([float(parts[3]), float(parts[4]), float(parts[5])])
                        j += 1
                    except ValueError:
                        break
                else:
                    break
            
            # 向后搜索最近的"free  energy   TOTEN"
            for k in range(j, min(len(lines), j+500)):
                if 'free  energy   TOTEN' in lines[k]:
                    match = re.search(r'free  energy   TOTEN\s*=\s*([\d\.\-]+)', lines[k])
                    if match:
                        step_data['energy'] = float(match.group(1))
                        break
            
            if forces and step_data['energy'] is not None:
                step_data['forces'] = np.array(forces)
                step_data['positions'] = np.array(positions)
                trajectory.append(step_data)
            
            i = j
        else:
            i += 1
    
    return trajectory


def read_vasp_relaxation(rlx_dir):
    """
    读取VASP弛豫计算的完整轨迹
    
    Args:
        rlx_dir: 弛豫目录路径（包含OUTCAR的目录）
    
    Returns:
        list: 每个离子步骤的数据列表
    """
    rlx_dir = Path(rlx_dir)
    
    # 读取OUTCAR获取完整轨迹
    outcar_path = rlx_dir / 'OUTCAR'
    if not outcar_path.exists():
        raise FileNotFoundError(f"找不到OUTCAR: {outcar_path}")
    
    trajectory = parse_outcar_relaxation(outcar_path)
    
    # 添加步骤编号
    for idx, step_data in enumerate(trajectory):
        step_data['step'] = idx
    
    return trajectory


if __name__ == '__main__':
    # 测试读取单个步骤
    step0_path = '/home/duguex/developing/nn-zfs/attempt2/step0_results'
    print(f"读取 {step0_path}")
    print("="*60)
    
    data = read_dft_step(step0_path)
    
    print(f"能量: {data['energy']:.6f} eV")
    print(f"SCF步数: {len(data['energies'])}")
    print(f"原子数: {len(data['symbols'])}")
    print(f"元素: {data['elements']}")
    print(f"原子数: {data['numbers']}")
    
    if data['forces'] is not None:
        print(f"\n力统计:")
        print(f"  最大力: {np.max(np.linalg.norm(data['forces'], axis=1)):.6f} eV/Å")
        print(f"  平均力: {np.mean(np.linalg.norm(data['forces'], axis=1)):.6f} eV/Å")
        print(f"\n前5个原子的力:")
        for i in range(min(5, len(data['forces']))):
            print(f"  原子{i} {data['symbols'][i]}: {data['forces'][i]}")
    
    # 测试读取整个弛豫路径（采样点）
    print("\n" + "="*60)
    print("读取采样点弛豫路径")
    print("="*60)
    
    base_path = '/home/duguex/developing/nn-zfs/attempt2'
    relaxation_data = read_relaxation_path(base_path)
    
    print(f"\n找到 {len(relaxation_data)} 个采样点")
    for step_data in relaxation_data:
        print(f"Step {step_data['step']}: E = {step_data['energy']:.6f} eV, "
              f"最大力 = {np.max(np.linalg.norm(step_data['forces'], axis=1)):.6f} eV/Å")
    
    # 测试读取VASP完整弛豫轨迹
    print("\n" + "="*60)
    print("读取VASP完整弛豫轨迹")
    print("="*60)
    
    rlx_path = '/home/duguex/developing/nn-zfs/attempt2/step0_results/rlx'
    try:
        vasp_trajectory = read_vasp_relaxation(rlx_path)
        print(f"\n找到 {len(vasp_trajectory)} 个离子步骤")
        for i, step_data in enumerate(vasp_trajectory):
            max_force = np.max(np.linalg.norm(step_data['forces'], axis=1))
            print(f"Ion step {i}: E = {step_data['energy']:.6f} eV, "
                  f"最大力 = {max_force:.6f} eV/Å")
    except FileNotFoundError as e:
        print(f"错误: {e}")
