# `BatchOrchestrator.run` 主循环

## 回填缓存 (Backfill)

```python
for 每个系统:
    for 每个 cpd/竞争相手目录:
        if JobStore().latest(dir) == "converged":
            continue           # 已记录，跳过
        verdict = convergence_verdict(dir)
        if not verdict.converged:
            continue           # 没收敛，跳过
        _cache_put(dir)        # 写入 maggma 缓存
        JobStore().record(dir, "converged", source="backfill")
```

## 轮询已完成作业 (Poll)
```python
crisp_active = crisp_active_dirs()  # crisp 当前活跃列表（core/jobs.py）

for row in JobStore().tracked_dirs():
    wd = Path(row["dir_path"])
    if str(wd.resolve()) in crisp_active:
        continue                # 还在集群上跑

    verdict = convergence_verdict(wd)
    if verdict.converged:
        orchestrator.finalize_converged(wd)
        continue

    outcar = wd / "OUTCAR"
    if not outcar.is_file():
        outcar = wd / "output" / "OUTCAR"
    if not outcar.is_file():
        if time.time() - row["submitted_at"] > 7 * 86400:
            JobStore().record(wd, "failed", reason="orphaned")
            JobStore().untrack(wd)
        continue

    tail = tail_read(outcar, 4096)
    if "General timing and accounting" not in tail:
        JobStore().record(wd, "failed", reason="vasp_crash")
        JobStore().untrack(wd)
        continue

    # VASP 正常结束但未收敛 → CONTCAR 重启或放弃
    orchestrator.handle_unconverged(wd)
```

## CONTCAR 重启 + 停滞检测

```python
MAX_RESTART = 5
STALL_THRESHOLD = 0.99  # 受力改进 < 1% 即停滞

# 实际实现: BatchOrchestrator.handle_unconverged(wd) / is_stalled(prev, cur)
# （vasp_sop/vasp/convergence.py 与 core/orchestrator.py）

def _parse_max_f(outcar: Path) -> float:
    """从 OUTCAR TOTAL-FORCE 块解析最大受力。"""
    text = outcar.read_text()
    idx = text.rfind("TOTAL-FORCE (eV/Angst)")
    if idx < 0: return 0.0
    max_f = 0.0
    for line in text[idx:].splitlines()[2:]:
        parts = line.strip().split()
        if len(parts) < 6: break
        max_f = max(max_f, abs(parts[3]), abs(parts[4]), abs(parts[5]))
    return max_f

def handle_unconverged(wd: Path) -> None:
    """VASP 正常结束但未收敛 → CONTCAR 重启或放弃。"""
    wd_str = str(wd.resolve())
    history = JobStore().history(wd_str)
    attempt = history[-1].get("attempt", 0) if history else 0

    cur_f = _parse_max_f(wd / "OUTCAR")
    if cur_f == 0.0:
        cur_f = _parse_max_f(wd / "output" / "OUTCAR")

    # 停滞检测: 与上次重启前的受力比较
    if cur_f > 0 and attempt > 0:
        for h in reversed(history):
            reason = h.get("reason", "")
            if reason.startswith("restart,"):
                for part in reason.split(","):
                    if part.startswith("prev_f="):
                        prev_f = float(part.split("=")[1])
                        if cur_f >= prev_f * STALL_THRESHOLD:
                            JobStore().record(wd_str, "failed", reason=f"stalled,max_f={cur_f:.4f}", attempt=attempt)
                            JobStore().untrack(wd_str)
                            return
                        break
                break

    if attempt >= MAX_RESTART:
        JobStore().record(wd_str, "failed", reason=f"unconverged,max_f={cur_f:.4f}", attempt=attempt)
        JobStore().untrack(wd_str)
        return

    restart_from_contcar(wd)   # CONTCAR → POSCAR, ISTART=1
    NSW += 500                 # INCAR 中 NSW 增加
    job = submit_vasp(wd)      # 重新提交到 crisp
    JobStore().record(wd_str, "submitted", source=job.task_name,
                      attempt=attempt + 1, reason=f"restart,prev_f={cur_f:.4f}")
    # tracked 不变 — 仍在待检查列表中
```

## 逐个推进系统

```python
for root in systems:
    sys = System(root, config)
    p = sys.derive_phase()
    if p == COMPLETE or p == NO_TARGET:
        continue
    advance_one_system(root_dict_for(sys))
```
