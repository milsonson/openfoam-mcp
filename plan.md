# OpenFOAM Cloud Service (HTTP/SSE + Portal + Artifacts) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将当前 `openfoam-mcp` 改造成可部署在 GCloud 的商用服务，支持 HTTP/SSE、可访问 Portal URL、可下载完整建模与关键结果文件。

**Architecture:** 保留现有 `workflow_tools` 作为 CFD 执行核心，在 `src/server.py` 增加 Web 路由层（health、portal、artifacts）和 transport 配置（SSE/streamable-http）。执行结束后生成统一 artifacts 清单（bundle、logs、plots、kpi）并返回 `portal_url` 与下载 URL。v1 使用本地 artifact 目录（Cloud Run ephemeral）实现端到端链路，v2 切换 GCS Signed URL。

**Tech Stack:** Python 3.11, FastMCP, Starlette custom routes, pytest, Cloud Run, Docker.

---

## Scope

- 支持 `RDKIT` 风格的部署与服务体验：`/health`、`/mcp`、`/sse`、`/portal/*`、`/artifacts/*`
- `openfoam_run_workflow_from_prompt` 返回可交付 URL 与 artifacts 元数据
- Portal 页面展示任务状态、关键 KPI、文件下载入口
- 增加基础回收策略（TTL + max files）

## Non-Goals (v1)

- 不做多租户鉴权和组织级权限（后续补）
- 不做 GCS/Cloud SQL 强依赖（先跑通本地目录版本）
- 不做高成本 3D 在线可视化（先做结果摘要 + 下载）

## API/Output Contract (v1)

- `status`: `completed|completed_with_warnings|partial_failed|failed|needs_input`
- `portal_url`: 任务结果页面 URL
- `artifacts[]`: `name/type/url/path/size_bytes/created_at`
- `kpi_summary`: `converged/final_residuals/main_metrics`
- `quality_report`: `preflight/stability/validation`

---

### Task 1: 服务器配置与传输模式改造

**Files:**
- Modify: `src/server.py`
- Create: `src/web/config.py`
- Test: `tests/test_server_config.py`

**Step 1: Write the failing test**

- 新增测试覆盖环境变量解析：
  - `OPENFOAM_MCP_TRANSPORT` 支持 `sse|streamable-http|stdio`
  - `OPENFOAM_MCP_HOST/PORT` 默认值正确
  - `OPENFOAM_MCP_PUBLIC_HOST` 对 `0.0.0.0` 回退行为正确

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest -q tests/test_server_config.py`  
Expected: FAIL（配置函数/模块尚不存在）

**Step 3: Write minimal implementation**

- 新增 `src/web/config.py`：
  - `parse_transport()`
  - `normalize_public_host()`
  - `build_default_base_urls()`
- `src/server.py` 引入配置并按 env 运行 `mcp.run(transport=...)`

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest -q tests/test_server_config.py`  
Expected: PASS

**Step 5: Commit**

```bash
git add src/server.py src/web/config.py tests/test_server_config.py
git commit -m "feat: add cloud-oriented server transport and host config"
```

---

### Task 2: Artifact 存储与清理能力

**Files:**
- Create: `src/web/artifacts.py`
- Test: `tests/test_artifacts_storage.py`

**Step 1: Write the failing test**

- 覆盖以下场景：
  - 创建 job artifact 目录与 metadata
  - 生成下载 URL 路径
  - TTL/max-files 清理旧产物
  - 路径越界防护

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest -q tests/test_artifacts_storage.py`  
Expected: FAIL（模块不存在）

**Step 3: Write minimal implementation**

- 实现：
  - `create_job_dir(job_id)`
  - `list_job_artifacts(job_id)`
  - `cleanup_old_artifacts(ttl_seconds, max_jobs)`
  - `safe_resolve_artifact_path(path)`

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest -q tests/test_artifacts_storage.py`  
Expected: PASS

**Step 5: Commit**

```bash
git add src/web/artifacts.py tests/test_artifacts_storage.py
git commit -m "feat: add artifact storage and cleanup utilities"
```

---

### Task 3: Workflow 产物打包与返回 schema 升级

**Files:**
- Modify: `src/tools/workflow_tools.py`
- Create: `tests/test_workflow_artifacts_response.py`

**Step 1: Write the failing test**

- 断言 `openfoam_run_workflow_from_prompt(..., response_format=json)` 返回：
  - `portal_url`
  - `artifacts[]`
  - `kpi_summary`
  - `quality_report`

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest -q tests/test_workflow_artifacts_response.py`  
Expected: FAIL（新字段不存在）

**Step 3: Write minimal implementation**

- 在 workflow 末尾新增：
  - `manifest.json` 生成
  - `case_bundle.tar.zst` 打包（若 `zstd` 不可用回退 `.tar.gz`）
  - `kpi_summary` 提取（收敛、残差、关键统计）
  - `quality_report` 聚合（preflight/stability/validation）
  - 返回 `portal_url` 与 `artifacts[]`

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest -q tests/test_workflow_artifacts_response.py`  
Expected: PASS

**Step 5: Commit**

```bash
git add src/tools/workflow_tools.py tests/test_workflow_artifacts_response.py
git commit -m "feat: return portal and artifacts metadata from workflow"
```

---

### Task 4: 增加 HTTP 路由（health、artifacts、portal）

**Files:**
- Modify: `src/server.py`
- Create: `src/web/routes.py`
- Create: `tests/test_http_routes.py`

**Step 1: Write the failing test**

- 覆盖：
  - `GET /health` 返回 `status`
  - `GET /artifacts/{path}` 可下载允许类型文件
  - 非法路径返回 `400`
  - `GET /portal/{job_id}` 返回 HTML

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest -q tests/test_http_routes.py`  
Expected: FAIL

**Step 3: Write minimal implementation**

- 使用 `@mcp.custom_route(...)` 注册路由
- `FileResponse` 返回 artifact 文件
- 限制扩展名（`.json/.png/.csv/.log/.tar/.gz/.zst/.mp4/.gif/.html`）

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest -q tests/test_http_routes.py`  
Expected: PASS

**Step 5: Commit**

```bash
git add src/server.py src/web/routes.py tests/test_http_routes.py
git commit -m "feat: add health artifact and portal routes"
```

---

### Task 5: Portal 页面（精简但可商用）

**Files:**
- Create: `src/web/portal.py`
- Create: `src/web/templates/portal.html`
- Create: `tests/test_portal_render.py`

**Step 1: Write the failing test**

- 验证 HTML 包含：
  - `job_id`
  - `status`
  - `kpi_summary`
  - 下载按钮（artifact links）

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest -q tests/test_portal_render.py`  
Expected: FAIL

**Step 3: Write minimal implementation**

- 使用模板字符串/Jinja2 渲染 portal
- 前端 JS:
  - 渲染 KPI 卡片
  - 渲染 artifacts 下载列表
  - 展示 warning/failure badge

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest -q tests/test_portal_render.py`  
Expected: PASS

**Step 5: Commit**

```bash
git add src/web/portal.py src/web/templates/portal.html tests/test_portal_render.py
git commit -m "feat: add portal renderer with artifact downloads"
```

---

### Task 6: Docker 与 Cloud Run 启动流程

**Files:**
- Create: `Dockerfile`
- Create: `docker/start.sh`
- Modify: `README.md`
- Test: `tests/test_container_env_defaults.py`

**Step 1: Write the failing test**

- 验证默认环境变量：
  - `OPENFOAM_MCP_HOST=0.0.0.0`
  - `OPENFOAM_MCP_PORT` 跟随 `PORT`
  - 默认 `OPENFOAM_MCP_TRANSPORT=streamable-http`

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest -q tests/test_container_env_defaults.py`  
Expected: FAIL

**Step 3: Write minimal implementation**

- 新增容器镜像定义与入口脚本
- README 增加 GCloud 构建/推送/部署命令
- 文档明确：
  - MCP endpoint（`/mcp`）
  - health endpoint（`/health`）
  - portal/artifacts 访问方式

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest -q tests/test_container_env_defaults.py`  
Expected: PASS

**Step 5: Commit**

```bash
git add Dockerfile docker/start.sh README.md tests/test_container_env_defaults.py
git commit -m "feat: add cloud run containerization and deployment docs"
```

---

### Task 7: SSE 事件流（可选但建议）

**Files:**
- Modify: `src/server.py`
- Create: `src/web/events.py`
- Create: `tests/test_sse_events.py`

**Step 1: Write the failing test**

- `GET /jobs/{job_id}/events` 返回 SSE 格式行
- 至少包含 `stage_changed`、`job_completed` 事件

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest -q tests/test_sse_events.py`  
Expected: FAIL

**Step 3: Write minimal implementation**

- 新增内存级事件总线（v1）
- Workflow 执行关键阶段发事件
- Portal 前端可轮询/订阅刷新状态

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest -q tests/test_sse_events.py`  
Expected: PASS

**Step 5: Commit**

```bash
git add src/server.py src/web/events.py tests/test_sse_events.py
git commit -m "feat: add sse job event stream"
```

---

### Task 8: 回归与发布验证

**Files:**
- Modify: `README.md`
- Modify: `tests/test_integration.py` (如需)

**Step 1: Run targeted tests**

```bash
PYTHONPATH=src pytest -q tests/test_server_config.py tests/test_artifacts_storage.py tests/test_workflow_artifacts_response.py tests/test_http_routes.py tests/test_portal_render.py tests/test_container_env_defaults.py tests/test_sse_events.py
```

Expected: PASS

**Step 2: Run full regression**

```bash
PYTHONPATH=src pytest -q
```

Expected: PASS（或记录已知失败并给出原因）

**Step 3: Smoke test server (timeout to avoid hanging)**

```bash
timeout 8s python src/server.py
```

Expected: 服务正常启动并监听配置端口

**Step 4: Commit**

```bash
git add README.md tests
git commit -m "chore: finalize cloud service docs and regression coverage"
```

---

## Implementation Order

1. Task 1
2. Task 2
3. Task 3
4. Task 4
5. Task 5
6. Task 6
7. Task 7
8. Task 8

## Risks and Mitigations

- Cloud Run 本地盘非持久：v1 仅用于短期交付；v2 升级 GCS Signed URL。
- OpenFOAM 命令环境依赖复杂：保持 preflight 结果在 Portal 明确可见。
- 大文件下载带宽压力：默认提供 bundle + 关键文件，Portal 仅预览小图。
- 长任务超时：在返回结构中明确 `status/stage/error/retryable`。

## v2 Backlog (Not in this plan)

- OAuth/OIDC 鉴权与 tenant 隔离
- GCS/Cloud SQL/Redis 作业化
- 任务队列与重试机制
- 更完整前端交互（图表时间轴、日志流）
