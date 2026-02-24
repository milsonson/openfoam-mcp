# openfoam-remote 调用测试报告（v2）

## 测试目标

基于 2026-02-24 新一轮远端联调，沉淀可复现的测试结论，重点回答：

1. 与上一轮（2026-02-24 早些时候日志、2026-02-23 汇总）相比是否有实质改进。
2. 哪些问题已改善，哪些阻塞项仍会影响下一轮优化。
3. 下一次优化应优先投入哪些修复项与回归用例。

## 测试环境

- 测试日期：`2026-02-24`
- 测试位置：远端 `openfoam-remote` MCP 服务
- OpenFOAM 版本：`11`（由 `preflight_check` 返回 `WM_PROJECT_VERSION=11`）
- 测试方式：真实工具调用（成功路径 + 参数校验失败路径 + 运行时失败路径）
- 对比基线：
  - `docs/test-reports/2026-02-23-openfoam-remote-call-test.md`
  - `docs/test-reports/openfoam_remote_mcp_test_log_2026-02-24.md`

## 测试范围

本轮覆盖 `openfoam-remote` 24 个工具，重点加深以下链路：

1. 端到端求解链路：`create_case -> preflight/validate -> run_solver -> get_run_status -> residual_plot`
2. 稳定性链路：`assess_case_stability -> apply_stability_fixes -> re-run`
3. 智能链路：`analyze_problem -> generate_modeling_plan -> run_workflow_from_prompt`
4. 多模板链路：
   - `cavity_flow`（`icoFoam`）
   - `dam_break`（`interFoam`）
   - `shock_tube`（`rhoCentralFoam`）
5. 并行链路：`run_parallel` 降级行为

## 执行记录

### 1) 基础可用性与信息类工具

- `list_templates/get_template_info/get_fluid_properties/search_tutorials/read_tutorial_file` 均可用。
- 参数校验行为明确：
  - `search_tutorials` 的 `keywords` 必须为列表。
  - `analyze_problem`、`generate_modeling_plan`、`run_workflow_from_prompt` 使用 `description` 字段。

### 2) `cavity_flow + icoFoam` 端到端

- `create_case` 成功（远端路径 `/tmp/remote_cavity_test_20260224`）。
- `preflight_check` 状态 `ready`。
- `run_solver` 成功并收敛（两次复跑均完成，残差量级 `1e-7 ~ 1e-6`）。
- `get_run_status` 能返回完整日志尾部和最新残差。
- `generate_residual_plot` 成功生成 `residuals.png`。
- `assess_case_stability` 提示 `adjustTimeStep/maxCo`，`apply_stability_fixes` 写入后复跑通过。

### 3) `dam_break + interFoam` 链路

- `create_case` 成功，但 `run_solver` 失败：
  - `FOAM FATAL ERROR: cannot find file ".../constant/momentumTransport"`
- 说明：模板生成与运行时文件要求存在兼容性缺口，导致“可建例但不可求解”。
- `generate_residual_plot` 因无有效残差数据失败（符合预期）。

### 4) `shock_tube + rhoCentralFoam` 链路

- `create_case` 成功，但首次 `run_solver` 失败：
  - `keyword PIMPLE is undefined in dictionary ".../fvSolution"`
- 应用稳定性修复后再次 `run_solver`，报新错误：
  - `Supported energy type is e, thermodynamics package provides h`
- 说明：不仅是算法块问题，热物性配置也存在求解器不匹配。

### 5) 并行与工作流行为

- `run_parallel`：
  - 仍遇到 `libmetisDecomp.so` 缺失。
  - 工具会自动回退串行并完成（具备降级能力，但并行能力受限）。
- `run_workflow_from_prompt`：
  - 中文描述可识别 `cavity_flow` 并完成工作流。
  - 返回中“求解阶段 `skipped`”仍存在，不满足一键端到端求解预期。

## 结果汇总

| 项目 | 本轮结果 | 与上一轮对比 |
|---|---|---|
| 工具可调用性 | 24/24 可调用 | 持平（保持可调用） |
| `cavity_flow` 端到端求解 | 成功、可收敛、可出残差图 | 改善（上一轮 `icoFoam` 失败） |
| `update_dictionary` 误改风险 | 本轮未复现 | 有改善迹象（需回归测试确认） |
| `run_parallel` 并行能力 | `metis` 缺失，自动回退串行 | 持平（问题仍在） |
| `run_workflow_from_prompt` 识别能力 | 较上一轮改善（可识别） | 改善 |
| `run_workflow_from_prompt` 求解阶段 | `solver_run = skipped` | 持平（问题仍在） |
| 多相/可压缩模板求解 | `dam_break/shock_tube` 均失败 | 新增暴露（本轮扩展测试发现） |

## 问题与风险

1. 模板与求解器配置兼容性不足（高风险）
   - `dam_break` 缺 `momentumTransport`
   - `shock_tube` 的 `fvSolution/thermophysicalProperties` 与 `rhoCentralFoam` 不匹配
2. 并行依赖缺失（中高风险）
   - `libmetisDecomp.so` 缺失导致并行不可用，仅能降级串行
3. 校验规则对求解器类型区分不足（中风险）
   - 对 `interFoam/rhoCentralFoam` 仍提示偏向单相不可压模板的必需文件
4. 自然语言一键流程未闭环（中风险）
   - `run_workflow_from_prompt` 结果中求解阶段持续 `skipped`

## 结论

本轮相较上轮有明确进展：`cavity_flow` 已恢复端到端可求解，智能模板识别能力也有所改善。  
但整体仍未达到“稳定可自动化生产”的标准，核心短板集中在多相/可压缩模板兼容与并行运行环境依赖。

## 后续建议

1. 最高优先级：修复模板产物与求解器兼容
   - `dam_break`：补齐 `interFoam` 必需配置（含 `momentumTransport`）
   - `shock_tube`：修正 `rhoCentralFoam` 对应的 `fvSolution` 与热物性能源形式
2. 增加强制回归链路（CI）
   - 每个代表模板都执行 `create_case -> run_solver`（至少 `cavity_flow/dam_break/shock_tube`）
3. 修复 `run_workflow_from_prompt` 闭环行为
   - 增加“显式执行求解”开关与断言，避免默认 `skipped`
4. 优化并行前置检查
   - 在 `run_parallel` 前检测 `metis` 可用性，提前给出可执行降级建议
5. 固化对比看板
   - 下轮报告沿用本文件表格，追踪“已修复/未修复/回归失败”状态，减少重复排查成本

