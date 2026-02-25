"""Stability-focused tools for OpenFOAM workflows."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from shutil import which
import json
import os
import re
import subprocess
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .path_utils import atomic_write_text, validate_allowed_case_path


class ResponseFormat(str, Enum):
    """Output format for stability tools."""

    MARKDOWN = "markdown"
    JSON = "json"


class PreflightCheckInput(BaseModel):
    """Input for OpenFOAM preflight checks."""

    model_config = ConfigDict(str_strip_whitespace=True)

    case_path: Optional[str] = Field(
        default=None,
        description="可选案例目录路径。提供后会检查目录结构和关键文件。",
    )
    solver: Optional[str] = Field(
        default=None,
        description="可选求解器名称。提供后会检查该求解器命令是否可用。",
    )
    n_processors: Optional[int] = Field(
        default=None,
        ge=2,
        le=1024,
        description="并行核心数。提供后会额外检查 mpirun 可用性。",
    )
    profile: Literal["diagnostic", "mesh", "solver", "parallel"] = Field(
        default="diagnostic",
        description="预检场景：diagnostic(仅诊断)、mesh(网格执行)、solver(串行求解)、parallel(并行求解)",
    )
    strict: bool = Field(
        default=False,
        description="是否使用严格模式。严格模式下缺失关键环境变量会计为错误。",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="输出格式: markdown 或 json",
    )

    @field_validator("case_path")
    @classmethod
    def validate_case_path(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return validate_allowed_case_path(value)

    @field_validator("solver")
    @classmethod
    def validate_solver(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _validate_solver_name(value)


class AssessCaseStabilityInput(BaseModel):
    """Input for case numerical-stability assessment."""

    model_config = ConfigDict(str_strip_whitespace=True)

    case_path: str = Field(..., min_length=1, description="OpenFOAM 案例目录路径")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="输出格式: markdown 或 json",
    )

    @field_validator("case_path")
    @classmethod
    def validate_case_path(cls, value: str) -> str:
        return validate_allowed_case_path(value)


class ApplyStabilityFixesInput(BaseModel):
    """Input for automatic stability fixes."""

    model_config = ConfigDict(str_strip_whitespace=True)

    case_path: str = Field(..., min_length=1, description="OpenFOAM 案例目录路径")
    strategy: Literal["balanced", "conservative"] = Field(
        default="balanced",
        description="修复策略：balanced(常规) 或 conservative(更保守)",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="输出格式: markdown 或 json",
    )

    @field_validator("case_path")
    @classmethod
    def validate_case_path(cls, value: str) -> str:
        return validate_allowed_case_path(value)


_TRANSIENT_SOLVERS = {
    "icofoam",
    "pisofoam",
    "pimplefoam",
    "interfoam",
    "compressibleinterfoam",
    "rhocentralfoam",
    "buoyantpimplefoam",
}

_STEADY_SOLVERS = {
    "simplefoam",
    "buoyantsimplefoam",
}

_SAFE_SOLVER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_FLOAT_COMPARE_EPS = 1e-9
_LAMINAR_ONLY_SOLVERS = {"icofoam"}


def _validate_solver_name(solver: str) -> str:
    """Validate solver token to avoid unexpected command execution."""
    name = solver.strip()
    if not _SAFE_SOLVER_NAME_RE.fullmatch(name):
        raise ValueError(f"非法求解器名称: {solver}")
    return name


def _extract_scalar_value(content: str, key: str) -> Optional[float]:
    """Extract numeric value for a dictionary key."""
    match = re.search(rf"\b{re.escape(key)}\s+([-+]?[\d.]+(?:[eE][-+]?\d+)?)\s*;", content)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_word_value(content: str, key: str) -> Optional[str]:
    """Extract word-like value for a dictionary key."""
    match = re.search(rf"\b{re.escape(key)}\s+([^;\s]+)\s*;", content)
    if not match:
        return None
    return match.group(1).strip()


def _extract_block(content: str, block_name: str) -> Optional[str]:
    """Extract the text body of a named OpenFOAM dictionary block."""
    start_match = re.search(rf"\b{re.escape(block_name)}\b\s*\{{", content)
    if not start_match:
        return None

    start = start_match.end() - 1  # points at "{"
    depth = 0
    for idx in range(start, len(content)):
        ch = content[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[start + 1 : idx]

    return None


def _extract_application(control_dict: str) -> Optional[str]:
    """Extract solver name from controlDict application entry."""
    return _extract_word_value(control_dict, "application")


def _probe_command_runtime_issue(command: str, resolved_path: str) -> Optional[str]:
    """Probe command load-time/runtime issues that `which` cannot detect."""
    if command not in {"decomposePar", "mpirun"}:
        return None

    probe_args = ["-help"] if command == "decomposePar" else ["--version"]
    try:
        proc = subprocess.run(
            [resolved_path, *probe_args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None

    output = f"{proc.stdout}\n{proc.stderr}".lower()
    if command == "decomposePar":
        if "libmetisdecomp.so" in output and "metis" in output:
            return "运行时缺少 libmetisDecomp.so（并行分解可能失败）"
        return None

    if (
        "pmix_ifinit" in output
        and "socket() failed" in output
        and ("errno=1" in output or "operation not permitted" in output)
    ) or "pmix server's listener thread failed to start" in output:
        return "PMIx listener 初始化失败（socket 权限受限，mpirun 并行不可用）"

    if "operation not permitted" in output and ("pmix" in output or "prte" in output):
        return "MPI 运行时权限受限（Operation not permitted），并行求解不可用"

    return None


def _has_function_object_type(control_dict: str, object_type: str) -> bool:
    """Check whether controlDict functions block contains a given functionObject type."""
    functions_block = _extract_block(control_dict, "functions")
    if not functions_block:
        return False
    pattern = rf"\btype\s+{re.escape(object_type)}\s*;"
    return bool(re.search(pattern, functions_block))


def _collect_solver_compatibility_checks(
    solver: Optional[str],
    control_text: Optional[str],
    case_path: Optional[Path],
) -> List[Dict[str, str]]:
    """Collect compatibility findings between solver and runtime dictionaries."""
    checks: List[Dict[str, str]] = []
    if not solver:
        return checks

    solver_name = solver.strip()
    solver_lower = solver_name.lower()
    if solver_lower not in _LAMINAR_ONLY_SOLVERS:
        return checks
    if not control_text:
        return checks

    if _has_function_object_type(control_text, "yPlus"):
        checks.append(
            {
                "severity": "error",
                "title": f"{solver_name} 与 yPlus 兼容性",
                "message": (
                    f"检测到 yPlus functionObject，但 {solver_name} 通常不提供湍流模型数据库，"
                    "运行时可能触发 fatal error。"
                ),
                "suggestion": "移除 yPlus functionObject，或切换到支持湍流的求解器（如 pimpleFoam）。",
            }
        )

    if case_path is not None:
        turbulence_path = case_path / "constant" / "turbulenceProperties"
        if turbulence_path.exists():
            try:
                turbulence_text = turbulence_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                turbulence_text = ""
            simulation_type = (_extract_word_value(turbulence_text, "simulationType") or "").strip()
            if simulation_type and simulation_type.lower() != "laminar":
                checks.append(
                    {
                        "severity": "error",
                        "title": f"{solver_name} 与 turbulenceProperties 兼容性",
                        "message": (
                            f"constant/turbulenceProperties 设置 simulationType={simulation_type}，"
                            f"与 {solver_name} 预期的层流设置不兼容。"
                        ),
                        "suggestion": "将 simulationType 设置为 laminar，或改用支持湍流模型的求解器。",
                    }
                )

    return checks


def _extract_boundary_section(block_mesh_content: str) -> Optional[str]:
    """Extract boundary list body from blockMeshDict."""
    boundary_keyword = re.search(r"\bboundary\b", block_mesh_content)
    if not boundary_keyword:
        return None

    list_start = block_mesh_content.find("(", boundary_keyword.end())
    if list_start < 0:
        return None

    depth = 0
    list_end = -1
    for idx in range(list_start, len(block_mesh_content)):
        ch = block_mesh_content[idx]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                list_end = idx
                break
    if list_end < 0:
        return None
    return block_mesh_content[list_start + 1 : list_end]


def _iter_patch_blocks(boundary_section: str):
    """Yield `(name, body)` tuples for each patch in blockMesh boundary section."""
    idx = 0
    length = len(boundary_section)
    name_pattern = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\{", re.MULTILINE)

    while idx < length:
        match = name_pattern.search(boundary_section, idx)
        if not match:
            break

        patch_name = match.group(1)
        brace_start = boundary_section.find("{", match.start())
        if brace_start < 0:
            break

        depth = 0
        brace_end = -1
        for cursor in range(brace_start, length):
            ch = boundary_section[cursor]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    brace_end = cursor
                    break

        if brace_end < 0:
            break

        yield patch_name, boundary_section[brace_start + 1 : brace_end]
        idx = brace_end + 1


def _extract_patch_value(patch_body: str, key: str) -> Optional[str]:
    """Extract a single entry value from patch block."""
    match = re.search(rf"^\s*{re.escape(key)}\s+([^;\n]+)\s*;", patch_body, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def _collect_block_mesh_semantic_checks(case_path: Path) -> List[Dict[str, str]]:
    """Collect semantic checks for blockMesh cyclic patch pairing."""
    checks: List[Dict[str, str]] = []
    block_mesh = case_path / "system" / "blockMeshDict"
    if not block_mesh.exists():
        return checks

    try:
        content = block_mesh.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return checks

    section = _extract_boundary_section(content)
    if section is None:
        checks.append(
            {
                "severity": "error",
                "title": "blockMesh 边界定义",
                "message": "system/blockMeshDict 缺少 boundary(...) 段，无法生成网格。",
                "suggestion": "补全 blockMeshDict 的 boundary 列表并重新运行 blockMesh。",
            }
        )
        return checks

    cyclic_pairs: Dict[str, str] = {}
    for patch_name, patch_body in _iter_patch_blocks(section):
        patch_type = (_extract_patch_value(patch_body, "type") or "").lower()
        if patch_type != "cyclic":
            continue
        neighbour = _extract_patch_value(patch_body, "neighbourPatch")
        if not neighbour:
            checks.append(
                {
                    "severity": "error",
                    "title": f"blockMesh cyclic patch `{patch_name}`",
                    "message": "缺少 neighbourPatch，blockMesh 会在构建 cyclic 边界时报错。",
                    "suggestion": f"为 `{patch_name}` 添加 `neighbourPatch` 并保证配对对称。",
                }
            )
            continue
        cyclic_pairs[patch_name] = neighbour

    for patch_name, neighbour in cyclic_pairs.items():
        neighbour_target = cyclic_pairs.get(neighbour)
        if neighbour_target is None:
            checks.append(
                {
                    "severity": "error",
                    "title": f"blockMesh cyclic patch `{patch_name}`",
                    "message": f"`neighbourPatch {neighbour}` 未定义为 cyclic patch。",
                    "suggestion": "检查 cyclic patch 命名并确保两端都设置 `type cyclic`。",
                }
            )
        elif neighbour_target != patch_name:
            checks.append(
                {
                    "severity": "error",
                    "title": f"blockMesh cyclic patch `{patch_name}`",
                    "message": f"配对不对称：`{patch_name} -> {neighbour}`，`{neighbour} -> {neighbour_target}`。",
                    "suggestion": "将两个 patch 的 neighbourPatch 互相指向对方。",
                }
            )

    return checks


def _add_check(
    checks: List[Dict[str, str]],
    severity: str,
    title: str,
    message: str,
    suggestion: Optional[str] = None,
):
    """Append a structured check result."""
    entry = {
        "severity": severity,
        "title": title,
        "message": message,
    }
    if suggestion:
        entry["suggestion"] = suggestion
    checks.append(entry)


def _upsert_top_level_entry(content: str, key: str, value: str) -> str:
    """Insert or update a top-level OpenFOAM dictionary entry."""
    pattern = rf"(^\s*{re.escape(key)}\b\s+)([^;]+)(;)"
    if re.search(pattern, content, re.MULTILINE):
        return re.sub(pattern, rf"\g<1>{value}\g<3>", content, count=1, flags=re.MULTILINE)

    marker = "// ************************************************************************* //"
    entry = f"\n{key} {value};\n"
    if marker in content:
        return content.replace(marker, entry + "\n" + marker, 1)
    return content + entry


def _find_block_span(content: str, block_name: str) -> Optional[Tuple[int, int]]:
    """Return '{' and matching '}' indices for a named block."""
    start_match = re.search(rf"\b{re.escape(block_name)}\b\s*\{{", content)
    if not start_match:
        return None

    brace_start = start_match.end() - 1
    depth = 0
    for idx in range(brace_start, len(content)):
        ch = content[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return brace_start, idx

    return None


def _append_block(content: str, block_name: str, entries: List[str]) -> str:
    """Append a dictionary block before the footer marker."""
    lines = ["", block_name, "{"] + [f"    {entry}" for entry in entries] + ["}", ""]
    block_text = "\n".join(lines)
    marker = "// ************************************************************************* //"
    if marker in content:
        return content.replace(marker, block_text + marker, 1)
    return content + block_text


def _upsert_block_entry(
    content: str,
    block_name: str,
    key: str,
    value: str,
    default_entries: Optional[List[str]] = None,
) -> str:
    """Insert or update key in a named dictionary block."""
    span = _find_block_span(content, block_name)
    if not span:
        entries = default_entries[:] if default_entries else [f"{key} {value};"]
        if not any(entry.strip().startswith(f"{key} ") for entry in entries):
            entries.append(f"{key} {value};")
        return _append_block(content, block_name, entries)

    block_start, block_end = span
    body = content[block_start + 1 : block_end]
    pattern = rf"(^\s*{re.escape(key)}\b\s+)([^;]+)(;)"
    if re.search(pattern, body, re.MULTILINE):
        new_body = re.sub(pattern, rf"\g<1>{value}\g<3>", body, count=1, flags=re.MULTILINE)
    else:
        if body.endswith("\n"):
            new_body = body + f"    {key} {value};\n"
        else:
            new_body = body + f"\n    {key} {value};\n"

    return content[: block_start + 1] + new_body + content[block_end:]


def openfoam_preflight_check(params: PreflightCheckInput) -> str:
    """
    Run environment and case-level preflight checks before OpenFOAM execution.
    """
    checks: List[Dict[str, str]] = []

    env_items = [
        ("WM_PROJECT_DIR", True),
        ("WM_PROJECT_VERSION", True),
        ("FOAM_APPBIN", False),
        ("FOAM_TUTORIALS", False),
    ]
    env_checks: List[Dict[str, str]] = []
    for key, required in env_items:
        value = os.environ.get(key)
        if value:
            env_checks.append({"key": key, "status": "ok", "value": value})
        else:
            missing_as_error = bool(params.strict and required)
            env_checks.append(
                {
                    "key": key,
                    "status": "error" if missing_as_error else "warning",
                    "value": "",
                }
            )

    solver = params.solver
    control_text: Optional[str] = None
    case_path_obj: Optional[Path] = None
    if params.case_path:
        case_path_obj = Path(params.case_path)
        control_dict = case_path_obj / "system" / "controlDict"
        if control_dict.exists():
            try:
                control_text = control_dict.read_text(encoding="utf-8", errors="replace")
            except Exception:
                control_text = None
        if not solver and control_text:
            solver = _extract_application(control_text)

    commands = ["blockMesh", "checkMesh", "decomposePar", "reconstructPar", "mpirun"]
    if solver:
        commands.append(solver)

    required_commands = set()
    if params.profile == "mesh":
        required_commands.update({"blockMesh", "checkMesh"})
    elif params.profile == "solver":
        if solver:
            required_commands.add(solver)
    elif params.profile == "parallel":
        required_commands.update({"decomposePar", "mpirun", "reconstructPar"})
        if solver:
            required_commands.add(solver)

    if params.strict:
        required_commands.update(commands)

    # Deduplicate while preserving display order.
    deduped_commands = list(dict.fromkeys(commands))

    command_checks = []
    for cmd in deduped_commands:
        path = which(cmd)
        required = cmd in required_commands
        runtime_issue: Optional[str] = None
        if path and cmd in {"decomposePar", "mpirun"} and (params.profile == "parallel" or params.n_processors):
            runtime_issue = _probe_command_runtime_issue(cmd, path)

        status = "ok" if path else ("error" if required else "warning")
        detail = path or ""
        if runtime_issue:
            status = "error" if required else "warning"
            detail = runtime_issue

        command_checks.append(
            {
                "command": cmd,
                "status": status,
                "path": path or "",
                "detail": detail,
                "required": "yes" if required else "no",
            }
        )

    case_checks: List[Dict[str, str]] = []
    if case_path_obj is not None:
        if not case_path_obj.exists():
            case_checks.append(
                {"name": "case_path", "status": "error", "message": "案例目录不存在"}
            )
        elif not case_path_obj.is_dir():
            case_checks.append(
                {"name": "case_path", "status": "error", "message": "case_path 不是目录"}
            )
        else:
            for rel in ["0", "constant", "system"]:
                p = case_path_obj / rel
                case_checks.append(
                    {
                        "name": rel,
                        "status": "ok" if p.is_dir() else "error",
                        "message": "目录存在" if p.is_dir() else "缺少目录",
                    }
                )
            for rel in ["system/controlDict", "system/fvSchemes", "system/fvSolution"]:
                p = case_path_obj / rel
                case_checks.append(
                    {
                        "name": rel,
                        "status": "ok" if p.is_file() else "error",
                        "message": "文件存在" if p.is_file() else "缺少文件",
                    }
                )
    compatibility_checks = _collect_solver_compatibility_checks(solver, control_text, case_path_obj)
    geometry_checks: List[Dict[str, str]] = []
    if case_path_obj is not None and case_path_obj.exists() and case_path_obj.is_dir():
        geometry_checks = _collect_block_mesh_semantic_checks(case_path_obj)

    # Aggregate counts
    for item in env_checks:
        if item["status"] == "error":
            _add_check(checks, "error", f"环境变量 {item['key']}", "未设置")
        elif item["status"] == "warning":
            _add_check(checks, "warning", f"环境变量 {item['key']}", "未设置")
        else:
            _add_check(checks, "ok", f"环境变量 {item['key']}", "已设置")

    for item in command_checks:
        if item["status"] == "error":
            _add_check(
                checks,
                "error",
                f"命令 {item['command']}",
                item.get("detail") or "不可用",
                suggestion="请先 source OpenFOAM 环境配置（etc/bashrc）",
            )
        elif item["status"] == "warning":
            _add_check(
                checks,
                "warning",
                f"命令 {item['command']}",
                item.get("detail") or "当前场景非阻塞缺失",
                suggestion="若要执行相关阶段，请先 source OpenFOAM 环境配置（etc/bashrc）",
            )
        else:
            _add_check(checks, "ok", f"命令 {item['command']}", "可用")

    for item in case_checks:
        severity = "ok" if item["status"] == "ok" else "error"
        _add_check(checks, severity, f"案例项 {item['name']}", item["message"])
    for item in compatibility_checks:
        _add_check(
            checks,
            item["severity"],
            item["title"],
            item["message"],
            suggestion=item.get("suggestion"),
        )
    for item in geometry_checks:
        _add_check(
            checks,
            item["severity"],
            item["title"],
            item["message"],
            suggestion=item.get("suggestion"),
        )

    errors = sum(1 for c in checks if c["severity"] == "error")
    warnings = sum(1 for c in checks if c["severity"] == "warning")
    oks = sum(1 for c in checks if c["severity"] == "ok")
    overall = "ready"
    if errors > 0:
        overall = "blocked"
    elif warnings > 0:
        overall = "degraded"

    payload = {
        "profile": params.profile,
        "strict": params.strict,
        "case_path": params.case_path,
        "solver": solver,
        "summary": {
            "overall": overall,
            "errors": errors,
            "warnings": warnings,
            "passed": oks,
        },
        "env_checks": env_checks,
        "command_checks": command_checks,
        "case_checks": case_checks,
        "compatibility_checks": compatibility_checks,
        "geometry_checks": geometry_checks,
        "checks": checks,
    }

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    lines = [
        "# OpenFOAM 运行前检查",
        "",
        f"- 场景: {params.profile}",
        f"- 总体状态: {overall}",
        f"- 错误: {errors}",
        f"- 警告: {warnings}",
        f"- 通过: {oks}",
        "",
        "## 环境变量检查",
    ]
    for item in env_checks:
        icon = "✅" if item["status"] == "ok" else ("❌" if item["status"] == "error" else "⚠️")
        value = item["value"] if item["value"] else "(未设置)"
        lines.append(f"- {icon} `{item['key']}`: {value}")

    lines.append("")
    lines.append("## 命令检查")
    for item in command_checks:
        icon = "✅" if item["status"] == "ok" else ("❌" if item["status"] == "error" else "⚠️")
        detail = item.get("detail") or (item["path"] if item["path"] else "未找到可执行文件")
        marker = " (必需)" if item["required"] == "yes" else ""
        lines.append(f"- {icon} `{item['command']}`{marker}: {detail}")

    lines.append("")
    lines.append("## 案例结构检查")
    if case_checks:
        for item in case_checks:
            icon = "✅" if item["status"] == "ok" else "❌"
            lines.append(f"- {icon} `{item['name']}`: {item['message']}")
    else:
        lines.append("- ℹ️ 未提供 case_path，已跳过案例结构检查")

    if compatibility_checks:
        lines.append("")
        lines.append("## 兼容性检查")
        for item in compatibility_checks:
            icon = "✅" if item["severity"] == "ok" else ("❌" if item["severity"] == "error" else "⚠️")
            lines.append(f"- {icon} **{item['title']}**: {item['message']}")
            if item.get("suggestion"):
                lines.append(f"  建议: {item['suggestion']}")

    if geometry_checks:
        lines.append("")
        lines.append("## 几何与网格语义检查")
        for item in geometry_checks:
            icon = "✅" if item["severity"] == "ok" else ("❌" if item["severity"] == "error" else "⚠️")
            lines.append(f"- {icon} **{item['title']}**: {item['message']}")
            if item.get("suggestion"):
                lines.append(f"  建议: {item['suggestion']}")

    if errors:
        lines.append("")
        lines.append("## 建议")
        lines.append("- 先修复所有 ❌ 阻塞项，再执行对应阶段命令。")
    elif warnings:
        lines.append("")
        lines.append("## 建议")
        lines.append("- 当前仅存在 ⚠️ 告警项，可继续诊断；执行网格/求解前建议先处理。")

    return "\n".join(lines)


def openfoam_assess_case_stability(params: AssessCaseStabilityInput) -> str:
    """
    Assess numerical-stability settings from controlDict and fvSolution.
    """
    case_path = Path(params.case_path)
    control_path = case_path / "system" / "controlDict"
    fv_solution_path = case_path / "system" / "fvSolution"

    if not case_path.exists() or not case_path.is_dir():
        return f"错误: 案例路径不存在或不是目录: {params.case_path}"
    if not control_path.exists():
        return f"错误: 缺少文件: {control_path}"
    if not fv_solution_path.exists():
        return f"错误: 缺少文件: {fv_solution_path}"

    control_text = control_path.read_text(encoding="utf-8", errors="replace")
    fv_solution_text = fv_solution_path.read_text(encoding="utf-8", errors="replace")

    solver = _extract_application(control_text) or "unknown"
    solver_lower = solver.lower()

    checks: List[Dict[str, str]] = []
    compatibility_checks = _collect_solver_compatibility_checks(solver, control_text, case_path)
    for item in compatibility_checks:
        _add_check(
            checks,
            item["severity"],
            item["title"],
            item["message"],
            suggestion=item.get("suggestion"),
        )

    delta_t = _extract_scalar_value(control_text, "deltaT")
    if delta_t is None:
        _add_check(checks, "warning", "deltaT", "未设置 deltaT")
    elif delta_t <= 0:
        _add_check(
            checks,
            "error",
            "deltaT",
            f"deltaT={delta_t}（必须 > 0）",
            suggestion="设置正的时间步长",
        )
    else:
        _add_check(checks, "ok", "deltaT", f"deltaT={delta_t}")

    is_transient = solver_lower in _TRANSIENT_SOLVERS
    is_steady = solver_lower in _STEADY_SOLVERS

    if is_transient:
        adjust_time_step = (_extract_word_value(control_text, "adjustTimeStep") or "").lower()
        if adjust_time_step != "yes":
            _add_check(
                checks,
                "warning",
                "adjustTimeStep",
                "瞬态求解器建议启用 adjustTimeStep yes",
                suggestion="在 controlDict 中设置 adjustTimeStep yes;",
            )
        else:
            _add_check(checks, "ok", "adjustTimeStep", "已启用")

        max_co = _extract_scalar_value(control_text, "maxCo")
        if max_co is None:
            _add_check(
                checks,
                "warning",
                "maxCo",
                "未设置 maxCo",
                suggestion="建议设置 maxCo ≤ 1（VOF 常用更小值）",
            )
        elif max_co > 1.0 + _FLOAT_COMPARE_EPS:
            _add_check(
                checks,
                "warning",
                "maxCo",
                f"maxCo={max_co}，可能偏大",
                suggestion="建议将 maxCo 调低到 1 或更小",
            )
        else:
            _add_check(checks, "ok", "maxCo", f"maxCo={max_co}")

        if "interfoam" in solver_lower:
            max_alpha_co = _extract_scalar_value(control_text, "maxAlphaCo")
            if max_alpha_co is None:
                _add_check(
                    checks,
                    "warning",
                    "maxAlphaCo",
                    "interFoam 建议设置 maxAlphaCo",
                    suggestion="建议设置 maxAlphaCo ≤ 1 以控制界面推进稳定性",
                )
            elif max_alpha_co > 1.0 + _FLOAT_COMPARE_EPS:
                _add_check(
                    checks,
                    "warning",
                    "maxAlphaCo",
                    f"maxAlphaCo={max_alpha_co}，可能偏大",
                    suggestion="建议调低到 1 或更小",
                )
            else:
                _add_check(checks, "ok", "maxAlphaCo", f"maxAlphaCo={max_alpha_co}")

    if is_steady:
        relax_block = _extract_block(fv_solution_text, "relaxationFactors")
        if not relax_block:
            _add_check(
                checks,
                "warning",
                "relaxationFactors",
                "稳态求解器建议设置 relaxationFactors",
                suggestion="在 fvSolution 中添加 relaxationFactors 以提升收敛稳定性",
            )
        else:
            factors = re.findall(
                r"\b([A-Za-z][A-Za-z0-9_]*)\s+([-+]?[\d.]+(?:[eE][-+]?\d+)?)\s*;",
                relax_block,
            )
            if not factors:
                _add_check(checks, "warning", "relaxationFactors", "未解析到松弛因子")
            else:
                invalid = []
                for name, raw in factors:
                    try:
                        value = float(raw)
                    except ValueError:
                        invalid.append(f"{name}={raw}")
                        continue
                    if value <= 0 or value > 1:
                        invalid.append(f"{name}={value}")
                if invalid:
                    _add_check(
                        checks,
                        "error",
                        "relaxationFactors",
                        "存在超出 (0,1] 的松弛因子",
                        suggestion=f"检查: {', '.join(invalid)}",
                    )
                else:
                    _add_check(checks, "ok", "relaxationFactors", "范围检查通过")

    algorithm_block = (
        _extract_block(fv_solution_text, "SIMPLE")
        or _extract_block(fv_solution_text, "PIMPLE")
        or _extract_block(fv_solution_text, "PISO")
    )
    if not algorithm_block:
        _add_check(
            checks,
            "warning",
            "SIMPLE/PIMPLE/PISO",
            "未找到主算法块，无法检查 nNonOrthogonalCorrectors",
        )
    else:
        n_non_orth = _extract_scalar_value(algorithm_block, "nNonOrthogonalCorrectors")
        if n_non_orth is None:
            _add_check(
                checks,
                "warning",
                "nNonOrthogonalCorrectors",
                "未显式设置 nNonOrthogonalCorrectors",
                suggestion="建议按网格非正交性显式配置该参数",
            )
        elif n_non_orth < 0:
            _add_check(
                checks,
                "error",
                "nNonOrthogonalCorrectors",
                f"nNonOrthogonalCorrectors={n_non_orth}（必须 >= 0）",
            )
        else:
            _add_check(checks, "ok", "nNonOrthogonalCorrectors", f"{n_non_orth}")

    errors = sum(1 for c in checks if c["severity"] == "error")
    warnings = sum(1 for c in checks if c["severity"] == "warning")

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(
            {
                "case_path": str(case_path),
                "solver": solver,
                "high_risk": errors,
                "warnings": warnings,
                "checks": checks,
            },
            indent=2,
            ensure_ascii=False,
        )

    lines = [
        "# 数值稳定性评估",
        "",
        f"- 案例: `{case_path}`",
        f"- 求解器: `{solver}`",
        f"- 高风险项: {errors}",
        f"- 警告项: {warnings}",
        "",
        "## 检查结果",
    ]

    for check in checks:
        if check["severity"] == "ok":
            icon = "✅"
        elif check["severity"] == "warning":
            icon = "⚠️"
        else:
            icon = "❌"

        lines.append(f"- {icon} **{check['title']}**: {check['message']}")
        if "suggestion" in check:
            lines.append(f"  建议: {check['suggestion']}")

    lines.append("")
    lines.append("## 参考基线")
    lines.append("- 瞬态算例通常启用 `adjustTimeStep` 并控制 `maxCo`。")
    lines.append("- VOF（如 interFoam）通常还应设置 `maxAlphaCo`。")
    lines.append("- 稳态算例通常通过 `relaxationFactors` 提升收敛稳定性。")
    lines.append("- `nNonOrthogonalCorrectors` 建议按网格质量显式设置。")

    return "\n".join(lines)


def openfoam_apply_stability_fixes(params: ApplyStabilityFixesInput) -> str:
    """
    Apply conservative numerical-stability fixes to controlDict and fvSolution.
    """
    case_path = Path(params.case_path)
    control_path = case_path / "system" / "controlDict"
    fv_solution_path = case_path / "system" / "fvSolution"

    if not case_path.exists() or not case_path.is_dir():
        return f"错误: 案例路径不存在或不是目录: {params.case_path}"
    if not control_path.exists():
        return f"错误: 缺少文件: {control_path}"
    if not fv_solution_path.exists():
        return f"错误: 缺少文件: {fv_solution_path}"

    control_original = control_path.read_text(encoding="utf-8", errors="replace")
    fv_solution_original = fv_solution_path.read_text(encoding="utf-8", errors="replace")
    control_text = control_original
    fv_solution_text = fv_solution_original

    changes: List[str] = []
    warnings: List[str] = []

    solver = _extract_application(control_text) or "unknown"
    solver_lower = solver.lower()
    is_transient = solver_lower in _TRANSIENT_SOLVERS
    is_steady = solver_lower in _STEADY_SOLVERS

    co_limit = 0.5 if params.strategy == "conservative" else 1.0
    alpha_co_limit = 0.5 if params.strategy == "conservative" else 1.0

    delta_t = _extract_scalar_value(control_text, "deltaT")
    if delta_t is None or delta_t <= 0:
        delta_t_value = "1e-4" if is_transient else "1"
        new_text = _upsert_top_level_entry(control_text, "deltaT", delta_t_value)
        if new_text != control_text:
            changes.append(f"controlDict: 设置 deltaT={delta_t_value}")
            control_text = new_text

    if is_transient:
        adjust_time_step = (_extract_word_value(control_text, "adjustTimeStep") or "").lower()
        if adjust_time_step != "yes":
            new_text = _upsert_top_level_entry(control_text, "adjustTimeStep", "yes")
            if new_text != control_text:
                changes.append("controlDict: 设置 adjustTimeStep yes")
                control_text = new_text

        max_co = _extract_scalar_value(control_text, "maxCo")
        if max_co is None or max_co > co_limit:
            new_text = _upsert_top_level_entry(control_text, "maxCo", f"{co_limit:g}")
            if new_text != control_text:
                changes.append(f"controlDict: 设置 maxCo={co_limit:g}")
                control_text = new_text

        if "interfoam" in solver_lower:
            max_alpha_co = _extract_scalar_value(control_text, "maxAlphaCo")
            if max_alpha_co is None or max_alpha_co > alpha_co_limit:
                new_text = _upsert_top_level_entry(control_text, "maxAlphaCo", f"{alpha_co_limit:g}")
                if new_text != control_text:
                    changes.append(f"controlDict: 设置 maxAlphaCo={alpha_co_limit:g}")
                    control_text = new_text

        algorithm_block_name = "PIMPLE"
        if _find_block_span(fv_solution_text, "PISO"):
            algorithm_block_name = "PISO"
        elif _find_block_span(fv_solution_text, "PIMPLE"):
            algorithm_block_name = "PIMPLE"

        default_entries = (
            ["nOuterCorrectors 1;", "nCorrectors 2;", "nNonOrthogonalCorrectors 1;"]
            if algorithm_block_name == "PIMPLE"
            else ["nCorrectors 2;", "nNonOrthogonalCorrectors 1;"]
        )
        new_fv = _upsert_block_entry(
            fv_solution_text,
            algorithm_block_name,
            "nNonOrthogonalCorrectors",
            "1",
            default_entries=default_entries,
        )
        if new_fv != fv_solution_text:
            changes.append(f"fvSolution: 设置 {algorithm_block_name}.nNonOrthogonalCorrectors=1")
            fv_solution_text = new_fv

    elif is_steady:
        new_fv = _upsert_block_entry(
            fv_solution_text,
            "SIMPLE",
            "nNonOrthogonalCorrectors",
            "0",
            default_entries=["nNonOrthogonalCorrectors 0;"],
        )
        if new_fv != fv_solution_text:
            changes.append("fvSolution: 设置 SIMPLE.nNonOrthogonalCorrectors=0")
            fv_solution_text = new_fv

        if not _find_block_span(fv_solution_text, "relaxationFactors"):
            fv_solution_text = _append_block(
                fv_solution_text,
                "relaxationFactors",
                [
                    "fields",
                    "{",
                    "    p 0.3;",
                    "}",
                    "equations",
                    "{",
                    "    U 0.7;",
                    "    k 0.7;",
                    "    epsilon 0.7;",
                    "    omega 0.7;",
                    "}",
                ],
            )
            changes.append("fvSolution: 添加 relaxationFactors（稳态默认值）")
    else:
        warnings.append("未识别求解器类型，已仅执行通用修复。")

    updated = (control_text != control_original) or (fv_solution_text != fv_solution_original)
    if control_text != control_original:
        atomic_write_text(control_path, control_text)
    if fv_solution_text != fv_solution_original:
        atomic_write_text(fv_solution_path, fv_solution_text)

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(
            {
                "case_path": str(case_path),
                "solver": solver,
                "strategy": params.strategy,
                "updated": updated,
                "changes": changes,
                "warnings": warnings,
            },
            ensure_ascii=False,
            indent=2,
        )

    lines = [
        "# 稳定性自动修复结果",
        "",
        f"- 案例: `{case_path}`",
        f"- 求解器: `{solver}`",
        f"- 策略: `{params.strategy}`",
        f"- 是否更新文件: {'是' if updated else '否'}",
        "",
        "## 变更项",
    ]
    if changes:
        for item in changes:
            lines.append(f"- ✅ {item}")
    else:
        lines.append("- ℹ️ 未检测到需要修改的项")

    if warnings:
        lines.append("")
        lines.append("## 提示")
        for item in warnings:
            lines.append(f"- ⚠️ {item}")

    return "\n".join(lines)
