---
name: vasp-sop-independent-recompute-approval-gate
description: "Bootstrap independent vasp-sop recompute trees with the operator approval-gate discipline, including the COMPETING race trap (machine submits the whole defect chain within minutes — stop the loop before COMPETING) and the wave1 target ZBRENT patch. Use when 背靠背重算/独立重算/新树重跑, or when defect_in.yaml/cpd were built without approval."
---

# vasp-sop 独立重算 + 审批纪律

## 何时用
背靠背独立重算（A 轮已完成，B 轮新树），或任何 defect_in.yaml/cpd 相集需要过目的场景。

## 运营纪律（2026-08-14 用户确认）
- 科学范围产物 = defect_in.yaml/缺陷电荷态清单、cpd 相集。**构建前必须操作员显式批准**，程序自动构建=违规。
- 审批语义：显式批准，**无超时放行**。
- 过渡机制：runbook 卡位（展示清单+等批准）+ GitHub issue 请求代码级 gate（已有 issue #140，duguex/pydefect-workflow-sop）。

## ⚠️ 竞速陷阱（2026-08-14 实证，必须遵守）
**机器不会在 COMPETING 停下等人**：
- 链根（中位电荷）在进入 COMPETING 的同一个 cycle 内就提交；
- 邻位电荷态随后几分钟内全部提交（ADR 0010 链式播种），全链 ~百个作业。
- 实测：两棵重建树在察觉前已提交 94 个 defect 作业、77 个 completed（撤销只来得及拦住 in-flight）。
- **操作：重建树接近 COMPETING 前（STRUCTURE_OPT 主相收敛后）就 `systemctl --user stop <loop>`**，展示 defect_in 清单等显式批准，批准后再 start。若已进入 COMPETING，先停 loop 再撤销作业（`crisp cancel -n <task>`，多个 task 需逐个撤）。

## ⚠️ 审批预置（prestage，重启 loop 前的唯一防线）

loop 会在体系到 COMPETING 之前的**早 cycle** 就用 pydefect `ds` 的**默认电荷态** build+submit 缺陷腿（`orchestrator.py::advance_one_system` 的 COMPETING 分支 + 更早的提前 build）。「等 loop 到 COMPETING 再审批」必然被机器速度绕过（2026-08-14 Ba3W2O9 事故：33 个默认电荷态缺陷在审批前已算完）。**唯一防线：在重启 loop 前手动预置批准范围的 `defect_in.yaml` 并重建缺陷目录**——`build_all` 尊重磁盘已有 defect_in（`_generate_defect_list` 见 `builder.py`：文件存在即 skip 生成），loop 之后不会覆盖。

操作步骤（新树/重建树，loop 停止状态下）：
1. 停 loop：`systemctl --user stop vasp-sop-loop.service`（必要时 purge `~/.vasp_sop/jobs.db` tracked/history 中该树记录，见 jobs.db schema：`tracked[dir_path, submitted_at]` + `job_history[dir_path,status,timestamp,...]`）。
2. 建 supercell + 生成默认 defect_in（拿学位点标签与默认范围做对照）：
   ```python
   from vasp_sop.core.config import PipelineConfig
   from vasp_sop.defect.builder import _build_supercell_doped, _generate_defect_list_pydefect
   cfg = PipelineConfig.from_yaml(root/'plan.yaml', root=root)
   _build_supercell_doped(df, td/'CONTCAR', cfg)   # td = cpd 主相目录
   _generate_defect_list_pydefect(df, cfg)
   ```
3. **关键陷阱**：若 defect 目录树此前被 loop 构建过，会残留 0 字节 `defect_generate_flag`——它让 build_all 跳过 `pydefect_vasp de` 结构生成，**defect_in.yaml 更新了但目录还是旧电荷态**。重建前必须 `rm -f defect_generate_flag`。
4. **只删需要变更的家族目录**（如 Mn_*/O_W*/W_O*），**不要全删**——范围与批准集一致且已收敛的目录（Va_*/阳离子反位）删了 = 白丢计算量（2026-08-14 误删 ~15 个已收敛 Va_*/W_Ba/Ba_W 的教训）。对照默认 defect_in 决定删除集。
5. 写批准范围的 `defect_in.yaml`（yaml.safe_dump, sort_keys=False；阴-阳反位如 Mn_O/O_W/W_O 直接不列入——de 只建 defect_in 里的家族）。
6. `build_all(df, td, cfg)`（**第二参数是 cpd 主相目录，不是 CONTCAR 文件**；内部拼 POSCAR）。验证：目录数 = Σ电荷态 + 1(perfect)，全部有 INCAR，无被剔除家族目录。
7. `git add defect/ && git commit`（几何基线，配合 ADR 0019 三明治判定）。
8. 重启 loop：`systemctl --user start vasp-sop-loop.service`。查 `batch_run.log`（批次根下）确认缺陷链按批准清单派发、crisp agent.db（`~/.crisp/data/agent.db` 表 jobs）无指向已删目录的 running 残留。
9. 审批前若已有作业被提交：在 crisp jobs 表确认旧作业状态（completed/failed/running），无 running 指向已删目录即可；已 completed 的旧目录若被删则会被 loop 重算（接受或保留目录二选一，别两不沾）。

验收：defect_in.yaml = 批准清单（逐家族逐电荷态）；磁盘缺陷目录与 defect_in 一一对应（ls 对照，别信 build 日志）；loop 日志里 defect 提交的目录名 ⊆ 批准清单。

## 独立树 bootstrap 步骤
1. 建树：`mkdir <root>/<Sys>_dNN`，复制 A 轮 plan.yaml，只改议定参数（如 min_distance 10→12）。
2. `vasp-sop batch run <root> --dry-run` 验证程序对树的理解（新树会报 NO_TARGET——正常）。
3. 拉相：`mkdir -p cpd` 然后 `vasp-sop materials fetch -e <els> -d <dop>`（**cpd/ 不存在时 fetch 直接 FileNotFoundError 崩溃**；fetch 的 combo 缓存**不含主相**，需 `vasp-sop defect init -f <formula> -d <dop>` 的 MPRester fallback 补主相，之后**恢复议定 plan.yaml**——注意 fetch 的 combo restore 会清掉已存在的主相目录，fetch 后必须重新下载主相）。
4. `vasp-sop batch generate-inputs <root>`（vise 生成全部 cpd 输入；只动 `!input_ready` 目录，旧体系自动跳过）。
5. `vasp-sop batch git-snapshot <root>`（ADR 0019 基线；单体系可用 `git_snapshot.init_system_repo(root)`）。
6. 起 loop：systemd user unit，ExecStart=`vasp-sop batch run <root> <root>/_noop --loop --poll 120`——roots 序即优先级（前根 priority=10），`_noop` 空目录占位。

## 审批点执行
- 程序在 COMPETING 自动构建 defect 腿（超胞→pydefect ds→de→输入）。**构建前**：展示 10 家族/条目数/电荷态汇总表（按家族聚合），等显式批准。
- 已违规时的补救：留档 `cpd 目录外副本 defect_in.approval.yaml` → `systemctl --user stop <loop>` → `rm -rf defect` → 提 issue → 呈审批包 → 批准后 `systemctl --user reset-failed <loop>` + start。

## 坑
- loop SIGTERM 挂起是已知问题（issue #137）：stop 超时（TimeoutStopUSec 90s）后需 `systemctl --user kill -s KILL <svc>`，unit 变 failed，重启前必须 `reset-failed`。
- cpd 在飞作业与 defect 删除无关，不用 cancel。
- 程序纪律边界：NELM 电子不收敛不盲重试（ADR 0017）、cpd 离子重启上限 3 次、ZBRENT 自动 EDIFF=1e-6——这些是程序停下等人，不是审批点。
- **ZBRENT 主相缺口（commit 8202a8a 已修）**：wave1 target（cpd 主相）曾无 EDIFF=1e-6 补丁导致主相每轮裸重提、永不收敛（Ba3W2O9 mp-18867 实证两次 ZBRENT fatal）；普通 cpd 相（wave2 分支）一直有补丁。若运行中 loop 是旧代码，主相会卡死——升级后需重启 loop 进程。
- 队列空是相位机串行单作业的正常态（120s 轮询间隙），不是卡死；看 `batch_run.log` 判断。