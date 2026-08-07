# `_advance_one_system()` 各阶段操作

## STRUCTURE_OPT 阶段

```python
if convergence_verdict(target_dir).converged:   # crisp 已物化结果到磁盘
    JobStore record("converged")
elif input_ready(target_dir):
    _submit_or_skip(target_dir) → crisp submit
```

## COMPETING 阶段

```python
for 每个需要提交的竞争相目录:
    if 未提交且未收敛且 JobStore 未 done:
        _submit_or_skip(dir) → crisp submit
        JobStore().track(dir) + record("submitted")
```

## CHEM_POT_DIAGRAM 阶段

```python
move_crisp_outputs(收敛的竞争相)
compute_chemical_potentials(cpd_root)
```

## UNITCELL_DEFECT 阶段

```python
# 1. 构建缺陷结构 + 生成 VASP 输入
build_defects(defect_root, target_dir)
_generate_vasp_inputs(defect_root)

# 2. 提交 UC 任务（band / dos / dielectric）
for task in ("band", "dos", "dielectric"):
    if check_task_complete(task_dir, task):  JobStore converged，跳过
    if JobStore "submitted":                  跳过
    if JobStore "converged":                  跳过
    prepare_inputs + _submit_or_skip

# 3. 提交缺陷任务（如果 summary 还没生成）
if defect_energy_summary.json 不存在:
    for 每个缺陷子目录:
        if 输入不齐全:                          跳过
        if convergence_verdict(...).converged:  记录 converged，跳过
        if JobStore "submitted":                 跳过
        if JobStore "converged":                 跳过
        _submit_or_skip

# 4. 后处理触发条件（三个条件都满足）
#    uc_all_done   = 所有 UC 任务的 JobStore 状态为 converged
#    df_vasp_done  = 所有缺陷目录的 JobStore 状态为 converged/failed
#    df_vasp_ondisk = 所有缺陷目录 OUTCAR 存在且收敛
if uc_all_done and df_vasp_done and df_vasp_ondisk:
    if defect_energy_summary.json 不存在:
        build_unitcell_yaml(uc_root)         # 生成 unitcell.yaml
        _analyze_defects(...)                # 11 步 pydefect 后处理
```

## _submit_or_skip

```python
def _submit_or_skip(path):
    job = submit_vasp(path)         → crisp submit
    JobStore().track(path)          → 加入待检查列表
    JobStore().record("submitted")  → 记录提交状态
```
