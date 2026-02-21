# OpenFOAM MCP

自然语言驱动的 OpenFOAM MCP Server。  
目标是让 AI 助手通过 MCP 直接完成 CFD 问题分析、案例生成、运行验证、并行求解与基础后处理。

## 1. 项目能力

- 模板化创建 OpenFOAM 案例（标准 `0/`, `constant/`, `system/` 结构）
- 基于自然语言分析问题并推荐模板/参数
- 网格配置生成（`blockMesh` / `snappyHexMesh`）
- 串行求解、并行求解、运行状态读取
- 残差图生成（`matplotlib`）
- 官方教程检索与文件读取（受路径安全约束）
- 运行前预检、数值稳定性评估与自动修复
- 端到端工作流：`openfoam_run_workflow_from_prompt`

## 2. 环境要求

### 基础依赖

- Python `>=3.10`
- `mcp`, `pydantic`, `jinja2`, `httpx`, `matplotlib`

### 执行 OpenFOAM 命令时的系统依赖

- OpenFOAM 已安装
- 并行求解需可用 `mpirun`（OpenMPI 或兼容 MPI）
- 建议先 `source` OpenFOAM 环境脚本（例如 `etc/bashrc`）

常见环境变量（用于预检与命令解析）：

- `WM_PROJECT_DIR`
- `WM_PROJECT_VERSION`
- `FOAM_APPBIN`
- `FOAM_TUTORIALS`

## 3. 安装

```bash
cd /path/to/openfoam-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## 4. 启动方式

### 方式 A：直接运行入口脚本

```bash
source .venv/bin/activate
python run_server.py
```

### 方式 B：模块方式

```bash
source .venv/bin/activate
python src/server.py
```

### 方式 C：安装后命令

```bash
source .venv/bin/activate
openfoam-mcp
```

## 5. MCP 客户端配置示例

项目内置示例：`.mcp.json`

```json
{
  "mcpServers": {
    "openfoam": {
      "command": "/home/your_user/openfoam-mcp/.venv/bin/python",
      "args": [
        "/home/your_user/openfoam-mcp/run_server.py"
      ]
    }
  }
}
```

按你的实际路径替换 `command` 和 `args`。

## 6. 快速使用流程

推荐从以下顺序开始：

1. `openfoam_generate_modeling_plan`  
输入自然语言需求，得到推荐模板、已识别参数、默认值补全与缺失项。
2. `openfoam_create_case`  
按模板参数生成案例目录（`case_path` 必须是绝对路径）。
3. `openfoam_preflight_check`  
执行环境与案例结构预检（建议选择对应 `profile`）。
4. `openfoam_run_solver` 或 `openfoam_run_parallel`  
执行串行/并行求解。
5. `openfoam_get_run_status` / `openfoam_generate_residual_plot`  
监控收敛并产出可视化。

若想一键执行，使用 `openfoam_run_workflow_from_prompt`。

## 7. 全部 MCP Tools（23 个）

### 模板与案例

- `openfoam_list_templates`
- `openfoam_get_template_info`
- `openfoam_create_case`
- `openfoam_validate_case`
- `openfoam_generate_mesh`
- `openfoam_generate_boundary_conditions`

### 执行与监控

- `openfoam_run_solver`
- `openfoam_run_parallel`
- `openfoam_get_run_status`
- `openfoam_generate_residual_plot`

### 知识与辅助计算

- `openfoam_analyze_problem`
- `openfoam_get_fluid_properties`
- `openfoam_calculate_yplus`
- `openfoam_search_tutorials`
- `openfoam_read_tutorial_file`

### 字典与调试

- `openfoam_get_patch_list`
- `openfoam_read_dictionary`
- `openfoam_update_dictionary`

### 稳定性与工作流

- `openfoam_preflight_check`
- `openfoam_assess_case_stability`
- `openfoam_apply_stability_fixes`
- `openfoam_generate_modeling_plan`
- `openfoam_run_workflow_from_prompt`

## 8. 模板清单（当前实现）

| Template ID | 类别 | Solver |
| --- | --- | --- |
| `pipe_flow` | incompressible | `simpleFoam` |
| `cavity_flow` | incompressible | `icoFoam` |
| `cylinder_flow` | incompressible | `pimpleFoam` |
| `natural_convection` | heat_transfer | `buoyantSimpleFoam` |
| `dam_break` | multiphase | `interFoam` |
| `bubble_rising` | multiphase | `interFoam` |
| `backward_step` | incompressible | `simpleFoam` |
| `channel_flow` | incompressible | `pimpleFoam` |
| `flat_plate` | incompressible | `simpleFoam` |
| `mixing_elbow` | incompressible | `simpleFoam` |
| `heat_exchanger` | heat_transfer | `buoyantSimpleFoam` |
| `shock_tube` | compressible | `rhoCentralFoam` |
| `supersonic_nozzle` | compressible | `rhoCentralFoam` |

## 9. 关键运行行为（升级后）

### `openfoam_preflight_check`

- 新增 `profile`：`diagnostic | mesh | solver | parallel`
- 新增 `strict`：严格模式下，关键缺失项升级为错误
- 输出包含：
  - 场景
  - 总体状态（`ready` / `degraded` / `blocked`）
  - 错误/警告/通过计数
- 在 `diagnostic` 场景下，缺失命令默认不阻塞（`warning`）

### `openfoam_run_solver`

- 未显式传入 solver 时，会从 `system/controlDict` 自动读取
- 命令解析顺序：`PATH` -> `FOAM_APPBIN`
- 若命令不可用，不硬失败，返回“未执行（环境未就绪）”并给出建议

### `openfoam_run_parallel`

- 若网格缺失且具备 `blockMeshDict` + `blockMesh`，会先自动建网格
- 并行依赖不完整时会自动回退串行求解（若 solver 可用）
- 并行和串行都不可执行时，返回明确环境缺失信息

### `openfoam_run_workflow_from_prompt`

- 会按意图自动选择 preflight profile（`mesh`/`solver`/`parallel`/`diagnostic`）
- 汇总 `warnings` 与 `failures`
- 状态更细化：
  - `completed`
  - `completed_with_warnings`
  - `partial_failed`

## 10. 输入与安全约束

- 多数 `case_path` 要求绝对路径（`/` 开头）
- 教程文件读取限制在 `FOAM_TUTORIALS` 目录内
- 字典读写限制在目标案例目录内，防止路径逃逸

## 11. 测试

运行全部测试：

```bash
source .venv/bin/activate
python -m pytest -q
```

## 12. 项目结构

```text
openfoam-mcp/
├── src/
│   ├── server.py                 # MCP 服务入口与 tool 注册
│   ├── core/                     # 生成、运行、并行、后处理、验证核心逻辑
│   ├── tools/                    # 各 MCP tool 实现
│   ├── templates/                # 模板注册与参数定义
│   └── knowledge/                # 求解器/边界/网格策略知识
├── examples/
│   └── parallel_and_postprocess_example.py
├── tests/
├── run_server.py
├── pyproject.toml
└── .mcp.json
```

## 13. 常见问题

### Q1: 提示找不到 `simpleFoam` / `blockMesh` / `decomposePar`

先执行 OpenFOAM 环境加载，再重试：

```bash
source /path/to/OpenFOAM/etc/bashrc
```

并先调用：

```text
openfoam_preflight_check(profile="solver" 或 "parallel")
```

### Q2: 并行求解失败

- 检查 `mpirun` 是否可用
- 检查 `decomposePar` / `reconstructPar` 是否可用
- 使用 `openfoam_run_parallel` 时确认 `n_processors >= 2`

### Q3: 为什么 workflow 返回 `completed_with_warnings`

表示流程主体已执行，但存在环境缺失、可选阶段跳过或预检告警。  
请查看返回中的 `warnings` 列表定位具体原因。

