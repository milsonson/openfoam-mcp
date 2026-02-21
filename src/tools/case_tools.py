"""MCP tools for OpenFOAM case creation and management."""

from typing import Optional, Dict, Any
from enum import Enum
from pathlib import Path
import shutil
from pydantic import BaseModel, Field, ConfigDict, field_validator
import json
import re

from .path_utils import (
    atomic_write_text,
    resolve_openfoam_command,
    validate_allowed_case_path,
)
from ..templates import (
    list_templates as get_all_templates,
    get_template,
    get_template_summary,
)
from ..core import (
    create_case_config_from_template,
    OpenFOAMGenerator,
    generate_residual_plot as core_generate_residual_plot,
    parse_log_file,
    validate_case,
    run_mesh_generation,
    run_mesh_check,
    run_solver,
    decompose_case,
    reconstruct_case,
    run_parallel,
    RunStatus,
)
from ..core.validator import parse_solver_log_errors
from ..knowledge import (
    estimate_reynolds_number,
    recommend_turbulence_model,
    get_fluid_properties,
    FLUID_PROPERTIES,
    TurbulenceType,
    calculate_yplus_first_layer,
    estimate_cell_count,
    validate_mesh_quality,
)


# Character limit for responses
CHARACTER_LIMIT = 25000
_SAFE_SOLVER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_SAFE_BC_EXTRA_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_BC_EXTRA_VALUE_RE = re.compile(r"^[^\r\n{};]+$")


def _truncate(text: str, limit: int = CHARACTER_LIMIT) -> str:
    """Truncate response text to stay within MCP character limits."""
    if len(text) <= limit:
        return text
    cutoff = text.rfind('\n', 0, limit - 100)
    if cutoff == -1:
        cutoff = limit - 100
    return text[:cutoff] + f"\n\n...(输出已截断，共 {len(text)} 字符，显示前 {cutoff} 字符)"


def _validate_allowed_case_path(case_path: str) -> str:
    """Compatibility wrapper for shared case path validator."""
    return validate_allowed_case_path(case_path)


def _validate_solver_name(solver: str) -> str:
    """Validate solver token to avoid unexpected command execution."""
    name = solver.strip()
    if not _SAFE_SOLVER_NAME_RE.fullmatch(name):
        raise ValueError(f"非法求解器名称: {solver}")
    return name


def _build_openfoam_header(class_type: str, object_name: str) -> str:
    """Build OpenFOAM file header without relying on generator instance state."""
    return OpenFOAMGenerator.OPENFOAM_HEADER.format(
        class_type=class_type,
        object_name=object_name,
    )


def _validate_bc_extra_entry(key: Any, value: Any) -> tuple[str, str]:
    """Validate extra boundary keys/values to prevent config injection."""
    key_text = str(key).strip()
    if not _SAFE_BC_EXTRA_KEY_RE.fullmatch(key_text):
        raise ValueError(f"边界条件附加属性名非法: {key}")

    value_text = str(value).strip()
    if not value_text:
        raise ValueError(f"边界条件附加属性值不能为空: {key_text}")
    if not _SAFE_BC_EXTRA_VALUE_RE.fullmatch(value_text):
        raise ValueError(
            f"边界条件附加属性值包含非法字符（换行/花括号/分号）: {key_text}"
        )
    return key_text, value_text


def _normalize_boundary_value(raw_value: Any, class_type: str) -> str:
    """Normalize boundary value and validate scalar/vector dimensionality."""
    if class_type == "volVectorField":
        if isinstance(raw_value, (list, tuple)):
            if len(raw_value) != 3:
                raise ValueError("向量场边界值必须是 3 分量")
            if not all(isinstance(v, (int, float)) for v in raw_value):
                raise ValueError("向量场边界值必须是数字")
            return f"({raw_value[0]} {raw_value[1]} {raw_value[2]})"

        value_text = str(raw_value).strip()
        vector_match = re.fullmatch(
            r"\(?\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s+"
            r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s+"
            r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*\)?",
            value_text,
        )
        if not vector_match:
            raise ValueError(f"向量场边界值格式错误: {raw_value}")
        return f"({vector_match.group(1)} {vector_match.group(2)} {vector_match.group(3)})"

    # Scalar field
    if isinstance(raw_value, (list, tuple)):
        raise ValueError("标量场边界值必须是单值")
    if isinstance(raw_value, (int, float)):
        return str(raw_value)

    value_text = str(raw_value).strip()
    if value_text.startswith("(") and value_text.endswith(")"):
        raise ValueError(f"标量场边界值不能是向量形式: {raw_value}")
    return value_text


_TEMPLATE_CLASSIFICATION_RULES: Dict[str, list[tuple[str, float]]] = {
    "pipe_flow": [
        (r"管道", 2.2),
        (r"\bpipe\b", 2.2),
        (r"内流", 1.2),
    ],
    "cavity_flow": [
        (r"方腔", 2.4),
        (r"\bcavity\b", 2.4),
        (r"顶盖驱动|lid[\s-]*driven", 2.4),
    ],
    "cylinder_flow": [
        (r"圆柱绕流|绕流圆柱", 2.6),
        (r"\bcylinder\b", 2.2),
        (r"卡门涡街|vortex\s+shedding", 2.0),
    ],
    "natural_convection": [
        (r"自然对流", 2.8),
        (r"\bbuoyant\b", 2.2),
        (r"热壁|冷壁", 1.8),
        (r"温差", 1.2),
    ],
    "dam_break": [
        (r"溃坝", 3.0),
        (r"dam\s*break", 3.0),
        (r"自由液面", 1.5),
    ],
    "bubble_rising": [
        (r"气泡上升", 3.0),
        (r"bubble\s*rising", 3.0),
        (r"表面张力|气液界面", 1.6),
    ],
    "backward_step": [
        (r"后台阶", 3.0),
        (r"backward\s*step", 3.0),
        (r"再附着|分离流", 1.5),
    ],
    "channel_flow": [
        (r"周期通道|通道流", 2.6),
        (r"channel\s*flow", 2.6),
        (r"re[_\s-]*tau|摩擦雷诺数", 2.0),
    ],
    "heat_exchanger": [
        (r"换热器|管壳式", 3.0),
        (r"heat\s*exchanger", 3.0),
        (r"强制对流", 1.8),
    ],
    "flat_plate": [
        (r"平板边界层", 3.0),
        (r"flat\s*plate", 3.0),
        (r"blasius|y\+", 1.6),
    ],
    "mixing_elbow": [
        (r"弯管|弯头|90度弯", 2.4),
        (r"mixing\s*elbow|\belbow\b", 2.6),
        (r"二次流", 1.5),
    ],
    "shock_tube": [
        (r"激波管", 3.2),
        (r"shock\s*tube", 3.2),
        (r"\bsod\b|隔膜|接触间断", 2.0),
    ],
    "supersonic_nozzle": [
        (r"超音速喷管|拉瓦尔喷管|喷管", 3.2),
        (r"supersonic\s*nozzle|nozzle", 3.2),
        (r"喉部|马赫数|背压", 1.8),
    ],
}

_TEMPLATE_MIN_SCORE = 2.0
_TEMPLATE_MIN_MARGIN = 1.0


def _classify_template_from_description(description: str) -> Dict[str, Any]:
    """Score candidate templates from natural language description."""
    desc = description.lower()
    scored: list[Dict[str, Any]] = []

    for template_id, rules in _TEMPLATE_CLASSIFICATION_RULES.items():
        score = 0.0
        matched_rules: list[str] = []
        for pattern, weight in rules:
            if re.search(pattern, desc):
                score += weight
                matched_rules.append(pattern)
        if score > 0:
            scored.append(
                {
                    "template_id": template_id,
                    "score": round(score, 3),
                    "matched_rules": matched_rules,
                }
            )

    scored.sort(key=lambda item: item["score"], reverse=True)

    if not scored:
        return {
            "suggested_template": None,
            "classification_status": "unknown",
            "template_confidence": 0.0,
            "template_candidates": [],
            "candidate_scores": [],
            "matched_rules": [],
        }

    best = scored[0]
    second_score = scored[1]["score"] if len(scored) > 1 else 0.0
    margin = best["score"] - second_score
    confidence = best["score"] / (best["score"] + second_score + 0.5)
    confidence = max(0.0, min(0.99, round(confidence, 3)))

    status = "ok"
    if best["score"] < _TEMPLATE_MIN_SCORE or (second_score > 0 and margin < _TEMPLATE_MIN_MARGIN):
        status = "low_confidence"

    return {
        "suggested_template": best["template_id"],
        "classification_status": status,
        "template_confidence": confidence,
        "template_candidates": [item["template_id"] for item in scored[:3]],
        "candidate_scores": [
            {"template_id": item["template_id"], "score": item["score"]} for item in scored[:3]
        ],
        "matched_rules": best["matched_rules"],
    }


class ResponseFormat(str, Enum):
    """Output format for tool responses."""
    MARKDOWN = "markdown"
    JSON = "json"


# ============================================================================
# Input Models
# ============================================================================

class ListTemplatesInput(BaseModel):
    """Input for listing available templates."""
    model_config = ConfigDict(str_strip_whitespace=True)

    category: Optional[str] = Field(
        default=None,
        description="过滤模板类别: 'incompressible'(不可压缩流), 'compressible'(可压缩流), 'heat_transfer'(传热)"
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="输出格式: 'markdown' 易于阅读, 'json' 便于程序处理"
    )


class GetTemplateInfoInput(BaseModel):
    """Input for getting template details."""
    model_config = ConfigDict(str_strip_whitespace=True)

    template_id: str = Field(
        ...,
        description="模板ID，如 'pipe_flow', 'cavity_flow', 'cylinder_flow', 'natural_convection'",
        min_length=1
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="输出格式"
    )


class CreateCaseInput(BaseModel):
    """Input for creating an OpenFOAM case."""
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')

    template_id: str = Field(
        ...,
        description="要使用的模板ID",
        min_length=1
    )
    case_path: str = Field(
        ...,
        description="案例保存路径，如 '/home/user/OpenFOAM/cases/my_case'",
        min_length=1
    )
    parameters: Dict[str, Any] = Field(
        ...,
        description="模板参数，键值对形式。例如: {'diameter': 0.1, 'length': 1.0, 'inlet_velocity': 1.0, 'fluid': 'water'}"
    )
    run_mesh: bool = Field(
        default=True,
        description="是否自动运行 blockMesh 生成网格"
    )
    run_validation: bool = Field(
        default=True,
        description="是否运行验证检查"
    )

    @field_validator('case_path')
    @classmethod
    def validate_path(cls, v: str) -> str:
        return _validate_allowed_case_path(v)


class ValidateCaseInput(BaseModel):
    """Input for validating an OpenFOAM case."""
    model_config = ConfigDict(str_strip_whitespace=True)

    case_path: str = Field(
        ...,
        description="要验证的案例路径",
        min_length=1
    )
    run_openfoam_checks: bool = Field(
        default=True,
        description="是否运行 OpenFOAM 命令进行实际验证（如 blockMesh, checkMesh）"
    )

    @field_validator('case_path')
    @classmethod
    def validate_path(cls, v: str) -> str:
        return _validate_allowed_case_path(v)


class RunSolverInput(BaseModel):
    """Input for running a solver."""
    model_config = ConfigDict(str_strip_whitespace=True)

    case_path: str = Field(
        ...,
        description="案例路径",
        min_length=1
    )
    solver: Optional[str] = Field(
        default=None,
        description="求解器名称（如不指定，将从 controlDict 读取）"
    )
    timeout: Optional[float] = Field(
        default=3600,
        description="最大运行时间（秒），默认1小时",
        ge=10,
        le=86400
    )

    @field_validator('case_path')
    @classmethod
    def validate_path(cls, v: str) -> str:
        return _validate_allowed_case_path(v)


class GetRunStatusInput(BaseModel):
    """Input for getting run status."""
    model_config = ConfigDict(str_strip_whitespace=True)

    case_path: str = Field(
        ...,
        description="案例路径",
        min_length=1
    )
    log_lines: int = Field(
        default=50,
        description="返回的日志行数",
        ge=10,
        le=500
    )

    @field_validator('case_path')
    @classmethod
    def validate_path(cls, v: str) -> str:
        return _validate_allowed_case_path(v)


class AnalyzeProblemInput(BaseModel):
    """Input for analyzing a CFD problem description."""
    model_config = ConfigDict(str_strip_whitespace=True)

    description: str = Field(
        ...,
        description="问题的自然语言描述，如 '模拟水在直径5cm、长度50cm的管道中以1m/s流动'",
        min_length=10,
        max_length=2000
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="输出格式"
    )


class GenerateMeshInput(BaseModel):
    """Input for generating mesh configuration."""
    model_config = ConfigDict(str_strip_whitespace=True)

    case_path: str = Field(
        ...,
        description="案例路径",
        min_length=1
    )
    mesh_type: str = Field(
        default="blockMesh",
        description="网格类型: 'blockMesh' 或 'snappyHexMesh'"
    )
    mesh_params: Optional[Dict[str, Any]] = Field(
        default=None,
        description="网格参数，如 {'n_cells_x': 50, 'n_cells_y': 50, 'grading_x': 1.0}"
    )

    @field_validator('case_path')
    @classmethod
    def validate_path(cls, v: str) -> str:
        return _validate_allowed_case_path(v)


class GenerateBoundaryConditionsInput(BaseModel):
    """Input for generating boundary condition file."""
    model_config = ConfigDict(str_strip_whitespace=True)

    case_path: str = Field(
        ...,
        description="案例路径",
        min_length=1
    )
    field_name: str = Field(
        ...,
        description="场变量名称，如 'U', 'p', 'k', 'epsilon', 'T'",
        min_length=1
    )
    boundary_definitions: Dict[str, Dict[str, Any]] = Field(
        ...,
        description="边界定义字典，键为边界名，值为边界条件参数"
    )

    @field_validator('case_path')
    @classmethod
    def validate_path(cls, v: str) -> str:
        return _validate_allowed_case_path(v)


class RunParallelInput(BaseModel):
    """Input for running solver in parallel."""
    model_config = ConfigDict(str_strip_whitespace=True)

    case_path: str = Field(
        ...,
        description="案例路径",
        min_length=1
    )
    n_processors: int = Field(
        ...,
        description="处理器数量",
        ge=2,
        le=128
    )
    solver: Optional[str] = Field(
        default=None,
        description="求解器名称（如不指定，将从 controlDict 读取）"
    )
    timeout: Optional[float] = Field(
        default=3600,
        description="最大运行时间（秒），默认1小时",
        ge=10,
        le=86400
    )

    @field_validator('case_path')
    @classmethod
    def validate_path(cls, v: str) -> str:
        return _validate_allowed_case_path(v)


class GenerateResidualPlotInput(BaseModel):
    """Input for generating residual plot."""
    model_config = ConfigDict(str_strip_whitespace=True)

    case_path: str = Field(
        ...,
        description="案例路径",
        min_length=1
    )
    output_path: Optional[str] = Field(
        default=None,
        description="输出图片路径，如 '/path/to/residuals.png'。如不指定，保存在案例目录"
    )

    @field_validator('case_path')
    @classmethod
    def validate_path(cls, v: str) -> str:
        return _validate_allowed_case_path(v)


class CalculateYplusInput(BaseModel):
    """Input for calculating y+ and first cell height."""
    model_config = ConfigDict(str_strip_whitespace=True)

    velocity: float = Field(
        ...,
        description="特征速度（m/s），如入口速度或来流速度",
        gt=0,
        le=1000
    )
    length_scale: float = Field(
        ...,
        description="特征长度（m），如直径、弦长或水力直径",
        gt=0,
        le=100
    )
    fluid: str = Field(
        default="water",
        description="流体类型: 'water', 'air', 'oil'"
    )
    target_yplus: float = Field(
        default=30.0,
        description="目标 y+ 值。壁面函数: 30-300，低雷诺数: 1-5",
        ge=0.1,
        le=500
    )


# ============================================================================
# Tool Implementations
# ============================================================================


def _detect_solver_from_control_dict(case_path: Path) -> Optional[str]:
    """Read solver from system/controlDict application entry."""
    control_path = case_path / "system" / "controlDict"
    try:
        content = control_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None

    match = re.search(r"application\s+([A-Za-z][A-Za-z0-9]*)\s*;", content)
    if match:
        solver = match.group(1)
        if _SAFE_SOLVER_NAME_RE.fullmatch(solver):
            return solver
    return None


def _mesh_exists(case_path: Path) -> bool:
    """Check whether a usable polyMesh appears to exist."""
    poly_mesh = case_path / "constant" / "polyMesh"
    return (poly_mesh / "points").is_file() and (poly_mesh / "faces").is_file()


def _update_blockmesh_hex_cell_counts(
    content: str,
    n_cells_updates: Dict[str, Any],
) -> tuple[str, int]:
    """Apply n_cells_(x|y|z) updates to all blockMesh hex blocks."""
    direction_map = {"x": 0, "y": 1, "z": 2}
    updates_by_idx: Dict[int, str] = {}
    for key, value in n_cells_updates.items():
        if not key.startswith("n_cells_"):
            continue
        direction = key.split("_")[-1]
        idx = direction_map.get(direction)
        if idx is not None:
            updates_by_idx[idx] = str(value)

    if not updates_by_idx:
        return content, 0

    pattern = re.compile(
        r"(\bhex\b\s*\([^)]+\)\s*\()"
        r"(\s*\d+\s+\d+\s+\d+)"
        r"(\s*\))",
        flags=re.MULTILINE,
    )
    replacements = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal replacements
        cells = match.group(2).split()
        if len(cells) != 3:
            return match.group(0)

        for idx, value in updates_by_idx.items():
            cells[idx] = value

        replacements += 1
        return f"{match.group(1)}{' '.join(cells)}{match.group(3)}"

    return pattern.sub(_replace, content), replacements


def openfoam_list_templates(params: ListTemplatesInput) -> str:
    """
    列出所有可用的 OpenFOAM 案例模板。

    返回模板列表，包含ID、名称、描述和类别信息。
    可以按类别过滤模板。

    Args:
        params: 包含过滤选项和输出格式的输入参数

    Returns:
        模板列表的 Markdown 或 JSON 格式字符串
    """
    templates = get_all_templates(category=params.category)

    if params.response_format == ResponseFormat.JSON:
        data = [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "category": t.category,
                "solver": t.solver,
                "is_2d": t.is_2d,
                "required_parameters": [p.name for p in t.get_required_parameters()]
            }
            for t in templates
        ]
        return json.dumps({"templates": data, "count": len(data)}, indent=2, ensure_ascii=False)

    # Markdown format
    lines = ["# 可用的 OpenFOAM 模板", ""]

    if not templates:
        lines.append("未找到匹配的模板。")
        return "\n".join(lines)

    for t in templates:
        lines.append(f"## {t.name}")
        lines.append(f"- **ID**: `{t.id}`")
        lines.append(f"- **类别**: {t.category}")
        lines.append(f"- **求解器**: {t.solver}")
        lines.append(f"- **描述**: {t.description}")
        lines.append("")

    lines.append("---")
    lines.append("使用 `openfoam_get_template_info` 获取模板的详细参数信息。")

    return "\n".join(lines)


def openfoam_get_template_info(params: GetTemplateInfoInput) -> str:
    """
    获取指定模板的详细信息，包括所有参数定义。

    返回模板的完整信息，包括每个参数的名称、类型、单位、
    取值范围、默认值和使用提示。

    Args:
        params: 包含模板ID的输入参数

    Returns:
        模板详情的 Markdown 或 JSON 格式字符串
    """
    template = get_template(params.template_id)

    if template is None:
        available = [t.id for t in get_all_templates()]
        return f"错误: 未找到模板 '{params.template_id}'。可用模板: {', '.join(available)}"

    if params.response_format == ResponseFormat.JSON:
        data = {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "category": template.category,
            "solver": template.solver,
            "geometry_type": template.geometry_type,
            "is_2d": template.is_2d,
            "parameters": [
                {
                    "name": p.name,
                    "label": p.label,
                    "description": p.description,
                    "type": p.type,
                    "unit": p.unit,
                    "default": p.default,
                    "range": p.range,
                    "choices": p.choices,
                    "hint": p.hint,
                    "required": p.required
                }
                for p in template.parameters
            ]
        }
        return json.dumps(data, indent=2, ensure_ascii=False)

    # Markdown format
    return get_template_summary(template)


def openfoam_create_case(params: CreateCaseInput) -> str:
    """
    创建完整的 OpenFOAM 案例。

    根据选择的模板和参数生成所有必需的配置文件，
    包括初始条件、边界条件、网格设置和求解器设置。
    可选择自动运行网格生成和验证。

    Args:
        params: 包含模板ID、案例路径、参数等的输入

    Returns:
        创建结果的详细信息，包括生成的文件列表和验证结果
    """
    case_target = Path(params.case_path)
    case_existed = case_target.exists()

    try:
        # Create case configuration
        config = create_case_config_from_template(
            params.template_id,
            params.parameters,
            params.case_path
        )

        # Generate files
        generator = OpenFOAMGenerator(config)
        created_files = generator.write_case()

        result_lines = [
            "# 案例创建成功",
            "",
            f"**路径**: `{params.case_path}`",
            f"**模板**: {params.template_id}",
            f"**求解器**: {config.solver}",
            f"**雷诺数**: {config.reynolds_number:.0f}",
            f"**流动类型**: {'层流' if config.turbulence_type == TurbulenceType.LAMINAR else '湍流'}",
            "",
        ]

        # Add mesh quality info
        if config.mesh_params:
            total_cells = estimate_cell_count(config.mesh_params)
            mesh_warnings = validate_mesh_quality(config.mesh_params)
            result_lines.append(f"**网格单元数**: {total_cells:,}")

            if mesh_warnings:
                result_lines.append("")
                result_lines.append("## 网格质量提示")
                for w in mesh_warnings:
                    result_lines.append(f"⚠️ {w}")

        # Add y+ estimate for turbulent flows
        if config.turbulence_type != TurbulenceType.LAMINAR:
            char_velocity = config.geometry_params.get("inlet_velocity",
                            config.geometry_params.get("lid_velocity", 1.0))
            char_length = config.geometry_params.get("diameter",
                          config.geometry_params.get("width", 0.1))
            nu = config.fluid_properties.get("nu", 1e-6)

            yplus_result = calculate_yplus_first_layer(
                velocity=char_velocity,
                length_scale=char_length,
                kinematic_viscosity=nu,
                target_yplus=30.0
            )
            if "error" not in yplus_result:
                result_lines.append("")
                result_lines.append("## y+ 估算")
                result_lines.append(f"- 目标 y+: 30 (壁面函数)")
                result_lines.append(f"- 建议第一层网格高度: {yplus_result['first_cell_height_mm']:.3f} mm")
                result_lines.append(f"- 推荐方法: {yplus_result['recommended_approach']}")

        result_lines.append("")
        result_lines.append("## 生成的文件")

        for f in created_files[:15]:  # Limit displayed files
            result_lines.append(f"- `{f}`")

        if len(created_files) > 15:
            result_lines.append(f"- ... 共 {len(created_files)} 个文件")

        # Run mesh generation if requested
        if params.run_mesh:
            result_lines.append("")
            result_lines.append("## 网格生成")

            mesh_result = run_mesh_generation(params.case_path, timeout=300)

            if mesh_result.status == RunStatus.COMPLETED:
                result_lines.append("✅ blockMesh 执行成功")

                # Run checkMesh
                check_result = run_mesh_check(params.case_path, timeout=120)
                if check_result.status == RunStatus.COMPLETED:
                    result_lines.append("✅ checkMesh 验证通过")
                else:
                    result_lines.append(f"⚠️ checkMesh: {check_result.error_message or '未知问题'}")
            else:
                result_lines.append(f"❌ blockMesh 失败: {mesh_result.error_message}")

        # Run validation if requested
        if params.run_validation:
            result_lines.append("")
            result_lines.append("## 验证结果")

            report = validate_case(params.case_path, run_openfoam=False)

            if report.passed:
                result_lines.append("✅ 所有验证检查通过")
            else:
                for r in report.results:
                    if not r.passed:
                        icon = "❌" if r.severity == "error" else "⚠️"
                        result_lines.append(f"{icon} {r.message}")

        # Add case checklist
        from ..core.validator import generate_case_checklist, generate_performance_recommendations
        mesh_warnings = validate_mesh_quality(config.mesh_params) if config.mesh_params else []
        checklist_lines = generate_case_checklist(config, mesh_warnings)
        result_lines.append("")
        result_lines.extend(checklist_lines)

        # Add performance recommendations
        if config.mesh_params:
            total_cells = estimate_cell_count(config.mesh_params)
            perf_lines = generate_performance_recommendations(total_cells, config)
            result_lines.append("")
            result_lines.extend(perf_lines)

        result_lines.append("")
        result_lines.append("## 下一步")
        result_lines.append(f"运行求解器: `{config.solver}` (使用 `openfoam_run_solver` 工具)")

        return _truncate("\n".join(result_lines))

    except ValueError as e:
        if not case_existed and case_target.exists():
            shutil.rmtree(case_target, ignore_errors=True)
        return _truncate(f"错误: {str(e)}\n\n请使用 `openfoam_get_template_info` 查看模板的参数要求。")
    except Exception as e:
        if not case_existed and case_target.exists():
            shutil.rmtree(case_target, ignore_errors=True)
        return _truncate(f"创建案例时发生错误: {str(e)}")


def openfoam_validate_case(params: ValidateCaseInput) -> str:
    """
    验证 OpenFOAM 案例的完整性和正确性。

    执行三层验证:
    1. OpenFOAM 实际验证 (blockMesh, checkMesh)
    2. 语法和结构验证
    3. 物理合理性检查

    Args:
        params: 包含案例路径和验证选项的输入

    Returns:
        验证报告的详细信息
    """
    try:
        report = validate_case(
            params.case_path,
            run_openfoam=params.run_openfoam_checks
        )
        return _truncate(report.summary())
    except Exception as e:
        return _truncate(f"验证时发生错误: {str(e)}")


def openfoam_run_solver(params: RunSolverInput) -> str:
    """
    运行 OpenFOAM 求解器。

    执行指定的求解器并返回运行结果。
    对于长时间运行的计算，建议设置适当的超时时间。

    Args:
        params: 包含案例路径、求解器名称和超时设置的输入

    Returns:
        运行结果，包括状态、耗时和最终残差
    """
    try:
        case_path = Path(params.case_path)
        if not case_path.exists():
            return f"错误: 案例路径不存在: {params.case_path}"
        if not case_path.is_dir():
            return f"错误: case_path 不是目录: {params.case_path}"

        solver = params.solver or _detect_solver_from_control_dict(case_path)
        if not solver:
            return "错误: 未指定求解器且无法从 controlDict 读取"
        try:
            solver = _validate_solver_name(solver)
        except ValueError as exc:
            return _truncate(f"错误: {exc}")

        solver_cmd = resolve_openfoam_command(solver)

        lines = ["# 求解器运行结果", "", f"**求解器**: `{solver}`"]

        if not solver_cmd:
            lines.append("⚠️ **状态**: 未执行（环境未就绪）")
            lines.append(f"**原因**: 未找到求解器命令 `{solver}`")
            lines.append("")
            lines.append("## 建议")
            lines.append("- 使用 `openfoam_preflight_check` 进行检查（profile=`solver`）")
            lines.append("- 先 source OpenFOAM 环境配置（`etc/bashrc`）再重试")
            lines.append("- 确认求解器可执行文件位于 `PATH` 或 `FOAM_APPBIN`")
            return _truncate("\n".join(lines))

        if solver_cmd != solver:
            lines.append(f"**执行命令**: `{solver_cmd}`")

        # Run solver
        result = run_solver(
            params.case_path,
            solver_cmd,
            timeout=params.timeout
        )

        lines.append("")

        if result.status == RunStatus.COMPLETED:
            lines.append("✅ **状态**: 完成")
            lines.append(f"**收敛**: {'是' if result.converged else '否'}")
        elif result.status == RunStatus.FAILED:
            missing_cmd = bool(result.error_message and "命令" in result.error_message and "未找到" in result.error_message)
            if missing_cmd:
                lines.append("⚠️ **状态**: 未执行（环境未就绪）")
            else:
                lines.append("❌ **状态**: 失败")
            if result.error_message:
                lines.append(f"**错误**: {result.error_message}")

            # Parse errors from log and provide suggestions
            log_content = result.stdout if result.stdout else ""
            if result.stderr:
                log_content += "\n" + result.stderr

            if log_content:
                errors = parse_solver_log_errors(log_content)
                if errors:
                    lines.append("")
                    lines.append("## 错误分析与建议")
                    for err in errors:
                        icon = "🔴" if err["severity"] == "error" else "⚠️"
                        lines.append(f"{icon} **{err['message']}**")
                        lines.append(f"   💡 建议: {err['suggestion']}")
        else:
            lines.append(f"**状态**: {result.status.value}")

        lines.append(f"**耗时**: {result.duration_seconds:.1f} 秒")

        if result.final_residuals:
            lines.append("")
            lines.append("## 最终残差")
            for field, residual in result.final_residuals.items():
                lines.append(f"- {field}: {residual:.2e}")

        # Include log tail for errors
        if result.status == RunStatus.FAILED and result.stderr:
            lines.append("")
            lines.append("## 错误日志（最后1000字符）")
            lines.append("```")
            lines.append(result.stderr[-1000:])
            lines.append("```")

        return _truncate("\n".join(lines))

    except Exception as e:
        return _truncate(f"运行求解器时发生错误: {str(e)}")


def openfoam_analyze_problem(params: AnalyzeProblemInput) -> str:
    """
    分析 CFD 问题描述并给出建议。

    根据自然语言描述分析问题类型、推荐模板、
    估算雷诺数并给出建模建议。

    Args:
        params: 包含问题描述的输入

    Returns:
        分析结果和建议
    """
    desc = params.description.lower()
    classification = _classify_template_from_description(desc)

    # Analyze problem type
    analysis = {
        "problem_type": classification["suggested_template"],
        "suggested_template": classification["suggested_template"],
        "classification_status": classification["classification_status"],
        "template_confidence": classification["template_confidence"],
        "template_candidates": classification["template_candidates"],
        "candidate_scores": classification["candidate_scores"],
        "matched_rules": classification["matched_rules"],
        "geometry_params": {},
        "flow_params": {},
        "recommendations": [],
    }

    # Detect fluid
    fluid = "water"
    if "空气" in desc or "air" in desc:
        fluid = "air"
    elif "油" in desc or "oil" in desc:
        fluid = "oil"
    analysis["flow_params"]["fluid"] = fluid

    # Extract numbers (simple pattern matching)
    # Diameter/size patterns
    size_patterns = [
        (r'直径\s*([\d.]+)\s*(?:cm|厘米)', 0.01),
        (r'直径\s*([\d.]+)\s*(?:m|米)', 1.0),
        (r'直径\s*([\d.]+)\s*(?:mm|毫米)', 0.001),
        (r'diameter\s*([\d.]+)\s*(?:cm)', 0.01),
        (r'([\d.]+)\s*(?:cm|厘米).*直径', 0.01),
    ]

    for pattern, multiplier in size_patterns:
        match = re.search(pattern, desc)
        if match:
            analysis["geometry_params"]["diameter"] = float(match.group(1)) * multiplier
            break

    # Length patterns
    length_patterns = [
        (r'长度?\s*([\d.]+)\s*(?:cm|厘米)', 0.01),
        (r'长度?\s*([\d.]+)\s*(?:m|米)', 1.0),
        (r'([\d.]+)\s*(?:cm|厘米).*长', 0.01),
        (r'([\d.]+)\s*(?:m|米).*长', 1.0),
    ]

    for pattern, multiplier in length_patterns:
        match = re.search(pattern, desc)
        if match:
            analysis["geometry_params"]["length"] = float(match.group(1)) * multiplier
            break

    # Velocity patterns
    velocity_patterns = [
        (r'(?:速度?|流速)\s*([\d.]+)\s*(?:m/s|米每秒)', 1.0),
        (r'([\d.]+)\s*(?:m/s|米每秒)', 1.0),
        (r'以\s*([\d.]+)\s*(?:m/s)?.*流', 1.0),
    ]

    for pattern, multiplier in velocity_patterns:
        match = re.search(pattern, desc)
        if match:
            analysis["flow_params"]["inlet_velocity"] = float(match.group(1)) * multiplier
            break

    # Calculate Reynolds number if possible
    fluid_props = get_fluid_properties(fluid)
    if fluid_props and "diameter" in analysis["geometry_params"] and "inlet_velocity" in analysis["flow_params"]:
        re_num = estimate_reynolds_number(
            analysis["flow_params"]["inlet_velocity"],
            analysis["geometry_params"]["diameter"],
            fluid_props["nu"]
        )
        analysis["reynolds_number"] = re_num
        analysis["turbulence_recommendation"] = recommend_turbulence_model(re_num)

        if re_num < 2300:
            analysis["recommendations"].append("雷诺数 < 2300，流动为层流")
        elif re_num < 4000:
            analysis["recommendations"].append("雷诺数在过渡区，建议使用湍流模型")
        else:
            analysis["recommendations"].append(f"雷诺数 = {re_num:.0f}，流动为湍流，建议使用 k-epsilon 模型")

    # Format output
    if params.response_format == ResponseFormat.JSON:
        return json.dumps(analysis, indent=2, ensure_ascii=False)

    lines = ["# 问题分析结果", ""]

    if analysis["suggested_template"]:
        template = get_template(analysis["suggested_template"])
        lines.append(f"## 推荐模板: {template.name if template else analysis['suggested_template']}")
        lines.append(f"模板 ID: `{analysis['suggested_template']}`")
        lines.append("")

    if analysis["geometry_params"]:
        lines.append("## 识别的几何参数")
        for k, v in analysis["geometry_params"].items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    if analysis["flow_params"]:
        lines.append("## 识别的流动参数")
        for k, v in analysis["flow_params"].items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    if "reynolds_number" in analysis:
        lines.append(f"## 雷诺数估算: {analysis['reynolds_number']:.0f}")
        lines.append("")

    if analysis["classification_status"] == "low_confidence":
        analysis["recommendations"].append(
            "模板识别置信度较低，请补充几何类型或物理场景关键词（如激波管/喷管/方腔/溃坝）"
        )
    elif analysis["classification_status"] == "unknown":
        analysis["recommendations"].append("未能识别模板，请明确案例类型后重试")

    if analysis["recommendations"]:
        lines.append("## 建议")
        for rec in analysis["recommendations"]:
            lines.append(f"- {rec}")
        lines.append("")

    # Next steps
    lines.append("## 下一步")
    if analysis["suggested_template"]:
        lines.append(f"使用 `openfoam_create_case` 创建案例，模板 ID: `{analysis['suggested_template']}`")
        lines.append(f"当前模板识别状态: `{analysis['classification_status']}`，置信度: {analysis['template_confidence']:.2f}")
    else:
        lines.append("请提供更多信息以确定合适的模板，或使用 `openfoam_list_templates` 查看可用模板")

    return "\n".join(lines)


def openfoam_get_fluid_properties(fluid_name: str) -> str:
    """
    获取常见流体的物性参数。

    Args:
        fluid_name: 流体名称 (water, air, oil)

    Returns:
        流体物性参数
    """
    props = get_fluid_properties(fluid_name)

    if props is None:
        available = list(FLUID_PROPERTIES.keys())
        return f"错误: 未知流体 '{fluid_name}'。可用流体: {', '.join(available)}"

    lines = [
        f"# {props['name']} 物性参数",
        "",
        f"- **密度 (ρ)**: {props['rho']} kg/m³",
        f"- **运动粘度 (ν)**: {props['nu']} m²/s",
        f"- **动力粘度 (μ)**: {props['mu']} Pa·s",
        f"- **比热容 (Cp)**: {props['Cp']} J/(kg·K)",
        f"- **导热系数 (k)**: {props['k']} W/(m·K)",
        f"- **普朗特数 (Pr)**: {props['Pr']}",
    ]

    return "\n".join(lines)


def openfoam_calculate_yplus(params: CalculateYplusInput) -> str:
    """
    计算 y+ 值和第一层网格高度。

    根据流动条件估算达到目标 y+ 所需的第一层网格高度，
    帮助用户设置合理的近壁面网格。

    Args:
        params: 包含速度、特征长度、流体类型和目标 y+ 的输入

    Returns:
        y+ 分析结果
    """
    # Get fluid kinematic viscosity
    fluid_nu = {"water": 1e-6, "air": 1.5e-5, "oil": 1e-4}.get(params.fluid)
    if fluid_nu is None:
        return _truncate(f"错误: 未知流体 '{params.fluid}'。支持: water, air, oil")

    result = calculate_yplus_first_layer(
        velocity=params.velocity,
        length_scale=params.length_scale,
        kinematic_viscosity=fluid_nu,
        target_yplus=params.target_yplus
    )

    if "error" in result:
        return _truncate(f"错误: {result['error']}")

    lines = [
        "# y+ 分析结果",
        "",
        "## 流动参数",
        f"- **速度**: {params.velocity} m/s",
        f"- **特征长度**: {params.length_scale} m",
        f"- **流体**: {params.fluid} (ν = {fluid_nu} m²/s)",
        f"- **雷诺数**: {result['reynolds_number']:.0f}",
        "",
        "## y+ 计算",
        f"- **目标 y+**: {result['target_yplus']}",
        f"- **摩擦系数 Cf**: {result['skin_friction_Cf']:.6f}",
        f"- **摩擦速度 u_τ**: {result['friction_velocity_u_tau']:.4f} m/s",
        f"- **第一层网格高度**: {result['first_cell_height_mm']:.4f} mm ({result['first_cell_height_m']:.2e} m)",
        "",
        "## 建议",
        f"- **方法**: {result['recommended_approach']}",
        f"- **推荐 y+ 范围**: {result['recommended_yplus_range']}",
        f"- **说明**: {result['note']}",
    ]

    # Add additional guidance
    Re = result['reynolds_number']
    lines.append("")
    lines.append("## 网格设置指导")
    if Re < 2300:
        lines.append("- 层流流动，无需湍流模型")
        lines.append("- 近壁面网格无特殊要求")
    elif params.target_yplus >= 30:
        lines.append("- 使用壁面函数（wall functions）")
        lines.append("- 第一层网格 y+ 应在 30-300 之间")
        lines.append("- 推荐 k-epsilon 或 k-omega SST 配合壁面函数")
    else:
        lines.append("- 使用低雷诺数模型（resolve boundary layer）")
        lines.append("- 需要更多近壁面网格层数")
        lines.append("- 推荐 k-omega SST 低雷诺数模式")

    return _truncate("\n".join(lines))


def openfoam_generate_mesh(params: GenerateMeshInput) -> str:
    """
    生成网格配置文件。

    单独生成 blockMeshDict 或 snappyHexMeshDict 配置文件，
    不运行网格生成命令。适用于需要精细控制网格参数的场景。

    Args:
        params: 包含案例路径、网格类型和参数的输入

    Returns:
        生成结果信息
    """
    try:
        case_path = Path(params.case_path)

        if not case_path.exists():
            return _truncate(f"错误: 案例路径不存在: {params.case_path}")

        if params.mesh_type == "blockMesh":
            # Check if blockMeshDict already exists
            blockmesh_path = case_path / "system" / "blockMeshDict"

            if not blockmesh_path.exists():
                return _truncate("错误: 未找到 blockMeshDict 模板。请先使用 openfoam_create_case 创建完整案例。")

            # If mesh_params provided, update the file
            if params.mesh_params:
                # Read existing file
                with open(blockmesh_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()

                # Update all hex block cell tuples, if requested.
                updated_content, updated_blocks = _update_blockmesh_hex_cell_counts(
                    content,
                    params.mesh_params,
                )

                # Write updated content atomically.
                atomic_write_text(blockmesh_path, updated_content)

                if any(k.startswith("n_cells_") for k in params.mesh_params) and updated_blocks == 0:
                    return _truncate(
                        f"⚠️ 未在 `{blockmesh_path}` 中找到可更新的 hex 网格块。\n"
                        "请检查 blockMeshDict 的 blocks 区段格式。"
                    )

                return _truncate(f"网格配置已更新: {blockmesh_path}\n使用 blockMesh 命令生成网格。")
            else:
                return _truncate(f"blockMeshDict 已存在: {blockmesh_path}\n使用 blockMesh 命令生成网格。")

        elif params.mesh_type == "snappyHexMesh":
            return _truncate("snappyHexMesh 支持正在开发中。请使用 blockMesh。")

        else:
            return _truncate(
                f"错误: 未知的网格类型 '{params.mesh_type}'。支持的类型: blockMesh, snappyHexMesh"
            )

    except Exception as e:
        return _truncate(f"生成网格配置时发生错误: {str(e)}")


def openfoam_generate_boundary_conditions(params: GenerateBoundaryConditionsInput) -> str:
    """
    为指定场变量生成边界条件文件。

    创建单个场变量的边界条件文件（如 U, p, k 等），
    根据提供的边界定义生成 OpenFOAM 格式的配置。

    Args:
        params: 包含案例路径、场名和边界定义的输入

    Returns:
        生成的边界条件内容或错误信息
    """
    try:
        case_path = Path(params.case_path)
        field_path = case_path / "0" / params.field_name

        # Ensure 0 directory exists
        (case_path / "0").mkdir(parents=True, exist_ok=True)

        # Determine field type and dimensions
        field_dims = {
            "U": ("[0 1 -1 0 0 0 0]", "volVectorField", "(0 0 0)"),
            "p": ("[0 2 -2 0 0 0 0]", "volScalarField", "0"),
            "k": ("[0 2 -2 0 0 0 0]", "volScalarField", "0.001"),
            "epsilon": ("[0 2 -3 0 0 0 0]", "volScalarField", "0.001"),
            "omega": ("[0 0 -1 0 0 0 0]", "volScalarField", "1"),
            "nut": ("[0 2 -1 0 0 0 0]", "volScalarField", "0"),
            "T": ("[0 0 0 1 0 0 0]", "volScalarField", "300"),
        }

        warnings = []
        if params.field_name not in field_dims:
            warnings.append(f"未知场变量 '{params.field_name}'，已使用默认设置")

        dims, class_type, internal_field = field_dims.get(
            params.field_name,
            ("[0 0 0 0 0 0 0]", "volScalarField", "0")
        )

        # Generate file header
        content = _build_openfoam_header(class_type, params.field_name)

        content += f"\ndimensions      {dims};\n\n"
        content += f"internalField   uniform {internal_field};\n\n"
        content += "boundaryField\n{\n"

        # Add boundary conditions
        for boundary_name, bc_def in params.boundary_definitions.items():
            content += f"    {boundary_name}\n"
            content += "    {\n"

            bc_type = str(bc_def.get("type", "zeroGradient")).strip()
            if not _SAFE_BC_EXTRA_KEY_RE.fullmatch(bc_type):
                return _truncate(f"生成边界条件时发生错误: 非法边界类型: {bc_type}")
            content += f"        type            {bc_type};\n"

            # Add value if needed
            if "value" in bc_def:
                normalized_value = _normalize_boundary_value(bc_def["value"], class_type)
                content += f"        value           uniform {normalized_value};\n"
            elif bc_type == "fixedValue":
                content += f"        value           uniform {internal_field};\n"

            # Add other properties
            for key, value in bc_def.items():
                if key not in ["type", "value"]:
                    key_text, value_text = _validate_bc_extra_entry(key, value)
                    content += f"        {key_text}            {value_text};\n"

            content += "    }\n"

        content += "}\n\n"
        content += "// ************************************************************************* //\n"

        # Write file atomically.
        atomic_write_text(field_path, content)

        lines = [
            f"# 边界条件文件已生成: {field_path}",
            "",
            f"**场变量**: {params.field_name}",
            f"**维度**: {dims}",
            f"**边界数量**: {len(params.boundary_definitions)}",
            "",
            "## 边界列表",
        ]

        if warnings:
            lines.insert(2, f"⚠️ {'; '.join(warnings)}")
            lines.insert(3, "")

        for name, bc in params.boundary_definitions.items():
            lines.append(f"- {name}: {bc.get('type', 'zeroGradient')}")

        return _truncate("\n".join(lines))

    except Exception as e:
        return _truncate(f"生成边界条件时发生错误: {str(e)}")


def openfoam_get_run_status(params: GetRunStatusInput) -> str:
    """
    获取当前求解器运行状态。

    读取日志文件并解析运行进度、残差和收敛信息。
    适用于监控正在运行或已完成的计算。

    Args:
        params: 包含案例路径和日志行数的输入

    Returns:
        运行状态信息
    """
    try:
        case_path = Path(params.case_path)

        # Find log file
        log_files = list(case_path.glob("log.*"))

        if not log_files:
            return "未找到日志文件。求解器可能尚未运行。"

        # Use the most recent log file
        log_file = max(log_files, key=lambda p: p.stat().st_mtime)

        # Read log file
        with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()

        # Get last N lines
        recent_lines = lines[-params.log_lines:]

        # Parse status information
        status_info = {
            "current_time": None,
            "iterations": [],
            "residuals": {},
            "errors": [],
            "completed": False
        }

        for line in recent_lines:
            # Parse time
            time_match = re.search(r'Time\s*=\s*([\d.e+-]+)', line)
            if time_match:
                status_info["current_time"] = float(time_match.group(1))

            # Parse iteration
            iter_match = re.search(r'Iteration\s*(\d+)', line, re.IGNORECASE)
            if iter_match:
                status_info["iterations"].append(int(iter_match.group(1)))

            # Parse residuals
            residual_match = re.search(
                r'Solving for (\w+),.*?Final residual\s*=\s*([\d.e+-]+)',
                line
            )
            if residual_match:
                field = residual_match.group(1)
                residual = float(residual_match.group(2))
                if field not in status_info["residuals"]:
                    status_info["residuals"][field] = []
                status_info["residuals"][field].append(residual)

            # Check for errors
            if "FOAM FATAL" in line or "error" in line.lower():
                status_info["errors"].append(line.strip())

            # Check for completion
            if "End" in line or "Finalising parallel run" in line:
                status_info["completed"] = True

        # Format output
        lines_out = ["# 求解器运行状态", ""]

        lines_out.append(f"**日志文件**: {log_file.name}")

        if status_info["completed"]:
            lines_out.append("**状态**: 已完成")
        else:
            lines_out.append("**状态**: 运行中或已停止")

        if status_info["current_time"] is not None:
            lines_out.append(f"**当前时间**: {status_info['current_time']}")

        if status_info["iterations"]:
            last_iter = status_info["iterations"][-1]
            lines_out.append(f"**当前迭代**: {last_iter}")

        if status_info["residuals"]:
            lines_out.append("")
            lines_out.append("## 最新残差")
            for field, residuals in status_info["residuals"].items():
                if residuals:
                    latest = residuals[-1]
                    lines_out.append(f"- {field}: {latest:.2e}")

        if status_info["errors"]:
            lines_out.append("")
            lines_out.append("## 错误信息")
            for error in status_info["errors"][:5]:  # Show first 5 errors
                lines_out.append(f"- {error}")

        # Include log tail
        lines_out.append("")
        lines_out.append(f"## 日志尾部 (最后 {min(params.log_lines, len(recent_lines))} 行)")
        lines_out.append("```")
        lines_out.extend([line.rstrip() for line in recent_lines[-20:]])
        lines_out.append("```")

        return _truncate("\n".join(lines_out))

    except Exception as e:
        return _truncate(f"获取运行状态时发生错误: {str(e)}")


def openfoam_run_parallel(params: RunParallelInput) -> str:
    """
    并行运行 OpenFOAM 求解器。

    使用 MPI 并行执行求解器，支持自动分解网格和重建结果。
    需要预先配置 decomposeParDict 或使用默认设置。

    Args:
        params: 包含案例路径、处理器数量、求解器和超时的输入

    Returns:
        运行结果信息
    """
    try:
        case_path = Path(params.case_path)
        if not case_path.exists():
            return f"错误: 案例路径不存在: {params.case_path}"
        if not case_path.is_dir():
            return f"错误: case_path 不是目录: {params.case_path}"

        solver = params.solver or _detect_solver_from_control_dict(case_path)
        if not solver:
            return "错误: 未指定求解器且无法从 controlDict 读取"
        try:
            solver = _validate_solver_name(solver)
        except ValueError as exc:
            return _truncate(f"错误: {exc}")

        solver_cmd = resolve_openfoam_command(solver)
        blockmesh_cmd = resolve_openfoam_command("blockMesh")
        decompose_cmd = resolve_openfoam_command("decomposePar")
        mpirun_cmd = resolve_openfoam_command("mpirun")
        reconstruct_cmd = resolve_openfoam_command("reconstructPar")

        lines = [
            "# 并行求解器运行",
            "",
            f"**求解器**: `{solver}`",
            f"**处理器数量**: `{params.n_processors}`",
            "",
        ]

        # Ensure mesh is present before decomposition.
        if not _mesh_exists(case_path):
            blockmesh_dict = case_path / "system" / "blockMeshDict"
            if blockmesh_cmd and blockmesh_dict.is_file():
                lines.append("⚠️ 检测到缺失网格，已自动运行 blockMesh")
                mesh_result = run_mesh_generation(
                    case_path=str(case_path),
                    timeout=min(float(params.timeout or 3600), 600.0),
                )
                if mesh_result.status == RunStatus.COMPLETED:
                    lines.append("✅ 自动网格生成完成")
                else:
                    lines.append("❌ 自动网格生成失败")
                    if mesh_result.error_message:
                        lines.append(f"**错误**: {mesh_result.error_message}")
                    return _truncate("\n".join(lines))
                lines.append("")
            else:
                lines.append("⚠️ 未检测到可分解网格且无法自动运行 blockMesh")
                if not blockmesh_dict.is_file():
                    lines.append("**原因**: 缺少 `system/blockMeshDict`")
                if not blockmesh_cmd:
                    lines.append("**原因**: `blockMesh` 命令不可用")
                lines.append("")

        parallel_ready = bool(solver_cmd and decompose_cmd and mpirun_cmd)
        if not parallel_ready:
            missing = []
            if not solver_cmd:
                missing.append(solver)
            if not decompose_cmd:
                missing.append("decomposePar")
            if not mpirun_cmd:
                missing.append("mpirun")

            lines.append("⚠️ 并行依赖不完整，回退到串行")
            lines.append(f"**缺失项**: {', '.join(missing)}")

            if not solver_cmd:
                lines.append("⚠️ **状态**: 未执行（环境未就绪）")
                lines.append("")
                lines.append("## 建议")
                lines.append("- 使用 `openfoam_preflight_check` 检查（profile=`parallel`）")
                lines.append("- source OpenFOAM 环境配置并确认求解器可执行")
                return _truncate("\n".join(lines))

            serial_result = run_solver(
                case_path=str(case_path),
                solver=solver_cmd,
                timeout=params.timeout,
            )
            if serial_result.status == RunStatus.COMPLETED:
                lines.append("✅ 串行求解完成")
                lines.append(f"**耗时**: {serial_result.duration_seconds:.1f} 秒")
            else:
                lines.append("❌ 串行求解失败")
                lines.append(f"**耗时**: {serial_result.duration_seconds:.1f} 秒")
                if serial_result.error_message:
                    lines.append(f"**错误**: {serial_result.error_message}")

            if serial_result.final_residuals:
                lines.append("")
                lines.append("## 最终残差")
                for field, residual in serial_result.final_residuals.items():
                    lines.append(f"- {field}: {residual:.2e}")

            return _truncate("\n".join(lines))

        lines.append("## 分解网格")
        decompose_timeout = min(float(params.timeout or 3600), 600.0)
        decompose_result = decompose_case(
            case_path=str(case_path),
            n_processors=params.n_processors,
            method="scotch",
            timeout=decompose_timeout,
            force=True,
        )

        if decompose_result.status != RunStatus.COMPLETED:
            lines.append("❌ decomposePar 失败")
            if decompose_result.error_message:
                lines.append(f"**错误**: {decompose_result.error_message}")
            if decompose_result.stderr:
                lines.append("```")
                lines.append(decompose_result.stderr[-1000:])
                lines.append("```")

            if solver_cmd:
                lines.append("")
                lines.append("⚠️ decomposePar 失败，回退到串行求解")
                serial_result = run_solver(
                    case_path=str(case_path),
                    solver=solver_cmd,
                    timeout=params.timeout,
                )
                if serial_result.status == RunStatus.COMPLETED:
                    lines.append("✅ 串行求解完成")
                    lines.append(f"**耗时**: {serial_result.duration_seconds:.1f} 秒")
                else:
                    lines.append("❌ 串行求解失败")
                    if serial_result.error_message:
                        lines.append(f"**错误**: {serial_result.error_message}")
            return _truncate("\n".join(lines))

        lines.append("✅ decomposePar 完成")
        lines.append("")
        lines.append("## 运行求解器")

        solver_result = run_parallel(
            case_path=str(case_path),
            solver=solver_cmd,
            n_processors=params.n_processors,
            timeout=params.timeout,
        )

        if solver_result.status == RunStatus.COMPLETED:
            lines.append("✅ 并行求解完成")
            lines.append(f"**耗时**: {solver_result.duration_seconds:.1f} 秒")
        else:
            lines.append("❌ 并行求解失败")
            lines.append(f"**耗时**: {solver_result.duration_seconds:.1f} 秒")
            if solver_result.error_message:
                lines.append(f"**错误**: {solver_result.error_message}")

            log_content = solver_result.stdout or ""
            if solver_result.stderr:
                log_content += "\n" + solver_result.stderr

            issues = parse_solver_log_errors(log_content)
            if issues:
                lines.append("")
                lines.append("## 错误分析与建议")
                for issue in issues:
                    icon = "🔴" if issue["severity"] == "error" else "⚠️"
                    lines.append(f"{icon} **{issue['message']}**")
                    lines.append(f"   💡 建议: {issue['suggestion']}")

            if solver_result.stderr:
                lines.append("")
                lines.append("## 错误日志（最后1000字符）")
                lines.append("```")
                lines.append(solver_result.stderr[-1000:])
                lines.append("```")

            return _truncate("\n".join(lines))

        if solver_result.final_residuals:
            lines.append("")
            lines.append("## 最终残差")
            for field, residual in solver_result.final_residuals.items():
                lines.append(f"- {field}: {residual:.2e}")

        lines.append("")
        lines.append("## 重建结果")
        if reconstruct_cmd:
            reconstruct_timeout = min(float(params.timeout or 3600), 600.0)
            reconstruct_result = reconstruct_case(
                case_path=str(case_path),
                latest_time=False,
                timeout=reconstruct_timeout,
            )

            if reconstruct_result.status == RunStatus.COMPLETED:
                lines.append("✅ reconstructPar 完成")
            else:
                lines.append("⚠️ reconstructPar 未完全成功")
                if reconstruct_result.error_message:
                    lines.append(f"**错误**: {reconstruct_result.error_message}")
        else:
            lines.append("⚠️ reconstructPar 不可用，已跳过结果重建")

        return _truncate("\n".join(lines))

    except Exception as e:
        return _truncate(f"并行运行时发生错误: {str(e)}")


def openfoam_generate_residual_plot(params: GenerateResidualPlotInput) -> str:
    """
    生成残差曲线图。

    从日志文件中提取残差数据并生成曲线图，
    用于可视化求解器收敛过程。需要安装 matplotlib。

    Args:
        params: 包含案例路径和输出路径的输入

    Returns:
        生成的图片路径或错误信息
    """
    try:
        case_path = Path(params.case_path)

        log_files = list(case_path.glob("log.*"))
        if not log_files:
            return "错误: 未找到日志文件"

        log_file = max(log_files, key=lambda p: p.stat().st_mtime)
        residuals_data = parse_log_file(str(log_file))
        if not residuals_data:
            return "错误: 未在日志文件中找到残差数据"

        if params.output_path:
            output_file = Path(params.output_path)
        else:
            output_file = case_path / "residuals.png"

        output_file.parent.mkdir(parents=True, exist_ok=True)
        core_generate_residual_plot(
            residuals=residuals_data,
            output_path=str(output_file),
            plot_type="final",
            log_scale=True,
            title="Convergence History",
        )

        lines = [
            "# 残差图已生成",
            "",
            f"**输出文件**: {output_file}",
            f"**场变量数量**: {len(residuals_data)}",
            "",
            "## 包含的场变量",
        ]

        for field, data in residuals_data.items():
            final_residual = data.final_residuals[-1] if data.final_residuals else 0.0
            points = len(data.final_residuals)
            lines.append(f"- {field}: 最终残差 = {final_residual:.2e} ({points} 个数据点)")

        return _truncate("\n".join(lines))

    except ImportError:
        return "错误: 需要安装 matplotlib。请运行: pip install matplotlib"
    except Exception as e:
        return _truncate(f"生成残差图时发生错误: {str(e)}")
