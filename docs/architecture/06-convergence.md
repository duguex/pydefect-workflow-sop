# 收敛判定 (`convergence_verdict`)

实现：`vasp_sop/vasp/convergence.py::convergence_verdict`。  
任务级产物完整性：`check_task_complete`（band/dos/dielectric）。

---

## 任务类型与判据（先分清）

| 类型 | 识别 | 收敛含义 | **不要**用 |
|------|------|----------|------------|
| **结构弛豫** | `IBRION ∈ {1,2,3}` 且 `NSW > 1` | 离子步达到停判 | — |
| **非结构弛豫** | `NSW ≤ 1` 或 `IBRION ∉ {1,2,3}`（单点、band/dos 类、MD、`IBRION=8` DFPT 等） | 作业正常结束 + 任务产物 | **力 / EDIFFG 离子判据** |

---

## 结构弛豫：VASP 语义 vs 本仓库实现

### VASP 官方停判（权威）

OUTCAR / 标准输出中出现：

```text
reached required accuracy - stopping structural energy minimisation
```

表示离子结构优化按 EDIFFG 达到要求并停止。  
**结构弛豫是否收敛，应以这句话为准**（用户约定；与 VASP 行为一致）。

### 当前代码实现（与生产库对照）

1. 必须有 OUTCAR，且尾部含 `General timing and accounting`（VASP 正常写完）。  
2. 运行参数优先读 **本次 OUTCAR**（`NSW` / `IBRION` / `EDIFFG`）；**INCAR 仅回退**。  
   禁止用「续跑已改过的 INCAR NSW」去解释旧 OUTCAR。  
3. 离子弛豫且 `EDIFFG < 0`：  
   - 实现侧用最后一块 `TOTAL-FORCE` 的 `max|F| ≤ |EDIFFG|` 作硬门。  
   - 在 `2025_undergo_spin_defect` 缺陷集上，该力门控与 **`reached required accuracy…` 一致**。  
4. `EDIFFG ≥ 0` 或无法解析力：回退 `n_ionic < NSW_run`（pymatgen 风格提前退出启发式）。

**文档立场：** 语义上以 **`reached required accuracy - stopping structural energy minimisation`** 为结构弛豫收敛定义；实现目前以力门控为主，并与上述字符串在生产数据上对齐。后续实现可改为 **显式识别该字符串（优先或并列）**。

---

## 非结构弛豫

```text
NSW ≤ 1  或  IBRION ∉ {1,2,3}
  → 有 General timing → convergence_verdict True
  → 不使用 max|F| / EDIFFG 离子判据
```

| 任务 | 额外要求 (`check_task_complete`) |
|------|----------------------------------|
| `band` / `dos` | 收敛 OUTCAR + `vasprun.xml`（根目录；兼容遗留 `output/`） |
| `dielectric` | OUTCAR + timing（DFPT，无力判据） |

---

## 文件位置：crisp 当前布局 vs 遗留 `output/`

### 当前 crisp（2026-07 起）

| 约定 | 说明 |
|------|------|
| **结果直写工作目录** | `OUTCAR` / `vasprun.xml` / `CONTCAR` 等直接在 `{calc}/`，**废除** `output/` 中转 |
| **Slurm 日志** | `{slurm_job_id}.log`（例如 `205890.log`），不再依赖固定的 `vasp_stdout.log` 唯一名 |

`move_crisp_outputs({calc})` 在无 `output/` 时为 **no-op**（可安全每次调用）。

### 遗留目录（旧作业仍可能存在）

| 路径 | 角色 |
|------|------|
| `{calc}/output/*` | 旧 crisp 拉回路径 |
| 查找 | `convergence_verdict` / `has_vasprun` 仍：**根优先，再 `output/`** |
| 上提 | `move_crisp_outputs`：按 **mtime 择优** 合并到根，然后删除 `output/` |

### 混合树注意

生产可能同时有「新直写」与「旧 `output/`」。以 **根目录最新文件** 为准；上提时 mtime 更新的 `output/` 会覆盖更旧的根文件。

---

## 续跑 / 缺 vasprun 策略（相关）

| 场景 | 策略 |
|------|------|
| 离子未收敛 + 有 timing | CONTCAR 重启（`restart_from_contcar`），**不擅自改 NSW/IBRION**（#0016） |
| 离子已收敛但缺 `vasprun.xml` | 先确认直写/`output/`/cache；仍缺再考虑重交（优先补文件，避免对已 `reached` 盲目重弛豫） |
| 形成能 | `pydefect_vasp cr` 需要 `vasprun.xml`；缺则 `missing_vasprun`（#0010） |

---

## 规则摘要（实现伪代码）

```text
1. 无 OUTCAR（含遗留 output/）或无 General timing → False

2. 参数：OUTCAR 优先，INCAR 回退

3. 非结构弛豫（NSW≤1 或 IBRION∉{1,2,3}）→ True（仅 timing）

4. 结构弛豫（IBRION∈{1,2,3} 且 NSW>1）：
   语义: "reached required accuracy - stopping structural energy minimisation"
   实现: EDIFFG<0 → max|F| ≤ |EDIFFG|
         否则 → n_ionic < NSW_run
```

---

## 测试

- `tests/test_defects.py::TestVaspJobDone`  
- `TestVasprunRecovery`  
- `tests/test_jobs_move_crisp.py`（遗留 `output/` mtime 择优；无 `output/` 为 no-op）  

---

## 相关 issue

- #0010 missing vasprun · #0016 recovery · #0017 re-analyze  
- #0022 crisp direct-write + `jobid.log`  
