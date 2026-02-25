import re
from pathlib import Path
from typing import Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator

from .path_utils import atomic_write_text, resolve_within_root, validate_allowed_case_path


_SAFE_DICT_VALUE_RE = re.compile(r"^[^\r\n{};]+$")
MAX_READ_DICTIONARY_BYTES = 2 * 1024 * 1024

# ============================================================================ 
# Input Models
# ============================================================================ 

class GetPatchListInput(BaseModel):
    """Input for getting patch list from a case."""
    model_config = ConfigDict(str_strip_whitespace=True)

    case_path: str = Field(..., description="OpenFOAM 案例路径")

    @field_validator("case_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_allowed_case_path(value)


class ReadDictionaryInput(BaseModel):
    """Input for reading an OpenFOAM dictionary."""
    model_config = ConfigDict(str_strip_whitespace=True)

    case_path: str = Field(..., description="OpenFOAM 案例路径")
    dict_path: str = Field(..., description="字典文件相对路径，如 'system/controlDict'")

    @field_validator("case_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_allowed_case_path(value)


class UpdateDictionaryInput(BaseModel):
    """Input for updating an OpenFOAM dictionary."""
    model_config = ConfigDict(str_strip_whitespace=True)

    case_path: str = Field(..., description="OpenFOAM 案例路径")
    dict_path: str = Field(..., description="字典文件相对路径，如 'system/controlDict'")
    updates: Dict[str, Any] = Field(..., description="要更新的键值对，支持嵌套结构（暂不支持过于复杂的嵌套）")

    @field_validator("case_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_allowed_case_path(value)


def _normalize_dictionary_value_for_update(key: str, value: Any) -> str:
    """Normalize update value and reject multiline content to protect dictionary structure."""
    if isinstance(value, (list, tuple)):
        value_text = "(" + " ".join(map(str, value)) + ")"
    else:
        value_text = str(value)
    if "\n" in value_text or "\r" in value_text:
        raise ValueError(f"更新值包含换行符，已拒绝: {key}")
    if not _SAFE_DICT_VALUE_RE.fullmatch(value_text):
        raise ValueError(f"更新值包含非法字符（花括号/分号），已拒绝: {key}")
    return value_text


def _extract_parenthesized_section(content: str, section_name: str) -> str | None:
    """Extract a named section with balanced parentheses, e.g. boundary(...)."""
    section_match = re.search(rf"\b{re.escape(section_name)}\b", content)
    if not section_match:
        return None

    start = content.find("(", section_match.end())
    if start == -1:
        return None

    depth = 0
    for idx in range(start, len(content)):
        ch = content[idx]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return content[start + 1 : idx]
    return None


def _extract_parenthesized_from_index(content: str, start: int) -> str | None:
    """Extract balanced parenthesized content from known '(' index."""
    if start < 0 or start >= len(content) or content[start] != "(":
        return None

    depth = 0
    for idx in range(start, len(content)):
        ch = content[idx]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return content[start + 1 : idx]
    return None


def _line_code_without_comment(line: str) -> str:
    """Strip inline // comments for lightweight structural parsing."""
    return line.split("//", 1)[0]


# ============================================================================ 
# Tool Implementations
# ============================================================================ 

def openfoam_get_patch_list(params: GetPatchListInput) -> str:
    """
    获取 OpenFOAM 案例中定义的边界（Patches）列表。
    
    会尝试从 polyMesh/boundary (优先) 或 system/blockMeshDict 中解析。
    """
    case_path = Path(params.case_path)
    boundary_file = case_path / "constant" / "polyMesh" / "boundary"
    blockmesh_file = case_path / "system" / "blockMeshDict"
    
    patches = []
    source = ""
    
    if boundary_file.exists():
        source = "constant/polyMesh/boundary"
        try:
            with open(boundary_file, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
                # polyMesh/boundary starts patch definitions after "<nPatches> (".
                start_match = re.search(r'^\s*\d+\s*\(', content, re.MULTILINE)
                if start_match:
                    start = content.find("(", start_match.start())
                    inner_content = _extract_parenthesized_from_index(content, start)
                    if inner_content:
                        patch_matches = re.findall(
                            r'^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\{',
                            inner_content,
                            re.MULTILINE,
                        )
                        patches = patch_matches
        except Exception as e:
            return f"解析 boundary 文件时出错: {str(e)}"
            
    elif blockmesh_file.exists():
        source = "system/blockMeshDict"
        try:
            with open(blockmesh_file, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
                # Find boundary section using balanced parentheses
                inner_content = _extract_parenthesized_section(content, "boundary")
                if inner_content:
                    patch_matches = re.findall(
                        r'^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\{',
                        inner_content,
                        re.MULTILINE,
                    )
                    patches = patch_matches
        except Exception as e:
            return f"解析 blockMeshDict 时出错: {str(e)}"
    else:
        return f"错误: 在 {params.case_path} 中未找到边界定义文件。"

    if not patches:
        return f"在 {source} 中未找到任何边界定义。请检查文件格式。"
        
    lines = [f"# 边界 (Patches) 列表", f"**来源**: `{source}`", ""]
    for p in patches:
        lines.append(f"- {p}")
        
    lines.append("")
    lines.append("⚠️ **提示**: 在配置 `0/` 目录下的场变量时，请确保使用上述完全一致的名称。")
    
    return "\n".join(lines)


def openfoam_read_dictionary(params: ReadDictionaryInput) -> str:
    """
    读取并显示 OpenFOAM 字典文件内容。
    """
    case_path = Path(params.case_path)
    if not case_path.exists() or not case_path.is_dir():
        return f"错误: 案例路径不存在或不是目录: {params.case_path}"

    try:
        file_path = resolve_within_root(case_path, params.dict_path, "dict_path")
    except ValueError as e:
        return f"错误: {str(e)}"

    if not file_path.exists():
        return f"错误: 字典文件不存在: {file_path}"
        
    try:
        size_bytes = file_path.stat().st_size
        if size_bytes > MAX_READ_DICTIONARY_BYTES:
            return (
                f"错误: 字典文件过大（{size_bytes} 字节），超过限制 "
                f"{MAX_READ_DICTIONARY_BYTES} 字节"
            )

        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        return f"# 字典文件: {params.dict_path}\n\n```openfoam\n{content}\n```"
    except Exception as e:
        return f"读取字典时出错: {str(e)}"


def openfoam_update_dictionary(params: UpdateDictionaryInput) -> str:
    """
    更新 OpenFOAM 字典文件中的键值。
    
    使用简单的正则替换，尽量保留原有注释。
    """
    case_path = Path(params.case_path)
    if not case_path.exists() or not case_path.is_dir():
        return f"错误: 案例路径不存在或不是目录: {params.case_path}"

    try:
        file_path = resolve_within_root(case_path, params.dict_path, "dict_path")
    except ValueError as e:
        return f"错误: {str(e)}"

    if not file_path.exists():
        return f"错误: 字典文件不存在: {file_path}"
        
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
            
        modified = False
        new_lines = []
        updated_keys = []
        brace_depth = 0
        
        for line in lines:
            had_newline = line.endswith("\n")
            line_body = line[:-1] if had_newline else line
            updated_line = line_body

            if brace_depth == 0 and not line_body.lstrip().startswith("//"):
                for key, value in params.updates.items():
                    # Top-level only: avoid rewriting nested blocks (e.g. functions.*).
                    pattern = rf'^(\s*{re.escape(key)}\b\s+)([^;]+)(;.*)$'
                    match = re.match(pattern, line_body)
                    if match:
                        val_str = _normalize_dictionary_value_for_update(key, value)
                        updated_line = f"{match.group(1)}{val_str}{match.group(3)}"
                        modified = True
                        if key not in updated_keys:
                            updated_keys.append(key)

            new_lines.append(updated_line + ("\n" if had_newline else ""))

            code_part = _line_code_without_comment(line_body)
            brace_depth += code_part.count("{") - code_part.count("}")
            if brace_depth < 0:
                brace_depth = 0
            
        if modified:
            atomic_write_text(file_path, "".join(new_lines))
            return f"✅ 成功更新了字典 `{params.dict_path}` 中的以下键: {', '.join(updated_keys)}"
        else:
            return f"⚠️ 在 `{params.dict_path}` 中未找到匹配的键，无法更新。请检查键名是否正确。"
            
    except Exception as e:
        return f"更新字典时出错: {str(e)}"
