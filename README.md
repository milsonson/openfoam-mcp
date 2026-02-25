# OpenFOAM MCP

自然语言驱动的 OpenFOAM MCP Server。它让 AI 助手通过 MCP 工具链完成 CFD 任务：从问题理解、模板选型、案例生成，到预检、求解、稳定性修复与结果交付。

## 核心亮点

- 端到端自动化：从自然语言直接执行 `建模计划 -> 建案 -> 预检 -> 求解 -> 残差图`。
- 生产可用兜底：并行失败时可自动回退串行，避免任务直接中断。
- 安全默认：`case_path`、教程读取、字典读写都带路径边界约束，降低误操作风险。
- 云原生交付：内置健康检查、Portal、Artifacts、作业状态与 SSE 事件流。
- 模板驱动扩展：当前覆盖不可压缩、传热、多相、可压缩典型案例，可持续扩容。

## 适用场景

- 让 AI 助手“可执行”地完成 OpenFOAM 工程任务，而不只是给建议。
- 为团队提供标准化 CFD 自动化入口（本地、容器、Cloud Run）。
- 在并行环境能力不稳定时，仍保持任务可交付（串行兜底）。

## 项目能力总览

- 模板化创建标准 OpenFOAM 目录：`0/`、`constant/`、`system/`
- 自然语言问题分析、模板推荐、参数补全
- 网格配置生成/更新：`blockMesh`（`snappyHexMesh` 接口预留）
- 串行求解、并行求解、日志状态读取
- 残差曲线生成（`matplotlib`）
- 官方教程检索与受限读取
- 运行前预检、稳定性评估、自动修复
- 端到端工作流执行与产物交付

## 环境要求

### 基础依赖

- Python `>= 3.10`
- 依赖包：`mcp`, `pydantic`, `jinja2`, `httpx`, `matplotlib` 等

### OpenFOAM 运行依赖

- OpenFOAM 已安装
- 并行求解需要 `mpirun`（OpenMPI 或兼容实现）
- 建议先 `source` OpenFOAM 环境（如 `etc/bashrc`）

常见环境变量（预检和命令解析会用到）：

- `WM_PROJECT_DIR`
- `WM_PROJECT_VERSION`
- `FOAM_APPBIN`
- `FOAM_TUTORIALS`

## 快速开始

### 1) 安装

```bash
cd /path/to/openfoam-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### 2) 启动服务

方式 A：

```bash
source .venv/bin/activate
python run_server.py
```

方式 B：

```bash
source .venv/bin/activate
python src/server.py
```

方式 C：

```bash
source .venv/bin/activate
openfoam-mcp
```

### 3) 最小调用流程

推荐顺序：

1. `openfoam_generate_modeling_plan`
2. `openfoam_create_case`
3. `openfoam_preflight_check`
4. `openfoam_run_solver` 或 `openfoam_run_parallel`
5. `openfoam_get_run_status` / `openfoam_generate_residual_plot`

如需一键执行，使用：`openfoam_run_workflow_from_prompt`。

## MCP 配置示例

项目内示例：`.mcp.json`

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

按实际路径替换 `command` 与 `args`。

## 传输模式与访问地址

支持 transport：

- `sse`
- `streamable-http`
- `stdio`

常用环境变量：

- `OPENFOAM_MCP_TRANSPORT`（默认 `sse`）
- `OPENFOAM_MCP_HOST`（默认 `127.0.0.1`）
- `OPENFOAM_MCP_PORT`（默认 `8000`，若有 `PORT` 则跟随）
- `OPENFOAM_MCP_SSE_PATH`（默认 `/sse`）
- `OPENFOAM_MCP_STREAMABLE_HTTP_PATH`（默认 `/mcp`）
- `OPENFOAM_MCP_ARTIFACT_DIR`（默认 `/app/artifacts`，不可写时回退 `/tmp/openfoam-mcp-artifacts`）
- `OPENFOAM_MCP_ARTIFACT_BASE_URL`
- `OPENFOAM_MCP_PORTAL_BASE_URL`

`streamable-http` 启动示例：

```bash
source .venv/bin/activate
OPENFOAM_MCP_TRANSPORT=streamable-http \
OPENFOAM_MCP_PORT=8011 \
python src/server.py
```

常用地址（本地）：

- MCP：`http://127.0.0.1:8011/mcp`
- 健康检查：`http://127.0.0.1:8011/health`
- Portal：`http://127.0.0.1:8011/portal/<job_id>`
- Artifact：`http://127.0.0.1:8011/artifacts/<job_id>/<file>`
- Job 状态：`http://127.0.0.1:8011/jobs/<job_id>/status`
- Job SSE：`http://127.0.0.1:8011/jobs/<job_id>/events`

## 全部 MCP Tools（23 个）

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

### 知识与辅助

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

## 模板清单（当前实现）

| Template ID | Category | Solver |
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

## 关键运行行为

### `openfoam_preflight_check`

- 场景：`diagnostic | mesh | solver | parallel`
- 输出：`ready / degraded / blocked` + 错误/警告/通过统计
- 并行场景会检查 `decomposePar` 与 `mpirun` 可执行性和运行时问题

### `openfoam_run_solver`

- 未显式传 solver 时，自动从 `system/controlDict` 读取
- 命令解析顺序：`PATH -> FOAM_APPBIN`
- 命令缺失时返回“未执行（环境未就绪）”，不做硬崩溃

### `openfoam_run_parallel`

- 无网格时可自动触发 `blockMesh`
- 并行依赖不完整时自动回退串行
- `decomposePar` 失败时回退串行
- 识别到 MPI/PMIx 运行时失败（例如 PMIx listener/socket 权限问题）时，自动回退串行

### `openfoam_run_workflow_from_prompt`

- 按任务意图自动选择 preflight profile
- `case_path` 可选；未提供时自动分配到允许目录（默认 `/tmp/openfoam-mcp/cases/<job_id>`）
- 返回 `portal_url` / `delivery_url` 用于最终交付
- 状态：`completed`、`completed_with_warnings`、`partial_failed`
- 并行失败可降级串行并标记 `execution_mode=serial_fallback`

## 输入与安全约束

- 多数 `case_path` 要求绝对路径
- `openfoam_run_workflow_from_prompt` 可省略 `case_path`（服务端安全分配）
- 教程读取限制在 `FOAM_TUTORIALS`
- 字典读写限制在案例目录，防止路径逃逸

## 测试

运行全部测试：

```bash
source .venv/bin/activate
python -m pytest -q
```

## 项目结构

```text
openfoam-mcp/
├── src/
│   ├── server.py                 # MCP 服务入口与 tool 注册
│   ├── core/                     # 生成、执行、并行、后处理、验证
│   ├── tools/                    # MCP tool 实现
│   ├── templates/                # 模板定义
│   └── knowledge/                # 物性/求解策略知识
├── examples/
├── tests/
├── docker/
├── run_server.py
└── .mcp.json
```

## Docker 与 Cloud Run 部署

`Dockerfile` 已内置 OpenFOAM 11。容器启动脚本会尝试加载 `/opt/openfoam11/etc/bashrc`，并补齐 `LD_LIBRARY_PATH`（包含 `dummy` 分解库目录）。

### 本地 Docker

```bash
cd /path/to/openfoam-mcp
docker build -t openfoam-mcp:latest .
docker run --rm -p 8080:8080 \
  -e OPENFOAM_MCP_TRANSPORT=streamable-http \
  -e OPENFOAM_MCP_ARTIFACT_BASE_URL=http://127.0.0.1:8080/artifacts \
  -e OPENFOAM_MCP_PORTAL_BASE_URL=http://127.0.0.1:8080/portal \
  openfoam-mcp:latest
```

访问：

- 健康检查：`http://127.0.0.1:8080/health`
- MCP：`http://127.0.0.1:8080/mcp`

### Cloud Run（Artifact Registry）

```bash
PROJECT_ID="<your-gcp-project-id>"
REGION="us-central1"
REPO="openfoam-mcp"
IMAGE="openfoam-mcp"
SERVICE="openfoam-mcp"

gcloud config set project "${PROJECT_ID}"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com
gcloud artifacts repositories create "${REPO}" \
  --repository-format=docker \
  --location="${REGION}" \
  --description="OpenFOAM MCP images"
gcloud auth configure-docker "${REGION}-docker.pkg.dev"

IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${IMAGE}:latest"
docker build --platform linux/amd64 -t "${IMAGE_URI}" .
docker push "${IMAGE_URI}"

gcloud run deploy "${SERVICE}" \
  --image "${IMAGE_URI}" \
  --region "${REGION}" \
  --allow-unauthenticated
```

回填 URL：

```bash
SERVICE_URL="$(gcloud run services describe "${SERVICE}" --region "${REGION}" --format='value(status.url)')"
gcloud run services update "${SERVICE}" \
  --region "${REGION}" \
  --set-env-vars "OPENFOAM_MCP_TRANSPORT=streamable-http,OPENFOAM_MCP_ARTIFACT_BASE_URL=${SERVICE_URL}/artifacts,OPENFOAM_MCP_PORTAL_BASE_URL=${SERVICE_URL}/portal"
```

## 常见问题（FAQ）

### 1) 找不到 `simpleFoam` / `blockMesh` / `decomposePar`

先加载 OpenFOAM 环境：

```bash
source /path/to/OpenFOAM/etc/bashrc
```

然后执行：

```text
openfoam_preflight_check(profile="solver" 或 "parallel")
```

### 2) 并行求解失败怎么办

建议排查顺序：

1. `mpirun`、`decomposePar`、`reconstructPar` 是否可用
2. `n_processors >= 2` 是否满足
3. 是否出现 `libmetisDecomp.so` 缺失
4. 是否出现 PMIx/MPI 运行时权限问题（如 `listener thread failed to start`、`socket() failed with errno=1`）

说明：在受限沙箱环境（如部分无服务器运行时）中，MPI 可能不可用。此时系统会尝试回退串行，建议将并行计算放到 VM/K8s/HPC 节点执行。

### 3) 为什么 workflow 返回 `completed_with_warnings`

表示主流程完成，但出现了告警（例如预检告警、阶段跳过、并行回退串行）。请查看返回中的 `warnings` 字段。

## Roadmap

- 可观测性：指标、失败率、时延与实例健康告警
- 存储外部化：Artifacts 从本地目录迁移到对象存储
- 异步任务化：重任务走队列/worker，提升并发和可靠性

