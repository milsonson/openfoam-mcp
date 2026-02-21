"""Path safety helpers for MCP tools."""

import os
from pathlib import Path

from ..io_utils import atomic_write_text, resolve_openfoam_command


def resolve_within_root(root: Path, relative_path: str, parameter_name: str) -> Path:
    """
    Resolve a relative path within a trusted root directory.

    Args:
        root: Trusted root directory
        relative_path: User-provided relative path
        parameter_name: Input parameter name for error messages

    Returns:
        Resolved absolute path guaranteed to stay within root

    Raises:
        ValueError: If the provided path is absolute or escapes root
    """
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError(f"{parameter_name} 必须是案例目录内的相对路径")

    root_resolved = root.resolve()
    target_resolved = (root_resolved / candidate).resolve()

    try:
        target_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"{parameter_name} 必须位于案例目录内") from exc

    return target_resolved


def ensure_within_root(root: Path, target: Path, parameter_name: str) -> Path:
    """
    Ensure an absolute target path stays within a trusted root directory.

    Args:
        root: Trusted root directory
        target: Path to verify
        parameter_name: Input parameter name for error messages

    Returns:
        Resolved target path

    Raises:
        ValueError: If target escapes root
    """
    root_resolved = root.resolve()
    target_resolved = target.resolve()

    try:
        target_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"{parameter_name} 必须位于 $FOAM_TUTORIALS 目录内") from exc

    return target_resolved


def get_allowed_case_roots() -> list[Path]:
    """Resolve writable root allowlist for case operations."""
    env_value = os.environ.get("OPENFOAM_MCP_ALLOWED_CASE_ROOTS", "").strip()
    if env_value:
        roots = [
            Path(item).expanduser().resolve()
            for item in env_value.split(":")
            if item.strip()
        ]
        if roots:
            return roots

    return [
        Path.home().resolve(),
        Path("/tmp"),
    ]


def validate_allowed_case_path(case_path: str) -> str:
    """Validate case_path is absolute and constrained to configured safe roots."""
    raw = Path(case_path).expanduser()
    if not raw.is_absolute():
        raise ValueError("案例路径必须是绝对路径（以 / 开头）")

    resolved = raw.resolve()
    allowed_roots = get_allowed_case_roots()
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return str(resolved)
        except ValueError:
            continue

    allowed_text = ", ".join(str(root) for root in allowed_roots)
    raise ValueError(f"案例路径不在允许范围内。允许根目录: {allowed_text}")
