---
name: vasp-sop-loop-shutdown-rollback
description: "安全停 vasp-sop batch loop 或删除计算目录：先查 crisp 队列在飞作业、立即 SIGKILL（SIGTERM 挂起 issue #137）、未审批作业先 cancel 再删目录、JobStore 对账。用于停 loop/删目录/回滚未审批作业时。"
---

# vasp-sop loop 停机与回滚纪律

## 何时用
停 batch loop、删除计算目录、回滚未经审批的作业提交。

## 步骤（顺序不可换）

### 1. 先查 crisp 在飞队列（删除/回滚前必做）
```bash
sqlite3 ~/.crisp/data/agent.db "SELECT substr(task_name,1,8), status, local_dir FROM jobs WHERE status IN ('submit','submitted','running','ready_fetch');"
```
- 有目标路径的作业在飞 → 先 cancel，再动目录（`crisp cancel <task_name>`）
- **删目录前不查队列 = 作业在飞、本地目录被删的脏状态**（2026-08-14 事故：51 个 defect 作业被删目录后仍在跑，回拉时重建残缺目录）

### 2. 停 loop 用 SIGKILL，不等 graceful stop
- `systemctl --user stop` 的 SIGTERM 会挂起（issue #137，deactivating 期间 loop 仍会提交作业！）
- 顺序：
```bash
systemctl --user stop vasp-sop-<name>.service
sleep 3
systemctl --user is-active vasp-sop-<name>.service   # 若还是 deactivating/active：
systemctl --user kill -s KILL vasp-sop-<name>.service   # 立即 SIGKILL，别等 90s 超时
pgrep -f "batch run <root>"   # 确认无残留进程才动目录
```
- 验证：`systemctl --user is-active` == failed；`pgrep -f "batch run <root>"` 为空

### 3. 回滚未审批作业
1. 列清单：`sqlite3 ~/.crisp/data/agent.db "SELECT task_name, status FROM jobs WHERE local_dir LIKE '%<root>%' AND status IN ('submit','submitted','running','ready_fetch');"`
2. 逐条 `crisp cancel <task_name>`（或脚本循环）：
```bash
for t in $(sqlite3 ~/.crisp/data/agent.db "SELECT task_name FROM jobs WHERE local_dir LIKE '%<root>%' AND status IN ('submit','submitted','running','ready_fetch');"); do crisp cancel -n "$t"; done
```
   - ⚠️ **`crisp cancel --all` 会取消所有集群作业（含其他批次/项目）——只按 local_dir 过滤的精确清单逐条取消，慎用 --all**
3. **确认 0 活跃后才删目录**：
```bash
sqlite3 ~/.crisp/data/agent.db "SELECT COUNT(*) FROM jobs WHERE local_dir LIKE '%<root>%' AND status IN ('submit','submitted','running','ready_fetch');"
# = 0 再 rm -rf
```
4. 清目录：`rm -rf <root>/defect`（残目录是回拉重建的，无输入）
5. 对账：JobStore 残留 submitted 记录需 `vasp-sop batch retry` 体系处理或等下一轮自动 reconcile

## 纪律要点
- **"停了 loop"≠"没有作业在跑"**：crisp 队列里的作业不受 loop 控制，会继续跑完；loop 只负责提交和判定
- **SIGTERM 挂起期间 loop 仍会提交**（issue #137）：停止后必须确认进程死亡（pgrep）再动目录
- 删除目录前不查队列 = 把已提交作业的本地落点删掉 → 回拉时重建残目录、缓存/对账全乱
- 审批类纪律场景（如 defect 清单未批）：先 cancel 再删，顺序不可反
- 取消请求是异步的，之后 `status IN (...)` 应为 0

## 审批纪律（用户运营约定）
- 科学范围产物（defect_in.yaml、电荷态、cpd 相集）构建后、提交前，必须展示清单 + 用户**显式批准**（无超时放行）
- 程序无审批 gate（issue #140），过渡期由 agent runbook 卡位
- dry-run（`batch run . --dry-run`）只构建不提交，可用于产出清单供审查

## 独立重算轮（背靠背）注意
- plan.yaml 必须由 `vasp-sop defect init` 程序自己生成，**禁止从参考树拷贝**（拷贝破坏独立语义）
- 用户跑 loop 用前台 session：`vasp-sop batch run <root> <root>/_noop --loop --poll 120`（双 root 保 priority=10）
- 已知程序坑：`materials fetch` 在 cpd/ 不存在时崩溃（先 mkdir cpd）；MP combo 缓存不含主相（用 defect init 补拉）；`materials poscar` 只读缓存

## 参考
- 事故详情：2026-08-14 B 轮 Y2Ti2O7_d12（51 作业，1 completed 无法撤回）
- 相关 issue：#137（SIGTERM 挂起）