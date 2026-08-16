---
name: vasp-sop-bootstrap-external-host
description: "vasp_sop 新体系 bootstrap 当宿主公式不在 MP 时：外部结构获取（OpenAlex/GitHub code search CIF 路径）、半占位有序展开、NELECT fix（POTCAR 恢复后 vise 库 ZVAL 错配）、plan encut 显式化、defect_in.yaml flow 格式与反位过滤、tag=long 并行提交（unitcell+cpd 不阻塞）。用于\"算新体系/新掺杂体系\"且宿主无 MP 收录，或 bootstrap 提交前的输入合规清单。"
---

# vasp_sop 新体系 bootstrap（宿主不在 MP / 输入合规 / 并行提交）

2026-08-16 Li2ZnGe3O8（Mn/Cr/Fe）会话沉淀。分 4 段：宿主定锚 → 树构建 → 输入合规 → 提交。

## 1. 宿主定锚（几何第一资产）

### 1a. 宿主公式不在 MP
- `fetch_formula_polymorphs(formula)` 返回 None 时：先查元素空间（chemsys='Li-Zn-Ge-O' 类）确认 MP 是否收录该组成；最近似相若空间群不同构（如 mp-772981 是 P2₁ 而文献报 P4₁32）**不可**作骨架。
- 结构获取路径（按序尝试）：
  1. `api.openalex.org/works?search=<formula>` 找 OA 论文（doi/PMC/GitHub 链接）
  2. `gh api "search/code?q=<formula> cif"` → 常命中 ML 数据集（NRC-Mila/OBELiX mfs.cif 是标准库，含 Li2ZnGe3O8 P4₃32 a=8.1961Å 56原子）——raw.githubusercontent.com 直连即可
  3. ScienceDirect/RSC/MDPI/ResearchGate 直连与 xray 代理（127.0.0.1:10809）均 403；EPubs/PMC 可用 curl -A UA 抓
- pymatgen 验证：组成（Li8Zn4Ge12O32）、spglib 空间群（symprec=0.01）、键长（Li-O~2.0/Ge-O~1.8-1.95）、密度。P4₁32 vs P4₃32 手性对映能量等价，接受任一。

### 1b. 半占位展开
- 尖晶石常有共享位（Li/Zn 8c 各 0.5）——pymatgen `to(fmt='poscar')` 拒绝 disordered。
- 有序展开：遍历部分占据位点（`len(site.species)>1`）分配为单一元素（满足总组成），再写 POSCAR。展开后对称性会降（P4₃32→P4₃）——**同一排序贯穿全部缺陷目录**保证参考一致即可。

### 1c. 新掺杂元素
- 加 U_TABLE（3d 惯例 U=3 同 Mn/Fe）+ INITIAL_MAGMOM（**3d 统一 5.0**，2026-08-16 决策）+ check_results collapse_elems——三处同加，漏 MAGMOM 会从 VASP 默认 1.0/site 起跑落错磁态（#151 机制）。
- pp 默认对齐 vise potcar_set normal（Ga→Ga_d、Gd→Gd_3 等）——`list_potcar_variants` 已修。

## 2. 树构建

- 宿主 POSCAR → `cpd/<formula>_mp-local/POSCAR`（本地宿主，poscar_src: MP mp-local）→ generate_config（元素空间相下载 + plan）。
- `build_all`：对称晶格建超胞（perfect ISIF=3 前）+ 缺陷结构 + 输入生成。

## 3. 输入合规（提交前必过）

### 3a. NELECT fix（重要）
- prepare_inputs 恢复 PSP 库 POTCAR 后，vise 按**它的库 POTCAR** 算 NELECT——ZVAL 不同则全错（Li2ZnGe3O8 130 目录差 24 e⁻）。
- build_all 已内置：`verify_nelect(fix=True)` 按目录 POTCAR 重算修正 → 重验 → 门禁。

### 3b. plan encut 显式化
- defect 生成时目录可能无 POTCAR → effective_encut 检测 None → 落 vise 默认 400。
- plan `encut: <宿主 POTCAR 1.3×max ENMAX>` 显式写（Li2ZnGe3O8=520）→ 生成即正确。

### 3c. defect_in.yaml 格式 + 缺陷集
- 格式必须 flow 风格：`Cr_Ge1: [0, 1, 2]`（yaml.dump sort_keys=False）——block 顶格列表 pydefect 不认。
- 反位过滤（用户审批惯例）：去掉阴离子↔阳离子反位（掺杂剂@O、O@阳离子位）；保留宿主阳离子空位（含 Va_O）与阳离子间反位。电荷态按掺杂氧化态收敛（Cr_Ge +0..2、Fe_Ge -2..0）。
- 过滤后删磁盘多余目录（未计算零损失）。

### 3d. protocol_sandbox_verify 全绿
- 弛豫腿 stage1 无 LDAU 是两阶段预期（verify 已适配）；ENCUT=520；perfect ISIF=3；单点腿带 U 无 SOC；MAGMOM 原子序对（掺杂在 POSCAR 末尾）。

## 4. 提交（tag=long，并行）

- **用 `--tag long` 不用 `--cluster` 指定**：long 标签在 duguex_5/6138+8259cl、ckduan_167/compute（都 free），避开 test 分区 20 分钟限制。
- **不要 wave1 单提交阻塞**：宿主 target、unitcell/structure_opt（宿主 supercell）、cpd 全部竞争相都是独立 ISIF=3 弛豫——一次性并行提交（crisp submit --dir <d> --tag long 循环）。
- defect 目录（174+）晶格已对称就绪（ISIF=2）——提交时机用户定。
- 两阶段：stage1 无 U 收敛后需 stage2 补 U（apply_final_protocol + CONTCAR 续算）——batch loop 停着时手动/授权处理。

## 验证

- 提交后 `crisp_status task` 确认 submitted + 分区（compute 非 test）；crisp completed ≠ 收敛——convergence 字段才是真判据（force_gate/max_f）。
