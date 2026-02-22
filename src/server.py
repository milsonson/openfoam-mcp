#!/usr/bin/env python3
"""
OpenFOAM MCP Server - Natural language driven CFD case generation.

This MCP server enables AI assistants to help users create, configure,
and run OpenFOAM CFD simulations through natural language interaction.
"""

import asyncio

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


# ============================================================================
# Tool Registrations
# ============================================================================

@mcp.tool(
    name="openfoam_list_templates",
    annotations={
        "title": "列出 OpenFOAM 模板",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def list_templates_tool(params: ListTemplatesInput) -> str:
    return await asyncio.to_thread(openfoam_list_templates, params)


@mcp.tool(
    name="openfoam_get_template_info",
    annotations={
        "title": "获取模板详情",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def get_template_info_tool(params: GetTemplateInfoInput) -> str:
    return await asyncio.to_thread(openfoam_get_template_info, params)


@mcp.tool(
    name="openfoam_create_case",
    annotations={
        "title": "创建 OpenFOAM 案例",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def create_case_tool(params: CreateCaseInput) -> str:
    return await asyncio.to_thread(openfoam_create_case, params)


@mcp.tool(
    name="openfoam_validate_case",
    annotations={
        "title": "验证 OpenFOAM 案例",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def validate_case_tool(params: ValidateCaseInput) -> str:
    return await asyncio.to_thread(openfoam_validate_case, params)


@mcp.tool(
    name="openfoam_run_solver",
    annotations={
        "title": "运行 OpenFOAM 求解器",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def run_solver_tool(params: RunSolverInput) -> str:
    return await asyncio.to_thread(openfoam_run_solver, params)


@mcp.tool(
    name="openfoam_analyze_problem",
    annotations={
        "title": "分析 CFD 问题",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def analyze_problem_tool(params: AnalyzeProblemInput) -> str:
    return await asyncio.to_thread(openfoam_analyze_problem, params)


@mcp.tool(
    name="openfoam_get_fluid_properties",
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
    return await asyncio.to_thread(openfoam_get_fluid_properties, fluid)


@mcp.tool(
    name="openfoam_generate_mesh",
    annotations={
        "title": "生成网格配置",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def generate_mesh_tool(params: GenerateMeshInput) -> str:
    return await asyncio.to_thread(openfoam_generate_mesh, params)


@mcp.tool(
    name="openfoam_generate_boundary_conditions",
    annotations={
        "title": "生成边界条件",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def generate_boundary_conditions_tool(params: GenerateBoundaryConditionsInput) -> str:
    return await asyncio.to_thread(openfoam_generate_boundary_conditions, params)


@mcp.tool(
    name="openfoam_get_run_status",
    annotations={
        "title": "获取运行状态",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def get_run_status_tool(params: GetRunStatusInput) -> str:
    return await asyncio.to_thread(openfoam_get_run_status, params)


@mcp.tool(
    name="openfoam_run_parallel",
    annotations={
        "title": "并行运行求解器",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def run_parallel_tool(params: RunParallelInput) -> str:
    return await asyncio.to_thread(openfoam_run_parallel, params)


@mcp.tool(
    name="openfoam_generate_residual_plot",
    annotations={
        "title": "生成残差曲线图",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def generate_residual_plot_tool(params: GenerateResidualPlotInput) -> str:
    return await asyncio.to_thread(openfoam_generate_residual_plot, params)


@mcp.tool(
    name="openfoam_calculate_yplus",
    annotations={
        "title": "计算 y+ 和第一层网格高度",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def calculate_yplus_tool(params: CalculateYplusInput) -> str:
    return await asyncio.to_thread(openfoam_calculate_yplus, params)


@mcp.tool(
    name="openfoam_search_tutorials",
    annotations={
        "title": "搜索 OpenFOAM 官方教程",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def search_tutorials_tool(params: SearchTutorialsInput) -> str:
    return await asyncio.to_thread(openfoam_search_tutorials, params)


@mcp.tool(
    name="openfoam_read_tutorial_file",
    annotations={
        "title": "读取官方教程文件",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def read_tutorial_file_tool(params: ReadTutorialFileInput) -> str:
    return await asyncio.to_thread(openfoam_read_tutorial_file, params)


@mcp.tool(
    name="openfoam_get_patch_list",
    annotations={
        "title": "获取边界列表",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def get_patch_list_tool(params: GetPatchListInput) -> str:
    return await asyncio.to_thread(openfoam_get_patch_list, params)


@mcp.tool(
    name="openfoam_read_dictionary",
    annotations={
        "title": "读取 OpenFOAM 字典",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def read_dictionary_tool(params: ReadDictionaryInput) -> str:
    return await asyncio.to_thread(openfoam_read_dictionary, params)


@mcp.tool(
    name="openfoam_update_dictionary",
    annotations={
        "title": "更新 OpenFOAM 字典",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def update_dictionary_tool(params: UpdateDictionaryInput) -> str:
    return await asyncio.to_thread(openfoam_update_dictionary, params)


@mcp.tool(
    name="openfoam_preflight_check",
    annotations={
        "title": "OpenFOAM 运行前检查",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def preflight_check_tool(params: PreflightCheckInput) -> str:
    return await asyncio.to_thread(openfoam_preflight_check, params)


@mcp.tool(
    name="openfoam_assess_case_stability",
    annotations={
        "title": "评估案例数值稳定性",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def assess_case_stability_tool(params: AssessCaseStabilityInput) -> str:
    return await asyncio.to_thread(openfoam_assess_case_stability, params)


@mcp.tool(
    name="openfoam_apply_stability_fixes",
    annotations={
        "title": "自动修复稳定性设置",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def apply_stability_fixes_tool(params: ApplyStabilityFixesInput) -> str:
    return await asyncio.to_thread(openfoam_apply_stability_fixes, params)


@mcp.tool(
    name="openfoam_generate_modeling_plan",
    annotations={
        "title": "自然语言建模计划",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def generate_modeling_plan_tool(params: GenerateModelingPlanInput) -> str:
    return await asyncio.to_thread(openfoam_generate_modeling_plan, params)


@mcp.tool(
    name="openfoam_run_workflow_from_prompt",
    annotations={
        "title": "自然语言端到端建模",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def run_workflow_from_prompt_tool(params: RunWorkflowFromPromptInput) -> str:
    return await asyncio.to_thread(openfoam_run_workflow_from_prompt, params)


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
