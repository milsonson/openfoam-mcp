# openfoam-remote 调用测试报告

## 测试目标

验证 `openfoam-remote` MCP 工具在远端环境中的可调用性、参数校验行为、核心链路可用性与错误可观测性。

## 测试环境

- 测试日期：`2026-02-23`
- 测试位置：远端 `openfoam-remote` MCP 服务
- OpenFOAM 版本（从 preflight 输出）：`11`
- 说明：本报告基于实际工具调用返回内容整理，不包含本地伪造数据。

## 测试范围

覆盖 `openfoam-remote` 全部 23 个工具接口：

1. `openfoam_list_templates`
2. `openfoam_get_fluid_properties`
3. `openfoam_search_tutorials`
4. `openfoam_get_template_info`
5. `openfoam_create_case`
6. `openfoam_get_patch_list`
7. `openfoam_preflight_check`
8. `openfoam_validate_case`
9. `openfoam_assess_case_stability`
10. `openfoam_read_dictionary`
11. `openfoam_update_dictionary`
12. `openfoam_apply_stability_fixes`
13. `openfoam_generate_mesh`
14. `openfoam_generate_boundary_conditions`
15. `openfoam_calculate_yplus`
16. `openfoam_read_tutorial_file`
17. `openfoam_run_solver`
18. `openfoam_get_run_status`
19. `openfoam_run_parallel`
20. `openfoam_generate_residual_plot`
21. `openfoam_analyze_problem`
22. `openfoam_generate_modeling_plan`
23. `openfoam_run_workflow_from_prompt`

## 执行记录

### 1) 查询与信息类接口

- `list_templates`：成功返回 13 个模板。
- `get_fluid_properties(air)`：成功返回空气物性。
- `search_tutorials`：初次失败（`keywords` 缺失）；修正为列表后成功。
- `get_template_info(cavity_flow/pipe_flow)`：成功返回参数定义。
- `read_tutorial_file`：成功读取教程 `controlDict`。

### 2) 案例创建与校验链路

- `create_case(cavity_flow)`：成功创建 `/tmp/of_remote_test_cavity`（远端）。
- `preflight_check`：`ready`，环境和命令检查通过。
- `validate_case`：通过，含 2 条警告（压力参考值、网格偏斜度）。
- `assess_case_stability`：初始 2 条警告（未启用 `adjustTimeStep`、未设置 `maxCo`）。
- `apply_stability_fixes`：首次请求传输错误，重试成功。
- `assess_case_stability`（修复后）：0 警告。

### 3) 字典与边界条件

- `read_dictionary`：可正常读取 `controlDict`、`fvSolution`、`turbulenceProperties`。
- `update_dictionary`：
  - 更新已存在键成功（`deltaT`）。
  - 新增不存在键失败（例如 `adjustTimeStep`）。
  - 新建不存在字典失败（例如 `constant/physicalProperties`）。
- `generate_boundary_conditions`：
  - 缺少 `field_name`/`boundary_definitions` 时失败。
  - 参数补全后，`uniform (1 0 0)` 报格式错误。
  - 改为 `(1 0 0)` 后成功写出 `0/U`。

### 4) 数值辅助

- `calculate_yplus`：初次失败（缺少 `length_scale`），修正后成功。
- `generate_mesh`：调用成功，识别已存在 `blockMeshDict` 并执行网格流程。

### 5) 求解执行链路

- `run_solver`：
  - cavity 案例失败：缺少 `constant/physicalProperties`。
  - pipe 案例失败：`Unknown function type solverInfo`。
- `get_run_status`：可正确返回错误日志尾部。
- `run_parallel`：
  - 初次失败（字段应为 `n_processors`）。
  - 修正后进入执行，但 `decomposePar` 报 `libmetisDecomp.so` 缺失；回退串行仍因案例问题失败。
- `generate_residual_plot`：日志无残差数据，生成失败。

### 6) 智能分析与工作流

- `analyze_problem`：初次失败（字段应为 `description`），修正后成功。
- `generate_modeling_plan`：同上，修正后成功。
- `run_workflow_from_prompt`：接口可调用，但中英文描述均返回 `template=unknown`、`confidence=0.00`。

## 结果汇总

| 项目 | 结果 |
|---|---|
| 接口可达性 | 23/23 可调用 |
| 正常业务通过 | 18/23 |
| 参数校验失败（可修正） | 多处存在 |
| 执行链路硬失败 | `run_solver`, `run_parallel`, `generate_residual_plot`（由环境/案例兼容触发） |
| 智能工作流异常 | `run_workflow_from_prompt` 识别失败 |

## 问题与风险

1. `create_case` 生成案例与当前求解环境存在兼容性缺口，导致求解直接失败。
2. 并行分解依赖缺失（`libmetisDecomp.so`），影响并行能力。
3. 多接口参数命名不直观，首次调用错误率较高。
4. `run_workflow_from_prompt` 模板识别失效，影响自然语言一键流程。

## 结论

`openfoam-remote` 在“信息查询、案例检查、稳定性评估/修复、字典读取”等能力上可用；在“自动生成案例后可直接求解”与“自然语言工作流识别”上仍有明显缺陷，当前不适合直接作为全自动生产链路。

## 后续建议

1. 为每个工具补充最小可用请求示例，明确字段与类型。
2. 增加 `create_case -> run_solver` 回归测试，保证模板产物可直接求解。
3. 在并行接口前做依赖预检并返回明确降级策略。
4. 为 `run_workflow_from_prompt` 增加中英文语料回归集并修复模板识别。

