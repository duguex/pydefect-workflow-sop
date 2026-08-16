---
name: vasp-sop-cpd-empty-edge-label-diagnosis
description: 诊断 vasp_sop 交互式报告 CPD 边界边无化合物标签：先比报告顶点数与磁盘 cpd/chem_pot_diag.json target_vertices_dict 数，不一致 = 生成器 2D 凸包过滤丢 3D 顶点，勿误判数据漂移。
---

# vasp-sop CPD 空边标签诊断

## 症状
CPD 区域图（formation_energy_interactive.html）某条边界边无化合物标签，`edgePhases(i,j)` 返回空。JS 侧顶点相位列表交集为空。

## 根因（2026-08-15 实证）
生成器 `vasp_sop/report/interactive.py` 曾用 `_convex_hull(poly_2d)`（2D 投影凸包）过滤/排序顶点。对 3D 稳定区域（4 宿主元素），真实顶点投影到 2D 后可能落在投影凸包内部 → 被静默删除 → JS 端 3D 凸包重建伪边 → 伪边端点相位交集为空 → 无标签。物理上相邻顶点必须共享边界相，空交集 = 代码伪边，不是数据问题。

实证：BaAl2B2O7 10→8（丢 A/F）、Gd2GaSbO7:Bi 10→6、La2SrSc2O7 8→5。

## 诊断步骤
1. 页面里 `VERTEX_MU.length` vs 磁盘 `cpd/chem_pot_diag.json` 的 `target_vertices_dict` 键数。不一致 → 生成器丢顶点。
2. 若一致，再查 `ALL_EDGES` 每条边 `edgePhases`（空 = 真数据缺相，多为批次漂移）。
3. 勿把"报告顶点少"误判为"batch 数据漂移"——先做顶点数对比。

## 修复（commit 072fb48）
- 移除生成器 `_convex_hull` 过滤：保留 chem_pot_diag.json 全部顶点；`poly_2d` 仅作 JS spring 布局种子；顶点顺序由 JS `hull2D`/springLayout 负责。
- 删除死代码 `_convex_hull`（含其注释）。

## 验证
- 13/13 重生成后 ALL_EDGES 空标签 = 0，顶点数与磁盘一致。
- 回归：verify_marker2.py（boundaryMax 0.00px）、verify_drag.py、verify_section2.py（固定 μ 截面）、pytest 39 passed。
- 交互脚本在 `~/.conda/envs/dgkan_rocm_3.11`（playwright），验证脚本模板在 /tmp/verify_*.py。

## 陷阱
- interactive.py 内 JS 是 f-string 双括号 `{{}}` 转义版本——edit anchor 必须用双括号。
- 改生成器后必须重跑 `vasp-sop report <dir> --interactive` 全部体系并页面级验证（pytest 只测源码生成，不写磁盘 HTML）。
- webui 按 ADR 0005 直接服务磁盘 HTML，无需重启。}
