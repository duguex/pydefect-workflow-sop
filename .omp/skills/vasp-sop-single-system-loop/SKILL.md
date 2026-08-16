---
name: vasp-sop-single-system-loop
description: "Operate a vasp-sop batch loop scoped to ONE system on this OMP host (exclude the other 13 legacy systems), with dry-run mirror verification, ZBRENT/EDIFF policy, and git asset protection. Use when running/restarting the Li2ZnGe3O8 (or any single-system) orchestrator loop, verifying dry-run isolation, or debugging why a loop submits/doesn't submit for excluded systems."
---

# vasp-sop 单体系 loop 操作（本机）

## 场景
2026_undergo_spin_defect 批次 14 体系（13 存量 + Li2ZnGe3O8 新树）共享一个 JobStore（~/.vasp_sop/jobs.db）和 crisp daemon。只推一个体系时必须 `--exclude` 其余 13 个——**exclude 是硬边界**（commit b9eaf14 起 `_restore_crisp_active`/`_poll_tracked` 跳过 excluded roots，防存量未收敛目录被重提）。

## 启动命令（hub persist，独立于会话）
```
excl="--exclude Ba3W2O9 --exclude BaAl2B2O7 --exclude BaAl4O7 --exclude BaAl4O7_mp1019534 --exclude CaAl4O7 --exclude Gd2GaSbO7:Bi --exclude La2SrSc2O7 --exclude La2Zr2O7 --exclude SrAl4O7 --exclude SrGa4O7:Fe --exclude Y2Sn2O7 --exclude Y2Ti2O7 --exclude Y2Ti2O7_mp5373"
hub start vasp-sop-lzg -- /home/duguex/.conda/envs/dgkan_rocm_3.11/bin/vasp-sop batch run /mnt/shared/home/2sidesniddle/vasp/2026_undergo_spin_defect $excl --loop
```
- 用 `vasp-sop batch run`（非 `batch start`——后者无 exclude，会碰存量）。
- 启动后 `hub logs vasp-sop-lzg` 看推进；进程可能短暂 D state（NFS IO）——正常，等几秒复测。

## dry-run 隔离验证（生产零污染）
- `--dry-run` 在隔离镜像树运行（~/.vasp_sop/dryrun/<ts>/）：输入+json/yaml 拷贝、OUTCAR 软链、JobStore 重定向、exclude 过滤后才镜像（commit 13124ff）。
- 验证：`stat -c "%Y %n" <prod>/defect/Cr_Ge1_0/INCAR` 前后 md5 必须一致；镜像 INCAR 应含 LDAU（stage2 patch 落镜像不落生产）。
- 已知坑：镜像只建非空目录会丢 defect/ 空目录判定（classify_analyze_status）——现用 os.walk 保留全骨架。

## ZBRENT 策略（ISIF=3 弛豫崩溃）
- OUTCAR 尾 `ZBRENT: fatal error in bracketing` → VASP 建议 "rerun with smaller EDIFF, or copy CONTCAR to POSCAR"。
- 正确：**EDIFF 1E-06 + CONTCAR→POSCAR**（orchestrator 内置 issue #119——`_has_zbrent_failure` 自动 patch）。手动只做 CONTCAR 复制不改 EDIFF → 10/22 再崩（金属相 1e-4 太松）。
- 手动补救 = sed EDIFF 1E-06 + cp CONTCAR POSCAR + crisp submit --tag long。

## 播种纪律（ADR 0010）
- **绝不 crisp submit 批量手动提交 defect**——绕过 orchestrator 链播种（130/174 非根电荷态曾被取消重来）。
- orchestrator 自动：根电荷态（median）先提交 → 收敛后 `seed_geometry_from_contcar` 播种非根（省 ~80% 步数）→ 无收敛兄弟则等待。
- 手动提交无 JobStore 记录 → orchestrator 会重复提交（并发污染 OUTCAR）——必须取消手动任务交给 orchestrator。

## 关键资产 git 保护（用户强调：弛豫结构是重要资产）
- 新体系无 .git 时 `init_system_repo`（ADR 0019）：CONTCAR/POSCAR/INCAR 入库、POTCAR/OUTCAR/CHGCAR/WAVECAR 忽略、`.timeout`/`.crisp-submission.json` 忽略（瞬时标记快照中途消失会炸 commit）。
- baseline 后 `batch run --loop` 每 N 轮自动 `_git_snapshots()`。
- 事故：`git add` 遇 `.timeout` 消失 → "unable to index file" 128——已在模板忽略。

## 事故恢复（INCAR 被 dry-run 污染）
- 症状：INCAR 含 LDAU 但 OUTCAR 无 LDAU 回显（stage1 跑的）→ _stage2_pending False → stage2 永不提交。
- 修复：删 INCAR 的 LDAU/LDAUU/LDAUL/LDAUTYPE/LDAUPRINT/LMAXMIX 行恢复 stage1 → 真实 pass 重新 patch+提交。OUTCAR/CONTCAR 绝不动。

## unitcell 单点腿 gate
- band/dos/dielectric 设计上 gate 在 UNITCELL_DEFECT 相位（operator decision 2026-08-11）——structure_opt 收敛也不会提前提交；如需并行须先改 gate（等用户定）。
