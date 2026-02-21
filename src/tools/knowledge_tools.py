import os
import stat
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict

from .path_utils import ensure_within_root, resolve_within_root

# ============================================================================ 
# Input Models
# ============================================================================ 

class SearchTutorialsInput(BaseModel):
    """Input for searching OpenFOAM tutorials."""
    model_config = ConfigDict(str_strip_whitespace=True)

    keywords: List[str] = Field(
        ...,
        description="搜索关键字列表，如 ['simpleFoam', 'motorBike']",
        min_length=1
    )
    max_results: int = Field(
        default=10,
        description="返回的最大结果数量",
        ge=1,
        le=50
    )


class ReadTutorialFileInput(BaseModel):
    """Input for reading a tutorial file."""
    model_config = ConfigDict(str_strip_whitespace=True)

    tutorial_path: str = Field(
        ...,
        description="教程案例的路径（通常由 search_tutorials 返回），可以是相对路径或绝对路径",
        min_length=1
    )
    file_path: str = Field(
        ...,
        description="要读取的文件相对路径，如 'system/controlDict', '0/U'",
        min_length=1
    )


# ============================================================================ 
# Tool Implementations
# ============================================================================ 

def _get_tutorials_dir() -> Optional[Path]:
    """
    Get the OpenFOAM tutorials directory from environment variables or common locations.
    """
    # 1. Try environment variable
    tutorials_env = os.environ.get("FOAM_TUTORIALS")
    if tutorials_env and os.path.isdir(tutorials_env):
        return Path(tutorials_env)

    # 2. Try common installation paths (fallback)
    common_paths = [
        Path("/opt/openfoam/tutorials"),
        Path("/usr/lib/openfoam/tutorials"),
        Path("/usr/local/lib/openfoam/tutorials"),
    ]
    
    # Try to find versioned directories too
    base_paths = [Path("/opt"), Path("/usr/lib"), Path("/usr/local/lib")]
    for base in base_paths:
        if base.exists():
            for p in base.glob("openfoam*/tutorials"):
                if p.is_dir():
                    return p

    return None


def _resolve_tutorial_case_path(tutorial_path: str, tutorials_dir: Optional[Path]) -> Path:
    """
    Resolve tutorial case path and enforce FOAM_TUTORIALS boundary.
    """
    if not tutorials_dir:
        raise ValueError("未找到 OpenFOAM 教程目录 ($FOAM_TUTORIALS)。")

    raw_path = Path(tutorial_path)
    if raw_path.is_absolute():
        case_path = ensure_within_root(tutorials_dir, raw_path, "tutorial_path")
    else:
        case_path = ensure_within_root(tutorials_dir, tutorials_dir / raw_path, "tutorial_path")

    if not (case_path / "system" / "controlDict").exists():
        raise ValueError(
            f"目录不是有效教程案例（缺少 system/controlDict）: {case_path}"
        )

    return case_path


def openfoam_search_tutorials(params: SearchTutorialsInput) -> str:
    """
    搜索 OpenFOAM 官方教程案例。
    
    Args:
        params: 包含关键字和最大结果数的输入参数
        
    Returns:
        搜索结果的 Markdown 格式字符串
    """
    tutorials_dir = _get_tutorials_dir()
    
    if not tutorials_dir:
        return (
            "错误: 未找到 OpenFOAM 教程目录 ($FOAM_TUTORIALS)。\n"
            "请确保已安装 OpenFOAM 并加载了环境配置 (例如: `source /opt/openfoam*/etc/bashrc`)。"
        )
        
    found_cases = []
    keywords_lower = [k.lower() for k in params.keywords]
    
    try:
        # Walk through the tutorials directory (topdown=True allows pruning).
        for root, dirs, files in os.walk(tutorials_dir, topdown=True):
            if len(found_cases) >= params.max_results:
                dirs.clear()
                break

            # Check if this directory looks like a case (has system/controlDict)
            root_path = Path(root)
            if (root_path / "system" / "controlDict").exists():
                
                # Check match
                # 1. Path contains keywords
                rel_path = str(root_path.relative_to(tutorials_dir))
                path_match = any(k in rel_path.lower() for k in keywords_lower)
                
                # 2. Solver name (usually parent directory) match
                solver_name = root_path.parent.name
                solver_match = any(k in solver_name.lower() for k in keywords_lower)
                
                if path_match or solver_match:
                    found_cases.append({
                        "path": str(root_path),
                        "rel_path": rel_path,
                        "solver": solver_name,
                        "description": _get_case_description(root_path)
                    })
                    if len(found_cases) >= params.max_results:
                        dirs.clear()
                        break
                # A case directory often has nested files/dirs; prune to reduce walk cost.
                dirs.clear()
                
    except Exception as e:
        return f"搜索教程时发生错误: {str(e)}"
        
    # Format output
    if not found_cases:
        return f"在 `$FOAM_TUTORIALS` 中未找到匹配关键字 {params.keywords} 的案例。"
        
    lines = [f"# OpenFOAM 教程搜索结果 ({len(found_cases)})", ""]
    lines.append(f"**搜索关键字**: {', '.join(params.keywords)}")
    lines.append("")
    
    for case in found_cases:
        lines.append(f"### {case['rel_path']}")
        lines.append(f"- **完整路径**: `{case['path']}`")
        lines.append(f"- **求解器**: `{case['solver']}`")
        if case['description']:
            lines.append(f"- **描述**: {case['description']}")
        lines.append("")
        
    lines.append("---")
    lines.append("使用 `openfoam_read_tutorial_file` 读取这些案例的配置文件。")
    
    return "\n".join(lines)


def _get_case_description(case_path: Path) -> str:
    """Try to extract a description from Allrun or README."""
    # Try README first
    readme = case_path / "README"
    if not readme.exists():
        readme = case_path / "README.md"
        
    if readme.exists():
        try:
            with open(readme, 'r', encoding='utf-8', errors='replace') as f:
                # Return first non-empty line
                for line in f:
                    if line.strip() and not line.startswith("#"):
                        return line.strip()[:100] + "..."
        except Exception:
            pass
            
    return ""


def openfoam_read_tutorial_file(params: ReadTutorialFileInput) -> str:
    """
    读取指定官方教程案例的配置文件内容。
    
    Args:
        params: 包含案例路径和文件名的输入参数
        
    Returns:
        文件内容
    """
    tutorials_dir = _get_tutorials_dir()

    try:
        case_path = _resolve_tutorial_case_path(params.tutorial_path, tutorials_dir)
        file_path = resolve_within_root(case_path, params.file_path, "file_path")
    except ValueError as e:
        return (
            f"错误: {str(e)}\n"
            "请使用 `openfoam_search_tutorials` 获取有效教程路径（位于 $FOAM_TUTORIALS 内）。"
        )

    fd: Optional[int] = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(file_path, flags)
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            return f"错误: 仅支持读取常规文件: {file_path}"
        if file_stat.st_size > 100 * 1024:  # 100KB limit
            return f"错误: 文件过大 ({file_stat.st_size} bytes)。仅支持读取配置文件。"

        with os.fdopen(fd, 'r', encoding='utf-8', errors='replace') as f:
            fd = None
            content = f.read()

        lines = [
            f"# 文件内容: {params.file_path}",
            f"**来源案例**: {case_path}",
            "",
            "```openfoam",
            content,
            "```"
        ]
        return "\n".join(lines)

    except FileNotFoundError:
        return f"错误: 文件不存在: {file_path}"
    except Exception as e:
        return f"读取文件时发生错误: {str(e)}"
    finally:
        if fd is not None:
            os.close(fd)
