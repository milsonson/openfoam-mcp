# openfoam-remote MCP 详细测试日志

测试日期: 2026-02-24  
测试目标: 覆盖 `openfoam-remote` 全部工具（24/24），同时覆盖成功路径、参数校验失败路径、运行时失败路径。  
测试执行环境: 远端 OpenFOAM 11（从工具返回的 `WM_PROJECT_VERSION=11` 与命令路径确认）

## 1. 总体结论

- 工具覆盖: 24/24 已调用
- 通过: 24 个工具均可被调用并返回结构化结果
- 发现问题:
  1. `update_dictionary` 以键名全局替换，可能误改 `functions` 内同名字段（`writeInterval`）
  2. `cavity_flow + icoFoam` 默认配置下包含 `yPlus` functionObject，运行时报 “Unable to find turbulence model in the database”
  3. `run_parallel` 在当前环境遇到 `libmetisDecomp.so` 缺失，会自动回退串行

---

## 2. 逐项调用日志

说明: 每项包含 `输入` 与 `关键输出`。失败项保留原始报错用于复现。

### T01 `openfoam_list_templates`（成功）
输入:
```json
{}
```
关键输出:
- 返回模板列表（含 `pipe_flow`, `cavity_flow`, `cylinder_flow`, `dam_break`, `flat_plate`, `shock_tube` 等）

---

### T02 `openfoam_search_tutorials`（失败：缺字段）
输入:
```json
{"query":"cavity"}
```
关键输出:
```text
validation error: params.keywords Field required
```

### T03 `openfoam_search_tutorials`（失败：类型错误）
输入:
```json
{"keywords":"cavity"}
```
关键输出:
```text
validation error: params.keywords Input should be a valid list
```

### T04 `openfoam_search_tutorials`（成功）
输入:
```json
{"keywords":["cavity","lidDriven"],"max_results":5}
```
关键输出:
- 返回 5 条教程路径，如 `/opt/openfoam11/tutorials/incompressibleFluid/cavity`

---

### T05 `openfoam_get_fluid_properties`（成功）
输入:
```json
{"fluid_name":"air"}
```
关键输出:
- 空气: `rho=1.225`, `nu=1.5e-05`, `mu=1.8e-05`, `Cp=1005`

### T06 `openfoam_get_fluid_properties`（成功）
输入:
```json
{"fluid_name":"water"}
```
关键输出:
- 水: `rho=1000`, `nu=1e-06`, `mu=0.001`, `Cp=4182`

---

### T07 `openfoam_get_template_info`（成功）
输入:
```json
{"template_id":"cavity_flow"}
```
关键输出:
- 求解器 `icoFoam`
- 必填参数: `width,height,lid_velocity,fluid`

### T08 `openfoam_get_template_info`（成功）
输入:
```json
{"template_id":"flat_plate"}
```
关键输出:
- 求解器 `simpleFoam`

### T09 `openfoam_get_template_info`（失败：未知模板）
输入:
```json
{"template_id":"not_exists"}
```
关键输出:
```text
错误: 未找到模板 'not_exists'
```

---

### T10 `openfoam_create_case`（失败：路径策略）
输入:
```json
{"template_id":"cavity_flow","case_path":"/home/milsonson/openfoam_remote_test/cavity_case","parameters":{"width":0.1,"height":0.1,"lid_velocity":1.0,"fluid":"water","end_time":1.0}}
```
关键输出:
```text
案例路径不在允许范围内。允许根目录: /tmp
```

### T11 `openfoam_create_case`（成功）
输入:
```json
{"template_id":"cavity_flow","case_path":"/tmp/openfoam_remote_test/cavity_case","parameters":{"width":0.1,"height":0.1,"lid_velocity":1.0,"fluid":"water","end_time":1.0}}
```
关键输出:
- 案例创建成功，求解器 `icoFoam`
- 自动执行 `blockMesh/checkMesh`
- 初始验证通过

### T12 `openfoam_create_case`（失败：未知模板）
输入:
```json
{"template_id":"nonexistent_template","case_path":"/tmp/openfoam_remote_test/bad_case","parameters":{}}
```
关键输出:
```text
错误: Unknown template: nonexistent_template
```

---

### T13 `openfoam_get_patch_list`（成功）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case"}
```
关键输出:
- 返回 patch: `movingWall`, `fixedWalls`, `frontAndBack`（以及 `FoamFile`）

### T14 `openfoam_read_dictionary`（失败：参数名错误）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case","dictionary_path":"system/controlDict"}
```
关键输出:
```text
validation error: params.dict_path Field required
```

### T15 `openfoam_read_dictionary`（成功）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case","dict_path":"system/controlDict"}
```
关键输出:
- 成功读取 `controlDict` 全文

### T16 `openfoam_read_dictionary`（安全校验成功）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/pipe_case","dict_path":"../../../../etc/passwd"}
```
关键输出:
```text
错误: dict_path 必须位于案例目录内
```

---

### T17 `openfoam_update_dictionary`（成功）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case","dict_path":"system/controlDict","updates":{"endTime":0.2,"writeInterval":0.05}}
```
关键输出:
- 更新成功

复测发现:
- `functions/residuals/writeInterval` 也被改为 `0.05`（整数位字段被误改风险）

### T18 `openfoam_update_dictionary`（修复测试）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case","dict_path":"system/controlDict","updates":{"writeInterval":1}}
```
关键输出:
- 更新成功（将 `writeInterval` 改回整数）

---

### T19 `openfoam_validate_case`（成功但有告警）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case"}
```
关键输出:
- `✅ 验证通过`
- 告警: 压力场参考值、网格偏斜度

### T20 `openfoam_validate_case`（成功）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/pipe_case"}
```
关键输出:
- 21 项检查通过，含语法检查与网格检查

---

### T21 `openfoam_preflight_check`（成功：带 case_path）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case"}
```
关键输出:
- 状态 `ready`
- OpenFOAM 环境变量与命令可用

### T22 `openfoam_preflight_check`（成功：仅诊断）
输入:
```json
{}
```
关键输出:
- 环境就绪；未提供 `case_path` 时跳过案例结构检查

---

### T23 `openfoam_generate_mesh`（成功）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case"}
```
关键输出:
- 识别 `blockMeshDict` 并执行 `blockMesh`

---

### T24 `openfoam_run_solver`（失败：类型错误引发 OpenFOAM IO FATAL）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case","solver":"icoFoam"}
```
关键输出:
```text
FOAM FATAL IO ERROR:
wrong token type - expected int32_t ... controlDict/functions/residuals/writeInterval
```

### T25 `openfoam_run_solver`（失败：湍流模型缺失）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case","solver":"icoFoam"}
```
关键输出:
```text
FOAM FATAL ERROR:
Unable to find turbulence model in the database
From functionObjects::yPlus::execute()
```

### T26 `openfoam_run_solver`（成功）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/pipe_case","solver":"simpleFoam"}
```
关键输出:
- 状态 `完成`
- 最终残差: `Ux=1.14e-04`, `Uy=7.13e-04`, `p=3.65e-03`, `epsilon=1.59e-05`, `k=1.70e-04`

### T27 `openfoam_run_solver`（成功：不显式传 solver）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/pipe_case"}
```
关键输出:
- 自动使用 `simpleFoam`，运行成功

---

### T28 `openfoam_get_run_status`（失败分支可见）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case"}
```
关键输出:
- 日志尾部包含 `Unable to find turbulence model in the database`

### T29 `openfoam_get_run_status`（成功）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/pipe_case"}
```
关键输出:
- 状态 `已完成`
- 显示最新残差与日志尾部

### T30 `openfoam_get_run_status`（空日志分支）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/no_case"}
```
关键输出:
```text
未找到日志文件。求解器可能尚未运行。
```

---

### T31 `openfoam_generate_residual_plot`（失败分支）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case","solver":"icoFoam"}
```
关键输出:
```text
错误: 未在日志文件中找到残差数据
```

### T32 `openfoam_generate_residual_plot`（成功）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/pipe_case","solver":"simpleFoam"}
```
关键输出:
- 生成 `residuals.png`
- 包含 `Ux/Uy/p/epsilon/k` 五条曲线，各 50 点

---

### T33 `openfoam_calculate_yplus`（失败：缺参数）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case"}
```
关键输出:
```text
validation error: params.velocity Field required
validation error: params.length_scale Field required
```

### T34 `openfoam_calculate_yplus`（成功）
输入:
```json
{"velocity":0.5,"length_scale":0.05,"fluid":"water","target_yplus":30}
```
关键输出:
- `Re=25000`
- 第一层网格高度 `0.9699 mm`

---

### T35 `openfoam_assess_case_stability`（成功，cavity 有告警）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case"}
```
关键输出:
- 告警: `adjustTimeStep` 建议开启、`maxCo` 未设置

### T36 `openfoam_assess_case_stability`（成功，pipe 无告警）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/pipe_case"}
```
关键输出:
- 高风险 0，告警 0

### T37 `openfoam_apply_stability_fixes`（成功，cavity）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/cavity_case"}
```
关键输出:
- 写入 `adjustTimeStep yes`, `maxCo=1`
- 设置 `PISO.nNonOrthogonalCorrectors=1`

### T38 `openfoam_apply_stability_fixes`（成功，pipe 无改动）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/pipe_case","strategy":"conservative"}
```
关键输出:
- 未检测到需要修改项

---

### T39 `openfoam_generate_boundary_conditions`（失败：缺参数）
输入:
```json
{"description":"2D lid-driven cavity..."}
```
关键输出:
```text
params.case_path / field_name / boundary_definitions Field required
```

### T40 `openfoam_generate_boundary_conditions`（成功）
输入:
```json
{
  "case_path":"/tmp/openfoam_remote_test/pipe_case",
  "field_name":"U",
  "boundary_definitions":{
    "inlet":{"type":"fixedValue","value":"uniform (0.5 0 0)"},
    "outlet":{"type":"zeroGradient"},
    "walls":{"type":"noSlip"}
  }
}
```
关键输出:
- 成功写入 `0/U`
- 三个边界类型正确回显

---

### T41 `openfoam_read_tutorial_file`（成功）
输入:
```json
{"tutorial_path":"/opt/openfoam11/tutorials/incompressibleFluid/cavity","file_path":"system/controlDict"}
```
关键输出:
- 成功读取官方教程 `controlDict`

### T42 `openfoam_read_tutorial_file`（失败）
输入:
```json
{"tutorial_path":"/opt/openfoam11/tutorials/incompressibleFluid/cavity","file_path":"system/notExists"}
```
关键输出:
```text
错误: 文件不存在
```

---

### T43 `openfoam_run_parallel`（部分成功：并行失败后回退串行）
输入:
```json
{"case_path":"/tmp/openfoam_remote_test/pipe_case","solver":"simpleFoam","n_processors":2}
```
关键输出:
- `decomposePar` 失败：`libmetisDecomp.so` 缺失
- 自动回退串行并成功完成

---

### T44 `openfoam_analyze_problem`（失败：字段名错误）
输入:
```json
{"problem_description":"2D lid-driven cavity..."}
```
关键输出:
```text
validation error: params.description Field required
```

### T45 `openfoam_analyze_problem`（成功）
输入:
```json
{"description":"2D lid-driven cavity, incompressible transient at high Reynolds number with moving lid"}
```
关键输出:
- 推荐模板 `cavity_flow`
- 置信度 `0.91`

---

### T46 `openfoam_generate_modeling_plan`（成功）
输入:
```json
{"description":"2D lid-driven cavity, incompressible transient at high Reynolds number with moving lid"}
```
关键输出:
- 状态 `ready`
- 模板 `cavity_flow`
- 参数自动补全可用

---

### T47 `openfoam_run_workflow_from_prompt`（失败：字段名错误）
输入:
```json
{"prompt":"Create a lid-driven cavity..."}
```
关键输出:
```text
validation error: params.description Field required
```

### T48 `openfoam_run_workflow_from_prompt`（成功）
输入:
```json
{"description":"Create a lid-driven cavity case with width 0.05m, height 0.05m, lid velocity 0.2m/s, water, end time 0.05, and case_path /tmp/openfoam_remote_test/workflow_cavity"}
```
关键输出:
- 状态 `completed`
- 模板 `cavity_flow`
- 远端 Job ID: `381bec96eb664cd7843a3842aecde433`
- 访问链接: `https://openfoam-mcp-oag6gghvmq-uc.a.run.app/portal/381bec96eb664cd7843a3842aecde433`
- 求解阶段状态: `skipped`

---

## 3. 产物文件

- `cavity_case`: `/tmp/openfoam_remote_test/cavity_case`
- `pipe_case`: `/tmp/openfoam_remote_test/pipe_case`
- 残差图: `/tmp/openfoam_remote_test/pipe_case/residuals.png`

---

## 4. 建议修复项（按优先级）

1. `update_dictionary` 做“精确路径更新”而不是同名键全局替换（避免 `functions.*.writeInterval` 被误改）
2. `cavity_flow` 模板与 `icoFoam` 的 functionObjects 兼容性检查（默认不要启用依赖湍流模型的 `yPlus`，或自动切换）
3. `run_parallel` 前预检并提示 `metis` 依赖缺失，减少失败后回退的额外开销

