"""Workflow tools for natural-language-to-case orchestration."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
import json
import re
import shutil
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core import (
    OpenFOAMGenerator,
    RunResult,
    RunStatus,
    create_case_config_from_template,
    decompose_case,
    reconstruct_case,
    run_mesh_check,
    run_mesh_generation,
    run_parallel,
    run_solver,
    validate_case,
)
from ..templates import get_template
from .case_tools import (
    AnalyzeProblemInput,
    GenerateResidualPlotInput,
    ResponseFormat as CaseResponseFormat,
    openfoam_analyze_problem,
    openfoam_generate_residual_plot,
)
from .path_utils import resolve_openfoam_command, validate_allowed_case_path
from .stability_tools import (
    ApplyStabilityFixesInput,
    AssessCaseStabilityInput,
    PreflightCheckInput,
    ResponseFormat as StabilityResponseFormat,
    openfoam_apply_stability_fixes,
    openfoam_assess_case_stability,
    openfoam_preflight_check,
)


class ResponseFormat(str, Enum):
    """Output format for workflow tools."""

    MARKDOWN = "markdown"
    JSON = "json"


class GenerateModelingPlanInput(BaseModel):
    """Input for NL-driven modeling plan generation."""

    model_config = ConfigDict(str_strip_whitespace=True)

    description: str = Field(
        ...,
        min_length=10,
        max_length=4000,
        description="自然语言建模需求描述",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="输出格式: markdown 或 json",
    )


class RunWorkflowFromPromptInput(BaseModel):
    """Input for end-to-end NL workflow execution."""

    model_config = ConfigDict(str_strip_whitespace=True)

    description: str = Field(
        ...,
        min_length=10,
        max_length=4000,
        description="自然语言建模需求描述",
    )
    case_path: str = Field(
        ...,
        min_length=1,
        description="案例输出目录绝对路径",
    )
    run_mesh: bool = Field(
        default=True,
        description="是否执行 blockMesh/checkMesh",
    )
    run_solver: bool = Field(
        default=False,
        description="是否继续执行求解器",
    )
    run_parallel: bool = Field(
        default=False,
        description="求解器阶段是否并行运行（需 run_solver=true）",
    )
    n_processors: int = Field(
        default=4,
        ge=2,
        le=256,
        description="并行核数（run_parallel=true 时生效）",
    )
    timeout: float = Field(
        default=3600,
        ge=10,
        le=172800,
        description="网格/求解最大运行时间（秒）",
    )
    auto_apply_stability_fixes: bool = Field(
        default=True,
        description="是否自动应用稳定性修复",
    )
    run_openfoam_validation: bool = Field(
        default=False,
        description="是否在验证阶段运行 OpenFOAM 命令",
    )
    generate_residual_plot: bool = Field(
        default=True,
        description="求解完成后是否尝试生成残差图",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="输出格式: markdown 或 json",
    )

    @field_validator("case_path")
    @classmethod
    def validate_case_path(cls, value: str) -> str:
        return validate_allowed_case_path(value)


def _to_meters(raw_value: float, raw_unit: str) -> float:
    unit = raw_unit.lower()
    if unit in {"mm", "毫米"}:
        return raw_value * 0.001
    if unit in {"cm", "厘米"}:
        return raw_value * 0.01
    return raw_value


def _extract_first_float(desc_lower: str, patterns: List[str]) -> Optional[float]:
    for pattern in patterns:
        match = re.search(pattern, desc_lower)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def _extract_named_length(desc_lower: str, keyword: str) -> Optional[float]:
    pattern = rf"(?:{keyword})\s*([\d.]+)\s*(mm|毫米|cm|厘米|m|米)?"
    match = re.search(pattern, desc_lower)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    unit = (match.group(2) or "m").strip()
    return _to_meters(value, unit)


def _extract_temperature(desc_lower: str, keyword: str) -> Optional[float]:
    pattern = rf"(?:{keyword})\s*([\d.]+)\s*(k|℃|°c|c)?"
    match = re.search(pattern, desc_lower)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None

    unit = (match.group(2) or "K").lower()
    if unit in {"℃", "°c", "c"}:
        return value + 273.15
    return value


def _extract_additional_params(description: str, template_id: str) -> Dict[str, Any]:
    """Extract template-oriented parameters from NL text.

    This complements openfoam_analyze_problem for templates with custom keys.
    """
    desc = description.lower()
    extra: Dict[str, Any] = {}

    if template_id in {"cavity_flow", "natural_convection"}:
        width = _extract_named_length(desc, "宽|width")
        height = _extract_named_length(desc, "高|height")
        if width is not None:
            extra["width"] = width
        if height is not None:
            extra["height"] = height
        if width is not None and "height" not in extra:
            extra["height"] = width

    if template_id == "cavity_flow":
        lid_velocity = _extract_first_float(
            desc,
            [r"(?:lid|顶盖)(?:速度|velocity)?\s*([\d.]+)", r"顶盖.*?([\d.]+)\s*(?:m/s|米每秒)"],
        )
        if lid_velocity is not None:
            extra["lid_velocity"] = lid_velocity

    if template_id == "natural_convection":
        hot_wall_temp = _extract_temperature(desc, "热壁|hot")
        cold_wall_temp = _extract_temperature(desc, "冷壁|cold")
        if hot_wall_temp is not None:
            extra["hot_wall_temp"] = hot_wall_temp
        if cold_wall_temp is not None:
            extra["cold_wall_temp"] = cold_wall_temp

    return extra


def _normalize_parameter_aliases(template_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize generic extracted keys to template-specific names."""
    normalized = dict(parameters)

    if template_id == "cylinder_flow" and "diameter" in normalized and "cylinder_diameter" not in normalized:
        normalized["cylinder_diameter"] = normalized["diameter"]

    if template_id in {"cavity_flow", "natural_convection"}:
        if "diameter" in normalized and "width" not in normalized:
            normalized["width"] = normalized["diameter"]
        if "width" in normalized and "height" not in normalized:
            normalized["height"] = normalized["width"]

    if template_id == "mixing_elbow" and "diameter" in normalized and "pipe_diameter" not in normalized:
        normalized["pipe_diameter"] = normalized["diameter"]

    return normalized


def _coerce_param_value(param_type: str, value: Any) -> Any:
    """Coerce extracted value to template parameter type when possible."""
    if param_type == "float":
        return float(value)
    if param_type == "int":
        return int(float(value))
    if param_type in {"string", "choice"}:
        return str(value)
    return value


def _safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _build_modeling_plan(description: str) -> Dict[str, Any]:
    """Build a structured modeling plan from natural language description."""
    analysis_text = openfoam_analyze_problem(
        AnalyzeProblemInput(description=description, response_format=CaseResponseFormat.JSON)
    )
    analysis = _safe_json_loads(analysis_text)
    if not analysis:
        return {
            "status": "error",
            "message": "无法解析问题分析结果",
            "raw_analysis": analysis_text,
        }

    template_id = analysis.get("suggested_template")
    if not template_id:
        return {
            "status": "needs_input",
            "message": "无法自动识别模板，请手动指定 template_id",
            "raw_analysis": analysis,
        }

    template = get_template(template_id)
    if template is None:
        return {
            "status": "error",
            "message": f"模板不存在: {template_id}",
            "raw_analysis": analysis,
        }

    classification_status_raw = analysis.get("classification_status", "ok")
    classification_status = str(classification_status_raw)
    raw_confidence = analysis.get("template_confidence")
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = 1.0 if template_id else 0.0

    candidate_templates = analysis.get("template_candidates") or []
    if not isinstance(candidate_templates, list):
        candidate_templates = []

    merged_parameters: Dict[str, Any] = {}
    geometry_params = analysis.get("geometry_params") or {}
    flow_params = analysis.get("flow_params") or {}

    if isinstance(geometry_params, dict):
        merged_parameters.update(geometry_params)
    if isinstance(flow_params, dict):
        merged_parameters.update(flow_params)

    for k, v in _extract_additional_params(description, template.id).items():
        merged_parameters.setdefault(k, v)

    merged_parameters = _normalize_parameter_aliases(template.id, merged_parameters)

    auto_filled: Dict[str, Any] = {}
    missing_required: List[str] = []

    for parameter in template.parameters:
        if parameter.name in merged_parameters:
            try:
                merged_parameters[parameter.name] = _coerce_param_value(parameter.type, merged_parameters[parameter.name])
            except (ValueError, TypeError):
                # Keep original value for validation to surface clear message.
                pass
            continue

        if parameter.default is not None:
            merged_parameters[parameter.name] = parameter.default
            auto_filled[parameter.name] = parameter.default
        elif parameter.required:
            missing_required.append(parameter.name)

    validation_errors = template.validate_parameters(merged_parameters)
    clarification_reasons: List[str] = []

    status = "ready"
    if missing_required or validation_errors:
        status = "needs_input"
    if classification_status in {"low_confidence", "unknown"}:
        status = "needs_input"
        clarification_reasons.append("template_low_confidence")

    return {
        "status": status,
        "template_id": template.id,
        "template_name": template.name,
        "classification_status": classification_status,
        "confidence": round(confidence, 3),
        "candidate_templates": candidate_templates[:3],
        "parameters": merged_parameters,
        "auto_filled": auto_filled,
        "missing_required": missing_required,
        "validation_errors": validation_errors,
        "needs_clarification_reasons": clarification_reasons,
        "raw_analysis": analysis,
    }


def _format_plan_markdown(plan: Dict[str, Any]) -> str:
    if plan.get("status") == "error":
        return f"错误: {plan.get('message', '未知错误')}"

    lines = [
        "# 自然语言建模计划",
        "",
        f"- 状态: `{plan.get('status', 'unknown')}`",
        f"- 模板: `{plan.get('template_id', 'unknown')}` ({plan.get('template_name', 'unknown')})",
        f"- 识别状态: `{plan.get('classification_status', 'unknown')}`",
        f"- 识别置信度: {plan.get('confidence', 0.0):.2f}",
        "",
        "## 参数",
    ]

    candidate_templates = plan.get("candidate_templates", [])
    lines.append("")
    lines.append("## 候选模板")
    if candidate_templates:
        for template_id in candidate_templates:
            lines.append(f"- `{template_id}`")
    else:
        lines.append("- 无")

    parameters = plan.get("parameters", {})
    for key in sorted(parameters.keys()):
        lines.append(f"- `{key}`: {parameters[key]}")

    auto_filled = plan.get("auto_filled", {})
    lines.append("")
    lines.append("## 自动补全")
    if auto_filled:
        for key in sorted(auto_filled.keys()):
            lines.append(f"- `{key}`: {auto_filled[key]}")
    else:
        lines.append("- 无")

    missing = plan.get("missing_required", [])
    lines.append("")
    lines.append("## 缺失必填项")
    if missing:
        for key in missing:
            lines.append(f"- ❌ `{key}`")
    else:
        lines.append("- ✅ 无")

    errors = plan.get("validation_errors", [])
    lines.append("")
    lines.append("## 参数校验")
    if errors:
        for item in errors:
            lines.append(f"- ⚠️ {item}")
    else:
        lines.append("- ✅ 通过")

    clarification_reasons = plan.get("needs_clarification_reasons", [])
    lines.append("")
    lines.append("## 需要澄清")
    if clarification_reasons:
        for reason in clarification_reasons:
            lines.append(f"- ⚠️ {reason}")
    else:
        lines.append("- 无")

    return "\n".join(lines)


def openfoam_generate_modeling_plan(params: GenerateModelingPlanInput) -> str:
    """
    Generate a complete template+parameter plan directly from natural language.
    """
    plan = _build_modeling_plan(params.description)

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(plan, ensure_ascii=False, indent=2)

    return _format_plan_markdown(plan)


def _run_result_to_dict(result: RunResult) -> Dict[str, Any]:
    return {
        "status": result.status.value,
        "return_code": result.return_code,
        "duration_seconds": result.duration_seconds,
        "converged": result.converged,
        "error_message": result.error_message,
        "final_residuals": result.final_residuals,
    }


def _summarize_preflight(preflight_report: str) -> Dict[str, Any]:
    preflight_payload = _safe_json_loads(preflight_report)
    if preflight_payload:
        summary = preflight_payload.get("summary") or {}
        if not isinstance(summary, dict):
            summary = {}
        try:
            errors = int(summary.get("errors", 0))
        except (TypeError, ValueError):
            errors = 0
        try:
            warnings = int(summary.get("warnings", 0))
        except (TypeError, ValueError):
            warnings = 0
        return {
            "errors": errors,
            "warnings": warnings,
            "overall": str(summary.get("overall", "unknown")),
            "profile": str(preflight_payload.get("profile", "diagnostic")),
            "report": preflight_report,
            "details": preflight_payload,
        }

    errors = 0
    warnings = 0
    overall = "unknown"
    profile = "diagnostic"
    errors_match = re.search(r"-\s*错误:\s*(\d+)", preflight_report)
    warnings_match = re.search(r"-\s*警告:\s*(\d+)", preflight_report)
    overall_match = re.search(r"-\s*总体状态:\s*([a-zA-Z_]+)", preflight_report)
    profile_match = re.search(r"-\s*场景:\s*([a-zA-Z_]+)", preflight_report)
    if errors_match:
        errors = int(errors_match.group(1))
    if warnings_match:
        warnings = int(warnings_match.group(1))
    if overall_match:
        overall = overall_match.group(1)
    if profile_match:
        profile = profile_match.group(1)
    return {
        "errors": errors,
        "warnings": warnings,
        "overall": overall,
        "profile": profile,
        "report": preflight_report,
        "details": None,
    }


def openfoam_run_workflow_from_prompt(params: RunWorkflowFromPromptInput) -> str:
    """
    Execute end-to-end workflow from NL prompt to OpenFOAM case outputs.
    """
    plan = _build_modeling_plan(params.description)
    if plan.get("status") == "error":
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {
                    "status": "failed",
                    "stage": "planning",
                    "error": plan.get("message", "规划失败"),
                    "plan": plan,
                },
                ensure_ascii=False,
                indent=2,
            )
        return f"错误: {plan.get('message', '规划失败')}"

    if plan.get("status") != "ready":
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {
                    "status": "needs_input",
                    "stage": "planning",
                    "plan": plan,
                },
                ensure_ascii=False,
                indent=2,
            )
        return _format_plan_markdown(plan)

    case_path = Path(params.case_path)
    case_existed = case_path.exists()
    case_path.mkdir(parents=True, exist_ok=True)

    template_id = str(plan["template_id"])
    plan_parameters = dict(plan["parameters"])

    try:
        config = create_case_config_from_template(
            template_id=template_id,
            parameters=plan_parameters,
            case_path=params.case_path,
        )
        generator = OpenFOAMGenerator(config)
        created_files = generator.write_case()
    except Exception as exc:
        if not case_existed and case_path.exists():
            shutil.rmtree(case_path, ignore_errors=True)
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {
                    "status": "failed",
                    "stage": "create_case",
                    "case_path": params.case_path,
                    "template_id": template_id,
                    "error": str(exc),
                    "plan": plan,
                },
                ensure_ascii=False,
                indent=2,
            )
        return f"创建案例失败: {exc}"

    preflight_profile = "diagnostic"
    if params.run_solver and params.run_parallel:
        preflight_profile = "parallel"
    elif params.run_solver:
        preflight_profile = "solver"
    elif params.run_mesh:
        preflight_profile = "mesh"

    preflight_report = openfoam_preflight_check(
        PreflightCheckInput(
            case_path=params.case_path,
            solver=config.solver,
            n_processors=params.n_processors if params.run_parallel else None,
            profile=preflight_profile,
            strict=False,
            response_format=StabilityResponseFormat.JSON,
        )
    )
    preflight_summary = _summarize_preflight(preflight_report)

    validation = validate_case(
        case_path=params.case_path,
        run_openfoam=params.run_openfoam_validation,
    )

    stability_before_raw = openfoam_assess_case_stability(
        AssessCaseStabilityInput(
            case_path=params.case_path,
            response_format=StabilityResponseFormat.JSON,
        )
    )
    stability_before = _safe_json_loads(stability_before_raw)

    stability_fix = None
    stability_after = None
    if params.auto_apply_stability_fixes:
        fix_raw = openfoam_apply_stability_fixes(
            ApplyStabilityFixesInput(
                case_path=params.case_path,
                strategy="balanced",
                response_format=StabilityResponseFormat.JSON,
            )
        )
        stability_fix = _safe_json_loads(fix_raw)

        stability_after_raw = openfoam_assess_case_stability(
            AssessCaseStabilityInput(
                case_path=params.case_path,
                response_format=StabilityResponseFormat.JSON,
            )
        )
        stability_after = _safe_json_loads(stability_after_raw)

    mesh_result: Dict[str, Any] = {"status": "skipped", "reason": "run_mesh=false"}
    if params.run_mesh:
        block_mesh_cmd = resolve_openfoam_command("blockMesh")
        check_mesh_cmd = resolve_openfoam_command("checkMesh")
        if block_mesh_cmd:
            block_mesh_result = run_mesh_generation(params.case_path, timeout=min(params.timeout, 900.0))
            mesh_result = {
                "status": block_mesh_result.status.value,
                "blockMesh": _run_result_to_dict(block_mesh_result),
            }
            if block_mesh_result.status == RunStatus.COMPLETED and check_mesh_cmd:
                check_mesh_result = run_mesh_check(params.case_path, timeout=min(params.timeout, 600.0))
                mesh_result["checkMesh"] = _run_result_to_dict(check_mesh_result)
        else:
            mesh_result = {
                "status": "skipped",
                "reason": "blockMesh 不可用，请先加载 OpenFOAM 环境",
            }

    solver_result: Dict[str, Any] = {"status": "skipped", "reason": "run_solver=false"}
    if params.run_solver:
        solver_cmd = resolve_openfoam_command(config.solver)
        mpirun_cmd = resolve_openfoam_command("mpirun")
        decompose_cmd = resolve_openfoam_command("decomposePar")
        reconstruct_cmd = resolve_openfoam_command("reconstructPar")

        if params.run_parallel:
            if mpirun_cmd and solver_cmd and decompose_cmd:
                decompose_result = decompose_case(
                    case_path=params.case_path,
                    n_processors=params.n_processors,
                    method="scotch",
                    timeout=min(params.timeout, 900.0),
                    force=True,
                )
                solver_result = {"decompose": _run_result_to_dict(decompose_result)}

                if decompose_result.status == RunStatus.COMPLETED:
                    parallel_result = run_parallel(
                        case_path=params.case_path,
                        solver=solver_cmd,
                        n_processors=params.n_processors,
                        timeout=params.timeout,
                    )
                    solver_result["status"] = parallel_result.status.value
                    solver_result["solve"] = _run_result_to_dict(parallel_result)

                    if parallel_result.status == RunStatus.COMPLETED and reconstruct_cmd:
                        reconstruct_result = reconstruct_case(
                            case_path=params.case_path,
                            latest_time=False,
                            timeout=min(params.timeout, 900.0),
                        )
                        solver_result["reconstruct"] = _run_result_to_dict(reconstruct_result)
                else:
                    solver_result["status"] = RunStatus.FAILED.value
            else:
                solver_result = {
                    "status": "skipped",
                    "reason": "缺少 mpirun/decomposePar 或求解器命令",
                }
        else:
            if solver_cmd:
                result = run_solver(
                    case_path=params.case_path,
                    solver=solver_cmd,
                    timeout=params.timeout,
                )
                solver_result = _run_result_to_dict(result)
            else:
                solver_result = {
                    "status": "skipped",
                    "reason": f"求解器命令不可用: {config.solver}",
                }

        if (
            params.generate_residual_plot
            and solver_result.get("status") == RunStatus.COMPLETED.value
        ):
            solver_result["residual_plot"] = openfoam_generate_residual_plot(
                GenerateResidualPlotInput(case_path=params.case_path)
            )

    status = "completed"
    warning_reasons: List[str] = []
    failure_reasons: List[str] = []

    if preflight_summary["errors"] > 0:
        warning_reasons.append("preflight_errors")
    if preflight_summary["warnings"] > 0:
        warning_reasons.append("preflight_warnings")

    mesh_status = mesh_result.get("status")
    if mesh_status == RunStatus.FAILED.value:
        failure_reasons.append("mesh_failed")
    elif params.run_mesh and mesh_status in {"skipped", "cancelled"}:
        warning_reasons.append("mesh_not_executed")

    solver_status = solver_result.get("status")
    if solver_status == RunStatus.FAILED.value:
        failure_reasons.append("solver_failed")
    elif params.run_solver and solver_status in {"skipped", "cancelled"}:
        warning_reasons.append("solver_not_executed")

    if failure_reasons:
        status = "partial_failed"
    elif warning_reasons:
        status = "completed_with_warnings"

    payload: Dict[str, Any] = {
        "status": status,
        "case_path": params.case_path,
        "template_id": template_id,
        "template_name": plan.get("template_name"),
        "classification_status": plan.get("classification_status"),
        "confidence": plan.get("confidence"),
        "candidate_templates": plan.get("candidate_templates", []),
        "solver": config.solver,
        "parameters": plan_parameters,
        "auto_filled": plan.get("auto_filled", {}),
        "created_files": len(created_files),
        "preflight": preflight_summary,
        "validation": {
            "passed": validation.passed,
            "has_warnings": validation.has_warnings,
            "summary": validation.summary(),
        },
        "stability": {
            "before": stability_before,
            "fix": stability_fix,
            "after": stability_after,
        },
        "mesh": mesh_result,
        "solver_run": solver_result,
        "warnings": warning_reasons,
        "failures": failure_reasons,
    }

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    lines = [
        "# 自然语言工作流执行结果",
        "",
        f"- 状态: `{payload['status']}`",
        f"- 案例目录: `{payload['case_path']}`",
        f"- 模板: `{payload['template_id']}` ({payload['template_name']})",
        f"- 求解器: `{payload['solver']}`",
        f"- 生成文件数: {payload['created_files']}",
        "",
        "## 预检查",
        f"- 错误: {payload['preflight']['errors']}",
        f"- 警告: {payload['preflight']['warnings']}",
        "",
        "## 验证",
        f"- 通过: {'是' if payload['validation']['passed'] else '否'}",
        f"- 有警告: {'是' if payload['validation']['has_warnings'] else '否'}",
        "",
        "## 稳定性",
    ]

    before = payload["stability"]["before"] or {}
    after = payload["stability"]["after"] or {}
    lines.append(f"- 修复前高风险项: {before.get('high_risk', 'N/A')}")
    lines.append(f"- 修复后高风险项: {after.get('high_risk', 'N/A')}")

    lines.append("")
    lines.append("## 网格")
    lines.append(f"- 状态: {payload['mesh'].get('status', 'unknown')}")

    lines.append("")
    lines.append("## 求解")
    lines.append(f"- 状态: {payload['solver_run'].get('status', 'unknown')}")

    return "\n".join(lines)
