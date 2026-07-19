# 快速上手指南

本指南帮助新用户从零开始使用 **vasp-sop** —— VASP 点缺陷高通量计算流水线编排工具。

---

## 1. 环境检查

### Python 版本

vasp-sop 要求 Python >= 3.10：

```bash
python3 --version   # 需要 >= 3.10
```

### 安装

```bash
cd /path/to/vasp_sop
pip install -e .
```

验证安装成功：

```bash
vasp-sop --help
vasp-sop --version   # 应输出 vasp-sop 0.1.0
```

### Materials Project API Key

从 [Materials Project](https://materialsproject.org/) 获取 API key，设置环境变量：

```bash
export MP_API_KEY="your-api-key-here"
```

> 也支持 `PMG_MAPI_KEY` 环境变量（pymatgen 格式）。

### vasp-cache 检查

vasp-sop 依赖 [vasp-cache](https://github.com/duguex/vasp-cache) 存储计算结果。确认缓存目录可用：

```bash
vasp-sop cache status
```

首次使用时缓存为空，这是正常的。缓存数据存储在 `~/.vasp_sop/`（meta.json + blobs.json）。

### HPC 环境（可选）

如需提交 VASP 作业到集群，确认 `crisp` CLI 在 PATH 中：

```bash
which crisp
```

---

## 2. 最短路径：从材料到缺陷形成能

以下 5 步完成一次完整的缺陷计算流程。

### 步骤 1：下载竞争相

```bash
vasp-sop materials fetch -e Ga N -d Mg -o cpd
```

- `-e`：主体材料的元素（如 Ga N）
- `-d`：掺杂元素（可选，如 Mg Si）
- `-o`：输出目录（默认 `cpd`）

### 步骤 2：创建项目并生成 plan.yaml

```bash
mkdir my_project && cd my_project
vasp-sop defect init -f GaN -d Mg
```

这会在当前目录生成 `plan.yaml` 配置文件。编辑该文件调整参数（functional、supercell 大小等）。

### 步骤 3：干运行（不提交 VASP）

```bash
vasp-sop batch run . --dry-run
```

干运行会：
- 检查所有系统状态
- 构建缺陷结构、生成 VASP 输入文件
- **不提交**任何 VASP 作业

用于验证配置是否正确。

### 步骤 4：正式运行

```bash
vasp-sop batch run .
```

流水线自动推进每个系统经过以下阶段：

```
TARGET → COMPETING → CPD_POST → UC_DF → DONE
```

- 默认轮询间隔 60 秒，可用 `--poll 120` 调整
- 可用 `--exclude` 排除特定系统

### 步骤 5：查看缓存状态

```bash
vasp-sop cache status --verbose
```

---

## 3. CLI 命令概览

| 命令 | 说明 |
|------|------|
| `vasp-sop batch run <root>` | 多系统批量流水线（主工作流） |
| `vasp-sop batch run <root> --dry-run` | 干运行：生成输入但不提交 VASP |
| `vasp-sop batch status <root>` | 显示所有系统状态表 |
| `vasp-sop batch generate-inputs <root>` | 为所有系统生成 VASP 输入 |
| `vasp-sop batch submit <root>` | 提交所有系统的 VASP 计算 |
| `vasp-sop pipeline -c plan.yaml` | 单系统完整流水线 |
| `vasp-sop defect init -f <formula>` | 生成 plan.yaml 配置 |
| `vasp-sop defect run -c plan.yaml` | 运行缺陷流水线 |
| `vasp-sop defect resume -r <root>` | 从保存状态恢复流水线 |
| `vasp-sop defect status -r <root>` | 显示流水线状态 |
| `vasp-sop defect build <dir>` | 独立缺陷结构生成 |
| `vasp-sop defect analyze <dir>` | 独立缺陷后处理 |
| `vasp-sop materials fetch -e <elements>` | 从 MP 下载竞争相 |
| `vasp-sop materials phases -e <elements>` | 列出已缓存的相 |
| `vasp-sop materials poscar <mp-id>` | 按 MP-ID 下载单个 POSCAR |
| `vasp-sop materials cache list` | 列出 MP 缓存 |
| `vasp-sop materials cache clear` | 清除 MP 缓存 |
| `vasp-sop cache status [--verbose]` | 缓存统计 |
| `vasp-sop cache put <path>` | 缓存一个 VASP 计算目录 |
| `vasp-sop cache put <path> -r` | 递归扫描目录树缓存 |
| `vasp-sop cache query --formula <f>` | 语义化跨项目缓存查询 |
| `vasp-sop cache verify` | 检查缓存一致性 |
| `vasp-sop cache migrate` | 从旧 SQLite 迁移到 JSONStore |
| `vasp-sop vasp inputs <dir>` | 通过 vise 生成 VASP 输入 |
| `vasp-sop vasp check <dir>` | 检查 VASP 是否收敛 |
| `vasp-sop cpd energies <dir> -f <formula>` | 计算组分能量 |
| `vasp-sop cpd diagram <dir>` | 求解并绘制相图 |
| `vasp-sop unitcell yaml <dir>` | 从 VASP 输出生成 unitcell.yaml |

全局选项：

| 选项 | 说明 |
|------|------|
| `--version` | 显示版本号 |
| `-v, --verbose` | 启用调试日志 |

---

## 4. 常见问题

### `vasp-sop: command not found`

未正确安装或不在虚拟环境中。重新执行：

```bash
pip install -e .
```

确认 `pip` 对应的 Python 环境已激活。

### `MP_API_KEY` 相关报错

- 确认环境变量已设置：`echo $MP_API_KEY`
- 在 HPC 集群上需写入 `~/.bashrc` 或作业脚本中
- API key 从 https://materialsproject.org/ 个人账户获取

### 干运行报错 "No plan.yaml found"

目标目录下缺少 `plan.yaml`。使用以下命令生成：

```bash
vasp-sop defect init -f <化学式> -d <掺杂元素>
```

### 缓存查询无结果

- 首次使用缓存为空属正常现象
- 确认计算目录包含 OUTCAR 文件
- 使用 `vasp-sop cache put <path> -r` 手动导入已有计算结果

### VASP 作业提交失败

- 确认 `crisp` 在 PATH 中：`which crisp`
- 确认 VASP 容器路径存在（默认 `/mnt/shared/vasp_latest.sif`）
- 查看日志获取详细错误：`vasp-sop batch run . -v`

### 流水线卡在某个阶段

- 使用 `vasp-sop defect status -r .` 查看当前阶段
- 检查对应目录的 OUTCAR 是否收敛：`vasp-sop vasp check <calc_dir>`
- 未收敛的计算会自动通过 CONTCAR 重启（最多 20 次）

### 4 元体系 CPD 报错

pydefect 不支持超过 3 维的 halfspace 相图。vasp-sop 会自动跳过 4 元 CPD 图，无需手动处理。

---

## 更多信息

| 文档 | 内容 |
|------|------|
| [README.md](README.md) | 项目概览 |
| [FEATURES.md](FEATURES.md) | 完整功能清单 |
| [PROJECT.md](PROJECT.md) | 项目详细说明 |
| [docs/agent-conventions.md](docs/agent-conventions.md) | 架构与开发规范 |
