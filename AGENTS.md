# AGENTS.md — PyDefect Workflow SOP

通用指令文件，适用于各类 AI 编码 agent（Claude Code、Codex、Cursor、Copilot 等）。

## 项目概要

本仓库是点缺陷 VASP 计算的标准化操作流程（SOP），基于 pydefect + vise 工具链。覆盖从完美晶胞到复合缺陷的完整生命周期。

## 核心纪律 ⚠️

以下规则是硬性要求，任何 agent 在执行本工作流时必须遵守：

1. **严禁手工构造 VASP 输入文件** — INCAR/POTCAR/KPOINTS 必须通过 `vise vasp_set` 生成。手工构造会导致参数不一致、POTCAR 顺序错误、形成能系统偏移。
2. **复合缺陷必须使用 Maker API** — `ComplexDefectMaker.from_supercell_info()` → `make_all_n_body()` → `generate_entries()` → `write()`。严禁使用底层 `generate_all_entries()` / `write_all()` 函数，它们不做去重。
3. **单条测试后再批量** — 生成 N 个条目后，先对 1 个条目跑 `vise vasp_set` + `diff` 验证 INCAR 参数，确认无误后再批量处理剩余。
4. **crisp 提交必须指定具体子目录** — `local_dir` 必须指向缺陷子目录（如 `defect/Va_C1_0/`），不能指向父级 `defect/`。
5. **后处理前先同步 output/** — crisp 把结果放到 `output/` 子目录，但 pydefect 命令从根目录读文件。需要先 `cp output/* .`。
6. **defect_entry.json 必须是 monty 序列化对象** — plain dict 会导致 efnv 报 `'dict' object has no attribute 'charge'`。
7. **ENCUT 跨所有计算保持一致** — 完美晶胞、竞争相、缺陷的 ENCUT 必须统一，否则形成能系统偏移。

## 7 阶段工作流速览

| 阶段 | 内容 | 关键命令 |
|------|------|----------|
| 1 | 完美晶胞：结构优化→能带→DOS→介电 | `vise vasp_set -x pbesol -t structure_opt/band/dos/dielectric_dfpt` |
| 2 | 竞争相：CPD 构建 + 化学势图 | `pydefect_vasp make_poscars` → VASP 优化 → `pydefect cpd_and_vertices` |
| 3 | 缺陷生成：超胞 → 缺陷集 → 间隙位 → 输入文件 | `pydefect supercell` → `defect_set` → `local_extrema` → `defect_entries` + `vise vasp_set -t defect` |
| 4 | VASP 计算：crisp 批量提交 | Python `register_job()` API，每个子目录独立注册 |
| 5 | 后处理：同步→calc_results→efnv→形成能 | `pydefect efnv` → `defect_energy_infos` → `defect_energy_summary` → `plot_defect_formation_energy` |
| 6 | 增量掺杂：已有项目上加掺杂 | `defect_set -d <element>` → 更新 CPD |
| 7 | 复合缺陷：N 体缺陷（Maker API） | `ComplexDefectMaker` 分阶生成 N=2→3→4 |

## 常见坑点

- **crisp 批量提交秒失败 (EXIT_CODE:1)** → `local_dir` 指向了父级目录。每个缺陷子目录必须单独注册。
- **`NotPrimitiveError`** → AECCAR 和 supercell 的原胞晶格常数不匹配。统一用优化后结构重新生成。
- **`pydefect_vasp calc_results` 不可用** → 用 Python API 从 vasprun.xml 生成（见 SKILL.md 5.2 节）。
- **复合缺陷命名** — 目录名按 `out_atom` 排序，`Si_C1+B_C1` 实际是 `B_C1+Si_C1`。glob 前先 `ls` 确认。
- **N≥4 枚举慢 (~190s)** — 正常，勿杀进程。用 `maker.enumerate_geometries(N_max=4)` 复用缓存。

## 完整文档

- **SKILL.md** — 完整 SOP（Hermes Agent 格式，含所有命令和参考）
- **references/** — 9 个专题参考文档（3C-SiC 测试、doped 对比、cpd 扩展、defect_entry.json 修复等）
- **scripts/verify-installation.sh** — 环境验证脚本
- **官方文档**: https://kumagai-group.github.io/pydefect/
