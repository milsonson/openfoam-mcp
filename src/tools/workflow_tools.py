"""Workflow tools for natural-language-to-case orchestration."""

from __future__ import annotations

from enum import Enum
from datetime import datetime, timezone
from pathlib import Path
import json
import logging
import re
import shutil
import uuid
from typing import Any, Dict, List, Optional, TypedDict
from typing_extensions import NotRequired

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
from ..web.artifacts import (
    append_job_event,
    build_artifact_url,
    create_job_artifact_dir,
    create_job_bundle,
    list_job_artifacts,
    write_job_manifest,
)
from ..web.config import load_server_config
from ..web.events import publish_job_event
from .case_tools import (
    AnalyzeProblemInput,
    GenerateResidualPlotInput,
    ResponseFormat as CaseResponseFormat,
    openfoam_analyze_problem,
    openfoam_generate_residual_plot,
)
from .path_utils import (
    get_allowed_case_roots,
    resolve_openfoam_command,
    validate_allowed_case_path,
)
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


logger = logging.getLogger(__name__)
WORKFLOW_RESPONSE_CHAR_LIMIT = 25000
WORKFLOW_LONG_STRING_TRIM = 3000
WORKFLOW_MAX_LIST_ITEMS = 100


def _contains_missing_metis_error(*messages: Optional[str]) -> bool:
    """Detect decomposePar failures caused by missing metis decomposition library."""
    haystack = "\n".join(msg for msg in messages if msg).lower()
    return "libmetisdecomp.so" in haystack or ("metis" in haystack and "decompose" in haystack)


class KPISummary(TypedDict):
    """Key metrics extracted from workflow execution."""

    converged: bool | None
    final_residuals: dict[str, Any]
    final_residual_fields: list[str]
    duration_seconds: float | None


class QualityReport(TypedDict):
    """Consolidated quality checks for portal rendering."""

    preflight_overall: Any
    preflight_errors: Any
    preflight_warnings: Any
    validation_passed: Any
    validation_has_warnings: Any
    stability_high_risk_before: Any
    stability_high_risk_after: Any


class WorkflowEvent(TypedDict):
    """Serializable workflow stage event."""

    job_id: str
    stage: str
    status: str
    message: str
    timestamp: str
    progress: NotRequired[int]
    data: NotRequired[dict[str, Any]]


class WorkflowManifest(TypedDict, total=False):
    """Stored manifest state for a workflow job."""

    contract_version: str
    job_id: str
    portal_url: str
    delivery_url: str
    status: str
    stage: str
    progress: int
    case_path: str
    created_at: str
    updated_at: str


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
    case_path: Optional[str] = Field(
        default=None,
        description="可选案例输出目录绝对路径。不填时服务端自动分配目录。",
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
    def validate_case_path(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        return validate_allowed_case_path(value)


def _to_meters(raw_value: float, raw_unit: str) -> float:
    unit = raw_unit.lower()
    if unit in {"mm", "毫米"}:
        return raw_value * 0.001
    if unit in {"cm", "厘米"}:
        return raw_value * 0.01
    if unit in {"m", "米"}:
        return raw_value
    raise ValueError(f"不支持的长度单位: {raw_unit}")


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
    try:
        return _to_meters(value, unit)
    except ValueError:
        return None


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


def _truncate_text_response(text: str, limit: int = WORKFLOW_RESPONSE_CHAR_LIMIT) -> str:
    """Truncate markdown/plain responses to fit MCP output limits."""
    if len(text) <= limit:
        return text
    cutoff = text.rfind("\n", 0, limit - 100)
    if cutoff == -1:
        cutoff = limit - 100
    return (
        text[:cutoff]
        + f"\n\n...(输出已截断，共 {len(text)} 字符，显示前 {cutoff} 字符)"
    )


def _trim_long_strings(value: Any, max_chars: int = WORKFLOW_LONG_STRING_TRIM) -> Any:
    """Recursively trim oversized strings in JSON payloads."""
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value
        return (
            value[:max_chars]
            + f"...(字段已截断，原始长度 {len(value)} 字符)"
        )
    if isinstance(value, list):
        return [_trim_long_strings(item, max_chars=max_chars) for item in value]
    if isinstance(value, dict):
        return {k: _trim_long_strings(v, max_chars=max_chars) for k, v in value.items()}
    return value


def _drop_nested_key(payload: Dict[str, Any], path: tuple[str, ...]) -> None:
    """Remove nested key path if present."""
    node: Any = payload
    for part in path[:-1]:
        if not isinstance(node, dict):
            return
        node = node.get(part)
    if isinstance(node, dict):
        node.pop(path[-1], None)


def _limit_list_size(payload: Dict[str, Any], key: str, max_items: int = WORKFLOW_MAX_LIST_ITEMS) -> None:
    """Trim list field size and annotate truncated item count."""
    value = payload.get(key)
    if not isinstance(value, list) or len(value) <= max_items:
        return
    payload[key] = value[:max_items]
    payload[f"{key}_truncated_count"] = len(value) - max_items


def _serialize_json_response(payload: Dict[str, Any], limit: int = WORKFLOW_RESPONSE_CHAR_LIMIT) -> str:
    """Serialize JSON response while keeping payload within size limits."""
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(encoded) <= limit:
        return encoded

    candidate = _trim_long_strings(payload)
    if not isinstance(candidate, dict):
        candidate = dict(payload)
    candidate["response_truncated"] = True
    candidate["response_truncation_reason"] = "payload_exceeds_character_limit"

    encoded = json.dumps(candidate, ensure_ascii=False, indent=2)
    if len(encoded) <= limit:
        return encoded

    for path in [
        ("preflight", "report"),
        ("preflight", "details"),
        ("validation", "report"),
        ("validation", "errors"),
        ("validation", "warnings"),
        ("stability", "before", "report"),
        ("stability", "after", "report"),
        ("stability", "fix", "report"),
        ("plan", "raw_analysis"),
    ]:
        _drop_nested_key(candidate, path)

    _limit_list_size(candidate, "artifacts")
    _limit_list_size(candidate, "warnings")
    _limit_list_size(candidate, "failures")

    encoded = json.dumps(candidate, ensure_ascii=False, indent=2)
    if len(encoded) <= limit:
        return encoded

    minimal = {
        "contract_version": candidate.get("contract_version"),
        "job_id": candidate.get("job_id"),
        "status": candidate.get("status"),
        "stage": candidate.get("stage"),
        "template_id": candidate.get("template_id"),
        "template_name": candidate.get("template_name"),
        "solver": candidate.get("solver"),
        "portal_url": candidate.get("portal_url"),
        "delivery_url": candidate.get("delivery_url"),
        "manifest_url": candidate.get("manifest_url"),
        "warnings": candidate.get("warnings", []),
        "failures": candidate.get("failures", []),
        "response_truncated": True,
        "response_truncation_reason": "payload_reduced_to_minimal_view",
        "message": "响应过大，已返回精简结果。请通过 portal_url 或 manifest_url 查看完整数据。",
    }
    return json.dumps(minimal, ensure_ascii=False, indent=2)


def _extract_plot_path_from_output(text: str) -> Optional[Path]:
    """Extract residual plot file path from markdown tool output."""
    if not text:
        return None
    match = re.search(r"\*\*输出文件\*\*:\s*(.+)", text)
    if not match:
        return None
    plot_path = Path(match.group(1).strip())
    return plot_path if plot_path.exists() and plot_path.is_file() else None


def _build_kpi_summary(solver_run: Dict[str, Any]) -> KPISummary:
    """Build user-facing KPI summary from solver execution payload."""
    summary: KPISummary = {
        "converged": None,
        "final_residuals": {},
        "final_residual_fields": [],
        "duration_seconds": None,
    }

    solve_payload = solver_run.get("solve") if isinstance(solver_run.get("solve"), dict) else solver_run
    if not isinstance(solve_payload, dict):
        return summary

    final_residuals = solve_payload.get("final_residuals")
    if isinstance(final_residuals, dict):
        cleaned = {str(k): v for k, v in final_residuals.items()}
        summary["final_residuals"] = cleaned
        summary["final_residual_fields"] = sorted(cleaned.keys())

    converged = solve_payload.get("converged")
    if isinstance(converged, bool):
        summary["converged"] = converged

    duration_seconds = solve_payload.get("duration_seconds")
    if isinstance(duration_seconds, (int, float)):
        summary["duration_seconds"] = float(duration_seconds)

    return summary


def _build_quality_report(
    preflight: Dict[str, Any],
    validation: Dict[str, Any],
    stability: Dict[str, Any],
) -> QualityReport:
    """Build quality-focused report for portal display."""
    before = stability.get("before") if isinstance(stability.get("before"), dict) else {}
    after = stability.get("after") if isinstance(stability.get("after"), dict) else {}
    return {
        "preflight_overall": preflight.get("overall"),
        "preflight_errors": preflight.get("errors"),
        "preflight_warnings": preflight.get("warnings"),
        "validation_passed": validation.get("passed"),
        "validation_has_warnings": validation.get("has_warnings"),
        "stability_high_risk_before": before.get("high_risk"),
        "stability_high_risk_after": after.get("high_risk"),
    }


def _write_json(path: Path, payload: Dict[str, Any] | KPISummary | QualityReport) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_workflow_case_path(case_path: Optional[str], job_id: str) -> str:
    """Return validated case path, auto-allocating one when user does not provide it."""
    if case_path is not None and case_path.strip():
        return validate_allowed_case_path(case_path)

    allowed_roots = get_allowed_case_roots()
    preferred_root = next(
        (root for root in allowed_roots if root.as_posix().startswith("/tmp")),
        allowed_roots[0],
    )
    auto_case_path = preferred_root / "openfoam-mcp" / "cases" / job_id
    return validate_allowed_case_path(str(auto_case_path))


def _build_and_collect_artifacts(
    *,
    job_id: str,
    case_path: str,
    payload: Dict[str, Any],
    kpi_summary: KPISummary,
    quality_report: QualityReport,
) -> List[Dict[str, Any]]:
    """Persist workflow artifacts and return downloadable metadata."""
    job_dir = create_job_artifact_dir(job_id)

    try:
        create_job_bundle(job_id, case_path)
    except Exception:
        payload.setdefault("warnings", []).append("artifact_bundle_failed")

    _write_json(job_dir / "kpi_summary.json", kpi_summary)
    _write_json(job_dir / "quality_report.json", quality_report)

    final_residuals = kpi_summary.get("final_residuals", {})
    if isinstance(final_residuals, dict) and final_residuals:
        lines = ["field,residual"]
        for field in sorted(final_residuals.keys()):
            lines.append(f"{field},{final_residuals[field]}")
        (job_dir / "final_residuals.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    case_dir = Path(case_path)
    log_files = sorted(
        [p for p in case_dir.glob("log.*") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for log_file in log_files[:3]:
        try:
            shutil.copy2(log_file, job_dir / log_file.name)
        except Exception:
            payload.setdefault("warnings", []).append(f"artifact_copy_failed:{log_file.name}")

    residual_output = payload.get("solver_run", {}).get("residual_plot")
    if isinstance(residual_output, str):
        plot_path = _extract_plot_path_from_output(residual_output)
        if plot_path is not None:
            try:
                shutil.copy2(plot_path, job_dir / plot_path.name)
            except Exception:
                payload.setdefault("warnings", []).append(f"artifact_copy_failed:{plot_path.name}")

    manifest = {
        "contract_version": "v1",
        "job_id": job_id,
        "status": payload.get("status"),
        "template_id": payload.get("template_id"),
        "template_name": payload.get("template_name"),
        "solver": payload.get("solver"),
        "warnings": payload.get("warnings", []),
        "failures": payload.get("failures", []),
        "kpi_summary": kpi_summary,
        "quality_report": quality_report,
    }
    write_job_manifest(job_id, manifest)
    artifacts = list_job_artifacts(job_id)
    manifest["artifacts"] = artifacts
    write_job_manifest(job_id, manifest)
    return list_job_artifacts(job_id)


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

    classification_status_raw = analysis.get("classification_status", "unknown")
    classification_status = str(classification_status_raw)
    raw_confidence = analysis.get("template_confidence")
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = 0.0

    candidate_templates = analysis.get("template_candidates") or []
    if not isinstance(candidate_templates, list):
        candidate_templates = []

    template_id = analysis.get("suggested_template")
    if not template_id:
        clarification_reasons: List[str] = []
        if classification_status == "unknown":
            clarification_reasons.append("template_unknown")
        if classification_status == "low_confidence":
            clarification_reasons.append("template_low_confidence")
        return {
            "status": "needs_input",
            "message": "无法自动识别模板，请手动指定 template_id",
            "template_id": "unknown",
            "template_name": "unknown",
            "classification_status": classification_status,
            "confidence": round(confidence, 3),
            "candidate_templates": candidate_templates[:3],
            "parameters": {},
            "auto_filled": {},
            "missing_required": [],
            "validation_errors": [],
            "needs_clarification_reasons": clarification_reasons or ["template_unknown"],
            "raw_analysis": analysis,
        }

    template = get_template(template_id)
    if template is None:
        return {
            "status": "error",
            "message": f"模板不存在: {template_id}",
            "raw_analysis": analysis,
        }

    if confidence <= 0:
        confidence = 1.0 if template_id else 0.0

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
        return _serialize_json_response(plan)

    return _truncate_text_response(_format_plan_markdown(plan))


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
    runtime_config = load_server_config()
    job_id = uuid.uuid4().hex
    portal_url = f"{runtime_config.portal_base_url}/{job_id}"
    resolved_case_path = _resolve_workflow_case_path(params.case_path, job_id)
    create_job_artifact_dir(job_id)

    manifest_state: WorkflowManifest = {
        "contract_version": "v1",
        "job_id": job_id,
        "portal_url": portal_url,
        "delivery_url": portal_url,
        "status": "running",
        "stage": "planning",
        "progress": 0,
        "case_path": resolved_case_path,
        "created_at": _utc_now_iso(),
    }
    write_job_manifest(job_id, manifest_state)

    def _emit_event(
        *,
        stage: str,
        status: str,
        message: str,
        progress: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        event: WorkflowEvent = {
            "job_id": job_id,
            "stage": stage,
            "status": status,
            "message": message,
            "timestamp": _utc_now_iso(),
        }
        if progress is not None:
            event["progress"] = int(progress)
        if data:
            event["data"] = data

        try:
            append_job_event(job_id, event)
            publish_job_event(job_id, json.dumps(event, ensure_ascii=False))
        except Exception as exc:
            logger.warning(
                "作业事件写入失败，已降级继续执行 job_id=%s stage=%s status=%s error=%s",
                job_id,
                stage,
                status,
                exc,
            )

        logger.info(
            "作业阶段更新 job_id=%s stage=%s status=%s progress=%s message=%s",
            job_id,
            stage,
            status,
            progress,
            message,
        )

        manifest_state["stage"] = stage
        manifest_state["status"] = status
        manifest_state["updated_at"] = event["timestamp"]
        if progress is not None:
            manifest_state["progress"] = int(progress)
        write_job_manifest(job_id, manifest_state)

    def _with_common_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["contract_version"] = "v1"
        payload["job_id"] = job_id
        payload["portal_url"] = portal_url
        payload["delivery_url"] = portal_url
        payload["manifest_url"] = build_artifact_url(f"{job_id}/manifest.json")
        return payload

    _emit_event(stage="planning", status="running", message="开始分析建模需求", progress=5)
    plan = _build_modeling_plan(params.description)
    if plan.get("status") == "error":
        _emit_event(
            stage="planning",
            status="failed",
            message=plan.get("message", "规划失败"),
            progress=100,
        )
        base_payload = _with_common_fields(
            {
                "status": "failed",
                "stage": "planning",
                "error": plan.get("message", "规划失败"),
                "plan": plan,
            }
        )
        base_payload["artifacts"] = list_job_artifacts(job_id)
        manifest_state.update(base_payload)
        write_job_manifest(job_id, manifest_state)
        if params.response_format == ResponseFormat.JSON:
            return _serialize_json_response(base_payload)
        return _truncate_text_response(f"错误: {plan.get('message', '规划失败')}")

    if plan.get("status") != "ready":
        _emit_event(
            stage="planning",
            status="needs_input",
            message="需要更多信息以继续建模",
            progress=100,
            data={"classification_status": plan.get("classification_status")},
        )
        base_payload = _with_common_fields(
            {
                "status": "needs_input",
                "stage": "planning",
                "plan": plan,
            }
        )
        base_payload["artifacts"] = list_job_artifacts(job_id)
        manifest_state.update(base_payload)
        write_job_manifest(job_id, manifest_state)
        if params.response_format == ResponseFormat.JSON:
            return _serialize_json_response(base_payload)
        return _truncate_text_response(_format_plan_markdown(plan))

    _emit_event(
        stage="planning",
        status="running",
        message="建模计划已确认",
        progress=15,
        data={"template_id": plan.get("template_id")},
    )

    case_path = Path(resolved_case_path)
    case_existed = case_path.exists()
    _emit_event(stage="create_case", status="running", message="开始生成案例文件", progress=20)
    try:
        if case_path.exists() and not case_path.is_dir():
            raise ValueError(f"case_path 已存在且不是目录: {resolved_case_path}")
        case_path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _emit_event(
            stage="create_case",
            status="failed",
            message=f"创建案例目录失败: {exc}",
            progress=100,
        )
        base_payload = _with_common_fields(
            {
                "status": "failed",
                "stage": "create_case",
                "case_path": resolved_case_path,
                "error": str(exc),
                "plan": plan,
            }
        )
        base_payload["artifacts"] = list_job_artifacts(job_id)
        manifest_state.update(base_payload)
        write_job_manifest(job_id, manifest_state)
        if params.response_format == ResponseFormat.JSON:
            return _serialize_json_response(base_payload)
        return _truncate_text_response(f"创建案例目录失败: {exc}")

    template_id = str(plan["template_id"])
    plan_parameters = dict(plan["parameters"])

    try:
        case_config = create_case_config_from_template(
            template_id=template_id,
            parameters=plan_parameters,
            case_path=resolved_case_path,
        )
        generator = OpenFOAMGenerator(case_config)
        created_files = generator.write_case()
    except Exception as exc:
        if not case_existed and case_path.exists():
            shutil.rmtree(case_path, ignore_errors=True)
        _emit_event(
            stage="create_case",
            status="failed",
            message=f"创建案例失败: {exc}",
            progress=100,
        )
        base_payload = _with_common_fields(
            {
                "status": "failed",
                "stage": "create_case",
                "case_path": resolved_case_path,
                "template_id": template_id,
                "error": str(exc),
                "plan": plan,
            }
        )
        base_payload["artifacts"] = list_job_artifacts(job_id)
        manifest_state.update(base_payload)
        write_job_manifest(job_id, manifest_state)
        if params.response_format == ResponseFormat.JSON:
            return _serialize_json_response(base_payload)
        return _truncate_text_response(f"创建案例失败: {exc}")

    _emit_event(
        stage="create_case",
        status="running",
        message="案例文件生成完成",
        progress=30,
        data={"created_files": len(created_files)},
    )

    preflight_profile = "diagnostic"
    if params.run_solver and params.run_parallel:
        preflight_profile = "parallel"
    elif params.run_solver:
        preflight_profile = "solver"
    elif params.run_mesh:
        preflight_profile = "mesh"

    _emit_event(
        stage="preflight",
        status="running",
        message=f"执行预检查（{preflight_profile}）",
        progress=35,
    )
    preflight_report = openfoam_preflight_check(
        PreflightCheckInput(
            case_path=resolved_case_path,
            solver=case_config.solver,
            n_processors=params.n_processors if params.run_parallel else None,
            profile=preflight_profile,
            strict=False,
            response_format=StabilityResponseFormat.JSON,
        )
    )
    preflight_summary = _summarize_preflight(preflight_report)
    _emit_event(
        stage="preflight",
        status="running",
        message="预检查完成",
        progress=40,
        data={
            "errors": preflight_summary.get("errors"),
            "warnings": preflight_summary.get("warnings"),
        },
    )

    _emit_event(stage="validation", status="running", message="执行案例验证", progress=45)
    validation = validate_case(
        case_path=resolved_case_path,
        run_openfoam=params.run_openfoam_validation,
    )
    _emit_event(stage="validation", status="running", message="案例验证完成", progress=50)

    _emit_event(stage="stability", status="running", message="评估数值稳定性", progress=55)
    stability_before_raw = openfoam_assess_case_stability(
        AssessCaseStabilityInput(
            case_path=resolved_case_path,
            response_format=StabilityResponseFormat.JSON,
        )
    )
    stability_before = _safe_json_loads(stability_before_raw)

    stability_fix = None
    stability_after = None
    if params.auto_apply_stability_fixes:
        fix_raw = openfoam_apply_stability_fixes(
            ApplyStabilityFixesInput(
                case_path=resolved_case_path,
                strategy="balanced",
                response_format=StabilityResponseFormat.JSON,
            )
        )
        stability_fix = _safe_json_loads(fix_raw)

        stability_after_raw = openfoam_assess_case_stability(
            AssessCaseStabilityInput(
                case_path=resolved_case_path,
                response_format=StabilityResponseFormat.JSON,
            )
        )
        stability_after = _safe_json_loads(stability_after_raw)
    _emit_event(stage="stability", status="running", message="稳定性检查完成", progress=60)

    mesh_result: Dict[str, Any] = {"status": "skipped", "reason": "run_mesh=false"}
    if params.run_mesh:
        _emit_event(stage="mesh", status="running", message="开始网格阶段", progress=65)
        block_mesh_cmd = resolve_openfoam_command("blockMesh")
        check_mesh_cmd = resolve_openfoam_command("checkMesh")
        if block_mesh_cmd:
            block_mesh_result = run_mesh_generation(
                resolved_case_path,
                timeout=min(params.timeout, 900.0),
            )
            mesh_result = {
                "status": block_mesh_result.status.value,
                "blockMesh": _run_result_to_dict(block_mesh_result),
            }
            if block_mesh_result.status == RunStatus.COMPLETED and check_mesh_cmd:
                check_mesh_result = run_mesh_check(
                    resolved_case_path,
                    timeout=min(params.timeout, 600.0),
                )
                mesh_result["checkMesh"] = _run_result_to_dict(check_mesh_result)
        else:
            mesh_result = {
                "status": "skipped",
                "reason": "blockMesh 不可用，请先加载 OpenFOAM 环境",
            }
        _emit_event(
            stage="mesh",
            status="running",
            message="网格阶段完成",
            progress=70,
            data={"status": mesh_result.get("status")},
        )

    solver_result: Dict[str, Any] = {"status": "skipped", "reason": "run_solver=false"}
    if params.run_solver:
        _emit_event(stage="solver", status="running", message="开始求解阶段", progress=75)
        solver_cmd = resolve_openfoam_command(case_config.solver)
        mpirun_cmd = resolve_openfoam_command("mpirun")
        decompose_cmd = resolve_openfoam_command("decomposePar")
        reconstruct_cmd = resolve_openfoam_command("reconstructPar")

        if params.run_parallel:
            if mpirun_cmd and solver_cmd and decompose_cmd:
                decompose_result = decompose_case(
                    case_path=resolved_case_path,
                    n_processors=params.n_processors,
                    method="simple",
                    timeout=min(params.timeout, 900.0),
                    force=True,
                )
                solver_result = {"decompose": _run_result_to_dict(decompose_result)}

                if decompose_result.status == RunStatus.COMPLETED:
                    parallel_result = run_parallel(
                        case_path=resolved_case_path,
                        solver=solver_cmd,
                        n_processors=params.n_processors,
                        timeout=params.timeout,
                    )
                    solver_result["status"] = parallel_result.status.value
                    solver_result["solve"] = _run_result_to_dict(parallel_result)

                    if parallel_result.status == RunStatus.COMPLETED and reconstruct_cmd:
                        reconstruct_result = reconstruct_case(
                            case_path=resolved_case_path,
                            latest_time=False,
                            timeout=min(params.timeout, 900.0),
                        )
                        solver_result["reconstruct"] = _run_result_to_dict(reconstruct_result)
                else:
                    fallback_reason = "decompose_failed"
                    if _contains_missing_metis_error(
                        decompose_result.error_message,
                        decompose_result.stderr,
                        decompose_result.stdout,
                    ):
                        fallback_reason = "missing_metis_decomposer"

                    serial_result = run_solver(
                        case_path=resolved_case_path,
                        solver=solver_cmd,
                        timeout=params.timeout,
                    )
                    solver_result["status"] = serial_result.status.value
                    solver_result["solve"] = _run_result_to_dict(serial_result)
                    solver_result["execution_mode"] = "serial_fallback"
                    solver_result["fallback_reason"] = fallback_reason
            else:
                solver_result = {
                    "status": "skipped",
                    "reason": "缺少 mpirun/decomposePar 或求解器命令",
                }
        else:
            if solver_cmd:
                result = run_solver(
                    case_path=resolved_case_path,
                    solver=solver_cmd,
                    timeout=params.timeout,
                )
                solver_result = _run_result_to_dict(result)
            else:
                solver_result = {
                    "status": "skipped",
                    "reason": f"求解器命令不可用: {case_config.solver}",
                }

        if (
            params.generate_residual_plot
            and solver_result.get("status") == RunStatus.COMPLETED.value
        ):
            solver_result["residual_plot"] = openfoam_generate_residual_plot(
                GenerateResidualPlotInput(case_path=resolved_case_path)
            )
        _emit_event(
            stage="solver",
            status="running",
            message="求解阶段完成",
            progress=85,
            data={"status": solver_result.get("status")},
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
    if params.run_solver and params.run_parallel and solver_result.get("execution_mode") == "serial_fallback":
        warning_reasons.append("parallel_fallback_to_serial")

    if failure_reasons:
        status = "partial_failed"
    elif warning_reasons:
        status = "completed_with_warnings"

    payload: Dict[str, Any] = {
        "status": status,
        "stage": "completed",
        "case_path": resolved_case_path,
        "template_id": template_id,
        "template_name": plan.get("template_name"),
        "classification_status": plan.get("classification_status"),
        "confidence": plan.get("confidence"),
        "candidate_templates": plan.get("candidate_templates", []),
        "solver": case_config.solver,
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

    _emit_event(
        stage="postprocess",
        status="running",
        message="整理交付产物",
        progress=90,
    )

    kpi_summary = _build_kpi_summary(payload["solver_run"])
    quality_report = _build_quality_report(
        preflight=payload["preflight"],
        validation=payload["validation"],
        stability=payload["stability"],
    )
    artifacts = _build_and_collect_artifacts(
        job_id=job_id,
        case_path=resolved_case_path,
        payload=payload,
        kpi_summary=kpi_summary,
        quality_report=quality_report,
    )

    payload = _with_common_fields(payload)
    payload["artifacts"] = artifacts
    payload["kpi_summary"] = kpi_summary
    payload["quality_report"] = quality_report

    manifest_state.update(payload)
    write_job_manifest(job_id, manifest_state)
    _emit_event(
        stage="completed",
        status=status,
        message="工作流执行完成",
        progress=100,
        data={"artifacts": len(artifacts)},
    )
    payload["artifacts"] = list_job_artifacts(job_id)
    manifest_state["artifacts"] = payload["artifacts"]
    write_job_manifest(job_id, manifest_state)

    if params.response_format == ResponseFormat.JSON:
        return _serialize_json_response(payload)

    lines = [
        "# 自然语言工作流执行结果",
        "",
        f"- 状态: `{payload['status']}`",
        f"- 模板: `{payload['template_id']}` ({payload['template_name']})",
        f"- 求解器: `{payload['solver']}`",
        f"- Job ID: `{payload['job_id']}`",
        f"- 访问链接: {payload['delivery_url']}",
        f"- 生成文件数: {payload['created_files']}",
        f"- 交付产物数: {len(payload['artifacts'])}",
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

    return _truncate_text_response("\n".join(lines))
