#!/usr/bin/env python3
"""
OpenFOAM MCP Server - Natural language driven CFD case generation.

This MCP server enables AI assistants to help users create, configure,
and run OpenFOAM CFD simulations through natural language interaction.
"""

import asyncio
import time

from mcp.server.fastmcp import FastMCP

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    # Support direct execution: `python src/server.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.tools import (
        # Input models
        ListTemplatesInput,
        GetTemplateInfoInput,
        CreateCaseInput,
        ValidateCaseInput,
        RunSolverInput,
        AnalyzeProblemInput,
        GenerateMeshInput,
        GenerateBoundaryConditionsInput,
        GetRunStatusInput,
        RunParallelInput,
        GenerateResidualPlotInput,
        CalculateYplusInput,
        SearchTutorialsInput,
        ReadTutorialFileInput,
        GetPatchListInput,
        ReadDictionaryInput,
        UpdateDictionaryInput,
        PreflightCheckInput,
        AssessCaseStabilityInput,
        ApplyStabilityFixesInput,
        GenerateModelingPlanInput,
        RunWorkflowFromPromptInput,
        # Tool functions
        openfoam_list_templates,
        openfoam_get_template_info,
        openfoam_create_case,
        openfoam_validate_case,
        openfoam_run_solver,
        openfoam_analyze_problem,
        openfoam_get_fluid_properties,
        openfoam_generate_mesh,
        openfoam_generate_boundary_conditions,
        openfoam_get_run_status,
        openfoam_run_parallel,
        openfoam_generate_residual_plot,
        openfoam_calculate_yplus,
        openfoam_search_tutorials,
        openfoam_read_tutorial_file,
        openfoam_get_patch_list,
        openfoam_read_dictionary,
        openfoam_update_dictionary,
        openfoam_preflight_check,
        openfoam_assess_case_stability,
        openfoam_apply_stability_fixes,
        openfoam_generate_modeling_plan,
        openfoam_run_workflow_from_prompt,
    )
    from src.web import cleanup_old_job_dirs, load_server_config, register_custom_routes
else:
    from .tools import (
        # Input models
        ListTemplatesInput,
        GetTemplateInfoInput,
        CreateCaseInput,
        ValidateCaseInput,
        RunSolverInput,
        AnalyzeProblemInput,
        GenerateMeshInput,
        GenerateBoundaryConditionsInput,
        GetRunStatusInput,
        RunParallelInput,
        GenerateResidualPlotInput,
        CalculateYplusInput,
        SearchTutorialsInput,
        ReadTutorialFileInput,
        GetPatchListInput,
        ReadDictionaryInput,
        UpdateDictionaryInput,
        PreflightCheckInput,
        AssessCaseStabilityInput,
        ApplyStabilityFixesInput,
        GenerateModelingPlanInput,
        RunWorkflowFromPromptInput,
        # Tool functions
        openfoam_list_templates,
        openfoam_get_template_info,
        openfoam_create_case,
        openfoam_validate_case,
        openfoam_run_solver,
        openfoam_analyze_problem,
        openfoam_get_fluid_properties,
        openfoam_generate_mesh,
        openfoam_generate_boundary_conditions,
        openfoam_get_run_status,
        openfoam_run_parallel,
        openfoam_generate_residual_plot,
        openfoam_calculate_yplus,
        openfoam_search_tutorials,
        openfoam_read_tutorial_file,
        openfoam_get_patch_list,
        openfoam_read_dictionary,
        openfoam_update_dictionary,
        openfoam_preflight_check,
        openfoam_assess_case_stability,
        openfoam_apply_stability_fixes,
        openfoam_generate_modeling_plan,
        openfoam_run_workflow_from_prompt,
    )
    from .web import cleanup_old_job_dirs, load_server_config, register_custom_routes

_SERVER_CONFIG = load_server_config()

# Initialize the MCP server
mcp = FastMCP(
    "openfoam_mcp",
    host=_SERVER_CONFIG.host,
    port=_SERVER_CONFIG.port,
    sse_path=_SERVER_CONFIG.sse_path,
    streamable_http_path=_SERVER_CONFIG.streamable_http_path,
)
register_custom_routes(mcp)


_TOOL_CLEANUP_INTERVAL_SECONDS = 300.0
_last_tool_cleanup_at = 0.0


async def _run_tool(func, *args) -> str:
    """Execute tool in a worker thread with periodic artifact cleanup and safe errors."""
    global _last_tool_cleanup_at

    now = time.monotonic()
    if now - _last_tool_cleanup_at >= _TOOL_CLEANUP_INTERVAL_SECONDS:
        try:
            await asyncio.to_thread(
                cleanup_old_job_dirs,
                ttl_seconds=_SERVER_CONFIG.artifact_ttl_seconds,
                max_jobs=_SERVER_CONFIG.artifact_max_jobs,
            )
        except Exception:
            # Cleanup failures should not block tool serving.
            pass
        _last_tool_cleanup_at = now

    try:
        return await asyncio.to_thread(func, *args)
    except Exception as exc:
        return f"错误: {exc}"


# ============================================================================
# Tool Registrations
# ============================================================================

@mcp.tool(
    name="openfoam_list_templates",
    description="列出可用模板，可按 category 过滤并选择 markdown/json 输出格式。",
    annotations={
        "title": "列出 OpenFOAM 模板",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def list_templates_tool(params: ListTemplatesInput) -> str:
    return await _run_tool(openfoam_list_templates, params)


@mcp.tool(
    name="openfoam_get_template_info",
    description="获取指定模板的参数、默认值与生成文件说明。",
    annotations={
        "title": "获取模板详情",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def get_template_info_tool(params: GetTemplateInfoInput) -> str:
    return await _run_tool(openfoam_get_template_info, params)


@mcp.tool(
    name="openfoam_create_case",
    description="按模板与参数在绝对路径创建案例目录，并生成基础 OpenFOAM 文件。",
    annotations={
        "title": "创建 OpenFOAM 案例",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def create_case_tool(params: CreateCaseInput) -> str:
    return await _run_tool(openfoam_create_case, params)


@mcp.tool(
    name="openfoam_validate_case",
    description="检查案例目录结构与关键字典文件是否满足求解前要求。",
    annotations={
        "title": "验证 OpenFOAM 案例",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def validate_case_tool(params: ValidateCaseInput) -> str:
    return await _run_tool(openfoam_validate_case, params)


@mcp.tool(
    name="openfoam_run_solver",
    description="在案例目录串行运行求解器，可自动从 controlDict 推断 solver。",
    annotations={
        "title": "运行 OpenFOAM 求解器",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def run_solver_tool(params: RunSolverInput) -> str:
    return await _run_tool(openfoam_run_solver, params)


@mcp.tool(
    name="openfoam_analyze_problem",
    description="将自然语言 CFD 问题解析为推荐模板、边界条件和求解设置。",
    annotations={
        "title": "分析 CFD 问题",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def analyze_problem_tool(params: AnalyzeProblemInput) -> str:
    return await _run_tool(openfoam_analyze_problem, params)


@mcp.tool(
    name="openfoam_get_fluid_properties",
    description="返回常见流体物性参数，用于初始工况估算和量纲分析。",
    annotations={
        "title": "获取流体物性",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def get_fluid_properties_tool(fluid_name: str) -> str:
    name_map = {"水": "water", "空气": "air", "油": "oil"}
    fluid = name_map.get(fluid_name, fluid_name.lower())
    return await _run_tool(openfoam_get_fluid_properties, fluid)


@mcp.tool(
    name="openfoam_generate_mesh",
    description="生成或更新 blockMesh/snappyHexMesh 等网格配置文件。",
    annotations={
        "title": "生成网格配置",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def generate_mesh_tool(params: GenerateMeshInput) -> str:
    return await _run_tool(openfoam_generate_mesh, params)


@mcp.tool(
    name="openfoam_generate_boundary_conditions",
    description="按输入工况生成 0/ 目录边界条件字典文件。",
    annotations={
        "title": "生成边界条件",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def generate_boundary_conditions_tool(params: GenerateBoundaryConditionsInput) -> str:
    return await _run_tool(openfoam_generate_boundary_conditions, params)


@mcp.tool(
    name="openfoam_get_run_status",
    description="读取日志和案例状态，汇总求解进度、错误与关键指标。",
    annotations={
        "title": "获取运行状态",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def get_run_status_tool(params: GetRunStatusInput) -> str:
    return await _run_tool(openfoam_get_run_status, params)


@mcp.tool(
    name="openfoam_run_parallel",
    description="使用 decomposePar 与并行求解命令执行案例并记录运行日志。",
    annotations={
        "title": "并行运行求解器",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def run_parallel_tool(params: RunParallelInput) -> str:
    return await _run_tool(openfoam_run_parallel, params)


@mcp.tool(
    name="openfoam_generate_residual_plot",
    description="从求解日志提取残差并生成曲线图与统计摘要。",
    annotations={
        "title": "生成残差曲线图",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def generate_residual_plot_tool(params: GenerateResidualPlotInput) -> str:
    return await _run_tool(openfoam_generate_residual_plot, params)


@mcp.tool(
    name="openfoam_calculate_yplus",
    description="根据速度、粘度与目标 y+ 估算第一层网格高度。",
    annotations={
        "title": "计算 y+ 和第一层网格高度",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def calculate_yplus_tool(params: CalculateYplusInput) -> str:
    return await _run_tool(openfoam_calculate_yplus, params)


@mcp.tool(
    name="openfoam_search_tutorials",
    description="在 OpenFOAM 官方教程库按关键词搜索匹配案例。",
    annotations={
        "title": "搜索 OpenFOAM 官方教程",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def search_tutorials_tool(params: SearchTutorialsInput) -> str:
    return await _run_tool(openfoam_search_tutorials, params)


@mcp.tool(
    name="openfoam_read_tutorial_file",
    description="安全读取官方教程中的指定文件内容。",
    annotations={
        "title": "读取官方教程文件",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def read_tutorial_file_tool(params: ReadTutorialFileInput) -> str:
    return await _run_tool(openfoam_read_tutorial_file, params)


@mcp.tool(
    name="openfoam_get_patch_list",
    description="解析 boundary 文件并返回 patch 名称、类型与数量统计。",
    annotations={
        "title": "获取边界列表",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def get_patch_list_tool(params: GetPatchListInput) -> str:
    return await _run_tool(openfoam_get_patch_list, params)


@mcp.tool(
    name="openfoam_read_dictionary",
    description="读取指定 OpenFOAM 字典文件并返回文本内容。",
    annotations={
        "title": "读取 OpenFOAM 字典",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def read_dictionary_tool(params: ReadDictionaryInput) -> str:
    return await _run_tool(openfoam_read_dictionary, params)


@mcp.tool(
    name="openfoam_update_dictionary",
    description="对指定字典执行键值更新并将变更写回磁盘。",
    annotations={
        "title": "更新 OpenFOAM 字典",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def update_dictionary_tool(params: UpdateDictionaryInput) -> str:
    return await _run_tool(openfoam_update_dictionary, params)


@mcp.tool(
    name="openfoam_preflight_check",
    description="执行环境与案例预检，并输出 ready/degraded/blocked 结论。",
    annotations={
        "title": "OpenFOAM 运行前检查",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def preflight_check_tool(params: PreflightCheckInput) -> str:
    return await _run_tool(openfoam_preflight_check, params)


@mcp.tool(
    name="openfoam_assess_case_stability",
    description="基于数值设置评估稳定性风险并给出告警项。",
    annotations={
        "title": "评估案例数值稳定性",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def assess_case_stability_tool(params: AssessCaseStabilityInput) -> str:
    return await _run_tool(openfoam_assess_case_stability, params)


@mcp.tool(
    name="openfoam_apply_stability_fixes",
    description="自动应用保守稳定性修复建议到关键求解配置文件。",
    annotations={
        "title": "自动修复稳定性设置",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def apply_stability_fixes_tool(params: ApplyStabilityFixesInput) -> str:
    return await _run_tool(openfoam_apply_stability_fixes, params)


@mcp.tool(
    name="openfoam_generate_modeling_plan",
    description="从自然语言需求生成可执行建模步骤、参数补全和检查清单。",
    annotations={
        "title": "自然语言建模计划",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def generate_modeling_plan_tool(params: GenerateModelingPlanInput) -> str:
    return await _run_tool(openfoam_generate_modeling_plan, params)


@mcp.tool(
    name="openfoam_run_workflow_from_prompt",
    description="从自然语言一键执行建模规划、建案、预检与求解全流程。",
    annotations={
        "title": "自然语言端到端建模",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def run_workflow_from_prompt_tool(params: RunWorkflowFromPromptInput) -> str:
    return await _run_tool(openfoam_run_workflow_from_prompt, params)


def main():
    """Run the MCP server."""
    _SERVER_CONFIG.artifact_dir.mkdir(parents=True, exist_ok=True)
    cleanup_old_job_dirs(
        ttl_seconds=_SERVER_CONFIG.artifact_ttl_seconds,
        max_jobs=_SERVER_CONFIG.artifact_max_jobs,
    )
    mcp.run(transport=_SERVER_CONFIG.transport)


if __name__ == "__main__":
    main()
