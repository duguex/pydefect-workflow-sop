---
name: vasp-sop-independent-recompute-setup
description: "Set up an independent back-to-back recompute tree for a vasp-sop system (e.g. Y2Ti2O7 at a different supercell min_distance) letting the program drive the whole pipeline: plan copy+edit, cpd fetch quirks, defect init fallback, generate-inputs, git-snapshot, systemd loop with crisp priority via roots ordering. Use when 背靠背重算/独立重算 a 2026 batch system or re-running a system with a modified plan."
---

# vasp-sop 独立重算树搭建（背靠背 B 轮，程序自驱）

用户要求背靠背独立重算时（如 Y2Ti2O7 2026 批，min_distance 10→12），**只做最小引导，让 vasp-sop 程序自己跑全流程，不手工干预**。2026-08-14 实测流程（Y2Ti2O7_d12，176 原子超胞）。

## 关键前提
- B 树放批外独立目录：`/mnt/shared/home/2sidesniddle/vasp/recompute/<Sys>_d12`
- plan.yaml = 复制 A 树 plan 只改目标参数（`sed -i 's/min_distance: 10.0/min_distance: 12.0/'`）——不要用 `defect init` 生成的默认 plan（无 soc/stage2_soc/pp 等协议项）
- 中间禁止与 A 树比较；收尾才出对比表
- 让程序走：init → fetch → generate-inputs → git-snapshot → batch run --loop

## 已知程序坑（2026-08-14 实测）
1. `vasp-sop materials fetch -e Y Ti O -d Bi` 在 cpd/ 不存在时 **FileNotFoundError 崩溃**，不自动建目录 → 先 `mkdir -p cpd`
2. fetch 的 MP combo 缓存恢复**不含主相**（Y2Ti2O7_mp-1173093 缺失）→ 靠 `vasp-sop defect init -f <Formula> -d <dopant>` 的 MPRester fallback 补拉（**MP_API_KEY 需在 env**；init 也重写 plan.yaml 为默认值（无 SOC 两阶段），**之后必须把议定 plan 拷回去**）
3. `vasp-sop materials poscar mp-xxx` 只读缓存：`No cached POSCAR ... Run 'fetch' first`，但 fetch 给不出主相 → 死路，别用
4. init 拉相后 phase 判定 OK（`batch run --dry-run` 显示 STRUCTURE_OPT）

## 流程（实测顺序）
```bash
ROOT=/mnt/shared/home/2sidesniddle/vasp/recompute
mkdir -p $ROOT/Y2Ti2O7_d12
cp <A树>/plan.yaml $ROOT/Y2Ti2O7_d12/ && sed -i 's/min_distance: 10.0/min_distance: 12.0/' $ROOT/Y2Ti2O7_d12/plan.yaml
cd $ROOT/Y2Ti2O7_d12 && mkdir -p cpd
vasp-sop materials fetch -e Y Ti O -d Bi        # 22 相 + mol_O2，缺主相
vasp-sop defect init -f Y2Ti2O7 -d Bi          # MPRester fallback 补主相到 cpd/ + unitcell/structure_opt/POSCAR
cp <A树>/plan.yaml plan.yaml && sed -i 's/min_distance: 10.0/min_distance: 12.0/' plan.yaml   # 恢复议定 plan
cd $ROOT && vasp-sop batch run . --dry-run     # 验证 STRUCTURE_OPT 就绪
vasp-sop batch generate-inputs .               # 全部 cpd 输入（23 个，约 2.5 min，vise 模板 + NELM=50/EDIFF=1e-4 补丁）
vasp-sop batch git-snapshot .                  # ADR 0019 基线
```

## systemd loop + crisp 优先级
- **crisp 优先级由 roots 顺序决定**：`_dispatch_priority` = `10*(len(roots)-1-index)`，第一个 root 优先 10。多 root 传入时**靠前=高优先**。
- B root 想高优先：`batch run <Broot> <noop> --loop --poll 120`，`<noop>` 是空目录（占位，无 plan.yaml 被跳过；单树也能拿最高优先级）
- service：`~/.config/systemd/user/vasp-sop-b-<tag>.service`，ExecStart 同 vasp-sop-loop.service 模式（venv 路径、PATH、HOME），`Restart=always`
- 验证：`systemctl --user enable --now <svc>` → 20s 后 `sqlite3 ~/.crisp/data/agent.db "SELECT task_name,local_dir,priority,status FROM jobs WHERE local_dir LIKE '%recompute%'"` 应见 priority=10 的首作业（目标相 cpd/<formula>_mp-xxx）

## 预期轨迹
目标相收敛（44 原子约 5 min）→ loop 判收敛 → COMPETING（缺陷构建 176 原子超胞 ~10+ min + 21 相提交 + stage2 SOC 补充）→ CHEM_POT_DIAGRAM → UNITCELL_DEFECT → analyze → COMPLETE。每 cycle 120s 轮询。

## 收尾交付（议定）
- `formation_energies_compare.csv`：162 条目 × (defect/charge/E_f(A)/E_f(B)/ΔE_f/E_def/E_perfect/μ/pc/alignment/q_vbm) + md
- 缓存审计：crisp cache.db（`~/.crisp/data/cache.db`）对 B 路径 0 命中（cache.db 曾全空 = A 从未入缓存，审计天然干净）。独立轮次**缓存照开 + 事后审计**即可（若 A 从未走过 crisp 缓存则无 A→B 命中风险）
- A 树只读引用（formation_energies_20260813.csv 等），已收敛 A 树不重 analyze

## 监督
- 日志：`journalctl --user -u vasp-sop-b-<tag>.service -f`
- 进度：`vasp-sop batch status $ROOT`
- 注：`scripts/batch-watch.py` 只覆盖 2026 根，不含 recompute