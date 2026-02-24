"""Security and stability tests for MCP tools."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure repo root is importable when running this test file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.parallel import _generate_decompose_par_dict_content, decompose_case, run_parallel
from src.core.runner import RunResult, RunStatus
from src.tools.case_tools import (
    CalculateYplusInput,
    GenerateBoundaryConditionsInput,
    GenerateResidualPlotInput,
    GetRunStatusInput,
    GenerateMeshInput,
    _mesh_exists,
    openfoam_calculate_yplus,
    openfoam_generate_boundary_conditions,
    openfoam_generate_residual_plot,
    openfoam_generate_mesh,
    openfoam_get_run_status,
)
from src.tools.debug_tools import (
    GetPatchListInput,
    ReadDictionaryInput,
    UpdateDictionaryInput,
    openfoam_get_patch_list,
    openfoam_read_dictionary,
    openfoam_update_dictionary,
)
from src.tools.knowledge_tools import (
    ReadTutorialFileInput,
    SearchTutorialsInput,
    openfoam_search_tutorials,
    openfoam_read_tutorial_file,
)
from src.tools.stability_tools import (
    AssessCaseStabilityInput,
    ApplyStabilityFixesInput,
    PreflightCheckInput,
    _upsert_top_level_entry,
    openfoam_assess_case_stability,
    openfoam_preflight_check,
)
from src.core.validator import CaseValidator


def _create_min_case(case_path: Path) -> None:
    """Create a minimal OpenFOAM-like case structure for tests."""
    (case_path / "0").mkdir(parents=True, exist_ok=True)
    (case_path / "constant").mkdir(parents=True, exist_ok=True)
    (case_path / "system").mkdir(parents=True, exist_ok=True)
    (case_path / "0" / "U").write_text("dummy\n", encoding="utf-8")
    (case_path / "0" / "p").write_text("dummy\n", encoding="utf-8")
    (case_path / "constant" / "transportProperties").write_text("dummy\n", encoding="utf-8")
    (case_path / "system" / "controlDict").write_text("application simpleFoam;\n", encoding="utf-8")
    (case_path / "system" / "fvSchemes").write_text("FoamFile{}\n", encoding="utf-8")
    (case_path / "system" / "fvSolution").write_text("FoamFile{}\n", encoding="utf-8")


def test_server_script_entrypoint_starts_without_relative_import_error():
    """Running src/server.py directly should not fail with relative-import errors."""
    repo_root = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, "src/server.py"]

    try:
        subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # Expected for a long-running MCP server.
        return

    # If process exits quickly, it must not be due to relative import failure.
    result = subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=1.0,
        check=False,
    )
    combined = f"{result.stdout}\n{result.stderr}"
    assert "attempted relative import with no known parent package" not in combined


def test_read_dictionary_blocks_path_escape(tmp_path: Path):
    """Dictionary reading must stay inside case_path."""
    case_path = tmp_path / "case"
    _create_min_case(case_path)

    result = openfoam_read_dictionary(
        ReadDictionaryInput(
            case_path=str(case_path),
            dict_path="../../../etc/hosts",
        )
    )
    assert "dict_path" in result
    assert "案例目录内" in result


def test_update_dictionary_blocks_path_escape(tmp_path: Path):
    """Dictionary update must stay inside case_path."""
    case_path = tmp_path / "case"
    _create_min_case(case_path)

    target = Path("/tmp/openfoam_mcp_escape_test.txt")
    target.write_text("endTime 10;\n", encoding="utf-8")
    try:
        result = openfoam_update_dictionary(
            UpdateDictionaryInput(
                case_path=str(case_path),
                dict_path="../../../tmp/openfoam_mcp_escape_test.txt",
                updates={"endTime": 20},
            )
        )
        assert "dict_path" in result
        assert "案例目录内" in result
        assert target.read_text(encoding="utf-8") == "endTime 10;\n"
    finally:
        target.unlink(missing_ok=True)


def test_get_patch_list_parses_all_blockmesh_patches(tmp_path: Path):
    """Patch parser should not stop at the first faces(...) section."""
    case_path = tmp_path / "case_patch_list"
    (case_path / "system").mkdir(parents=True, exist_ok=True)
    (case_path / "system" / "blockMeshDict").write_text(
        (
            "boundary\n"
            "(\n"
            "    inlet\n"
            "    {\n"
            "        type patch;\n"
            "        faces\n"
            "        (\n"
            "            (0 4 7 3)\n"
            "        );\n"
            "    }\n"
            "    outlet\n"
            "    {\n"
            "        type patch;\n"
            "        faces\n"
            "        (\n"
            "            (1 2 6 5)\n"
            "        );\n"
            "    }\n"
            ");\n"
        ),
        encoding="utf-8",
    )

    result = openfoam_get_patch_list(GetPatchListInput(case_path=str(case_path)))
    assert "- inlet" in result
    assert "- outlet" in result


def test_update_dictionary_uses_atomic_write(tmp_path: Path, monkeypatch):
    """Dictionary updates should go through atomic write helper."""
    case_path = tmp_path / "case_update_dict"
    (case_path / "system").mkdir(parents=True, exist_ok=True)
    dict_path = case_path / "system" / "controlDict"
    dict_path.write_text("endTime 10;\n", encoding="utf-8")

    called = {}

    def fake_atomic_write(path: Path, content: str) -> None:
        called["path"] = path
        called["content"] = content
        path.write_text(content, encoding="utf-8")

    monkeypatch.setattr("src.tools.debug_tools.atomic_write_text", fake_atomic_write)

    result = openfoam_update_dictionary(
        UpdateDictionaryInput(
            case_path=str(case_path),
            dict_path="system/controlDict",
            updates={"endTime": 20},
        )
    )
    assert "成功更新" in result
    assert called.get("path") == dict_path
    assert "endTime 20;" in called.get("content", "")


def test_update_dictionary_rejects_multiline_value(tmp_path: Path):
    """Dictionary updates should reject multiline values to prevent structure break."""
    case_path = tmp_path / "case_update_multiline"
    (case_path / "system").mkdir(parents=True, exist_ok=True)
    dict_path = case_path / "system" / "controlDict"
    dict_path.write_text("endTime 10;\n", encoding="utf-8")

    result = openfoam_update_dictionary(
        UpdateDictionaryInput(
            case_path=str(case_path),
            dict_path="system/controlDict",
            updates={"endTime": "20\nwriteInterval 1"},
        )
    )
    assert "更新值包含换行符" in result
    assert dict_path.read_text(encoding="utf-8") == "endTime 10;\n"


def test_case_path_allowlist_validation_consistent_across_tools():
    """Case path validation should be enforced across debug/case/stability inputs."""
    with pytest.raises(ValueError, match="案例路径不在允许范围内"):
        GetPatchListInput(case_path="/etc")

    with pytest.raises(ValueError, match="案例路径不在允许范围内"):
        GenerateMeshInput(case_path="/etc", mesh_type="blockMesh")

    with pytest.raises(ValueError, match="案例路径不在允许范围内"):
        AssessCaseStabilityInput(case_path="/etc")

    with pytest.raises(ValueError, match="案例路径不在允许范围内"):
        ApplyStabilityFixesInput(case_path="/etc")


def test_read_tutorial_file_rejects_absolute_path_outside_tutorials(tmp_path: Path, monkeypatch):
    """Tutorial file reader must reject absolute tutorial paths outside FOAM_TUTORIALS."""
    tutorials_root = tmp_path / "tutorials"
    case_path = tutorials_root / "incompressible" / "simpleFoam" / "pitzDaily"
    (case_path / "system").mkdir(parents=True, exist_ok=True)
    (case_path / "system" / "controlDict").write_text("application simpleFoam;\n", encoding="utf-8")
    (case_path / "system" / "fvSchemes").write_text("dummy\n", encoding="utf-8")
    monkeypatch.setenv("FOAM_TUTORIALS", str(tutorials_root))

    result = openfoam_read_tutorial_file(
        ReadTutorialFileInput(
            tutorial_path="/etc",
            file_path="hosts",
        )
    )
    assert "FOAM_TUTORIALS" in result
    assert "tutorial_path" in result


def test_read_tutorial_file_relative_path_still_works(tmp_path: Path, monkeypatch):
    """Tutorial file reader should still support relative tutorial paths."""
    tutorials_root = tmp_path / "tutorials"
    case_path = tutorials_root / "incompressible" / "simpleFoam" / "pitzDaily"
    (case_path / "system").mkdir(parents=True, exist_ok=True)
    (case_path / "system" / "controlDict").write_text("application simpleFoam;\n", encoding="utf-8")
    (case_path / "system" / "fvSchemes").write_text("ddtSchemes{}\n", encoding="utf-8")
    monkeypatch.setenv("FOAM_TUTORIALS", str(tutorials_root))

    result = openfoam_read_tutorial_file(
        ReadTutorialFileInput(
            tutorial_path="incompressible/simpleFoam/pitzDaily",
            file_path="system/fvSchemes",
        )
    )
    assert "ddtSchemes{}" in result


def test_read_tutorial_file_rejects_non_regular_file(tmp_path: Path, monkeypatch):
    """Tutorial reader should reject directories and other non-regular files."""
    tutorials_root = tmp_path / "tutorials"
    case_path = tutorials_root / "incompressible" / "simpleFoam" / "pitzDaily"
    (case_path / "system").mkdir(parents=True, exist_ok=True)
    (case_path / "system" / "controlDict").write_text("application simpleFoam;\n", encoding="utf-8")
    monkeypatch.setenv("FOAM_TUTORIALS", str(tutorials_root))

    result = openfoam_read_tutorial_file(
        ReadTutorialFileInput(
            tutorial_path="incompressible/simpleFoam/pitzDaily",
            file_path="system",
        )
    )
    assert "仅支持读取常规文件" in result


def test_generate_boundary_conditions_unknown_field_still_creates_file(tmp_path: Path):
    """Unknown field should fall back to default and still generate a file."""
    case_path = tmp_path / "case"
    case_path.mkdir(parents=True, exist_ok=True)

    result = openfoam_generate_boundary_conditions(
        GenerateBoundaryConditionsInput(
            case_path=str(case_path),
            field_name="alphaWater",
            boundary_definitions={"inlet": {"type": "fixedValue", "value": "0"}},
        )
    )

    out_file = case_path / "0" / "alphaWater"
    assert out_file.exists()
    assert "默认" in result
    assert "alphaWater" in out_file.read_text(encoding="utf-8")


def test_generate_boundary_conditions_rejects_scalar_vector_mismatch(tmp_path: Path):
    """Boundary generator should reject vector value for scalar field."""
    case_path = tmp_path / "case_bc_dim"
    case_path.mkdir(parents=True, exist_ok=True)

    result = openfoam_generate_boundary_conditions(
        GenerateBoundaryConditionsInput(
            case_path=str(case_path),
            field_name="p",
            boundary_definitions={"inlet": {"type": "fixedValue", "value": "(1 0 0)"}},
        )
    )
    assert "标量场边界值不能是向量形式" in result


def test_generate_boundary_conditions_rejects_non_vector_value_for_U(tmp_path: Path):
    """Boundary generator should reject non-3D vector for U field."""
    case_path = tmp_path / "case_bc_dim_u"
    case_path.mkdir(parents=True, exist_ok=True)

    result = openfoam_generate_boundary_conditions(
        GenerateBoundaryConditionsInput(
            case_path=str(case_path),
            field_name="U",
            boundary_definitions={"inlet": {"type": "fixedValue", "value": "1"}},
        )
    )
    assert "向量场边界值格式错误" in result


def test_generate_boundary_conditions_rejects_unsafe_extra_property(tmp_path: Path):
    """Boundary extra properties should reject injected keys/values."""
    case_path = tmp_path / "case_bc_injection"
    case_path.mkdir(parents=True, exist_ok=True)

    bad_key_result = openfoam_generate_boundary_conditions(
        GenerateBoundaryConditionsInput(
            case_path=str(case_path),
            field_name="U",
            boundary_definitions={
                "inlet": {
                    "type": "fixedValue",
                    "value": "(1 0 0)",
                    "bad-key": "uniform 0",
                }
            },
        )
    )
    assert "附加属性名非法" in bad_key_result

    bad_val_result = openfoam_generate_boundary_conditions(
        GenerateBoundaryConditionsInput(
            case_path=str(case_path),
            field_name="U",
            boundary_definitions={
                "inlet": {
                    "type": "fixedValue",
                    "value": "(1 0 0)",
                    "mixingLength": "0.09;\n}",
                }
            },
        )
    )
    assert "附加属性值包含非法字符" in bad_val_result


def test_generate_boundary_conditions_rejects_unsafe_field_name(tmp_path: Path):
    """Field file name must be a safe token (no path traversal)."""
    case_path = tmp_path / "case_bc_field_name"
    case_path.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="field_name"):
        GenerateBoundaryConditionsInput(
            case_path=str(case_path),
            field_name="../../../../tmp/pwned",
            boundary_definitions={"inlet": {"type": "fixedValue", "value": "0"}},
        )


def test_generate_boundary_conditions_rejects_boundary_name_injection(tmp_path: Path):
    """Boundary patch names must reject injected braces/newlines."""
    case_path = tmp_path / "case_bc_boundary_name"
    case_path.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="非法边界名称"):
        GenerateBoundaryConditionsInput(
            case_path=str(case_path),
            field_name="U",
            boundary_definitions={"inlet\n}\nmalicious": {"type": "fixedValue", "value": "(1 0 0)"}},
        )


def test_generate_boundary_conditions_rejects_scalar_injection_value(tmp_path: Path):
    """Scalar boundary values should reject config-injection separators."""
    case_path = tmp_path / "case_bc_scalar_inject"
    case_path.mkdir(parents=True, exist_ok=True)

    result = openfoam_generate_boundary_conditions(
        GenerateBoundaryConditionsInput(
            case_path=str(case_path),
            field_name="p",
            boundary_definitions={"inlet": {"type": "fixedValue", "value": "0; bad"}},
        )
    )
    assert "标量场边界值包含非法字符" in result


def test_generate_mesh_uses_atomic_write_for_block_mesh_updates(tmp_path: Path, monkeypatch):
    """Mesh generator should write blockMeshDict via atomic helper."""
    case_path = tmp_path / "mesh_case"
    system_path = case_path / "system"
    system_path.mkdir(parents=True, exist_ok=True)
    block_mesh = system_path / "blockMeshDict"
    block_mesh.write_text(
        "hex (0 1 2 3 4 5 6 7) (10 10 10)\n// ************************************************************************* //\n",
        encoding="utf-8",
    )

    called = {}

    def fake_atomic_write(path: Path, content: str) -> None:
        called["path"] = path
        path.write_text(content, encoding="utf-8")

    monkeypatch.setattr("src.tools.case_tools.atomic_write_text", fake_atomic_write)

    result = openfoam_generate_mesh(
        GenerateMeshInput(
            case_path=str(case_path),
            mesh_type="blockMesh",
            mesh_params={"n_cells_x": 20},
        )
    )
    assert "网格配置已更新" in result
    assert called.get("path") == block_mesh


def test_generate_mesh_updates_all_hex_blocks(tmp_path: Path):
    """Mesh update should apply n_cells changes to every hex block."""
    case_path = tmp_path / "mesh_case_multi_block"
    system_path = case_path / "system"
    system_path.mkdir(parents=True, exist_ok=True)
    block_mesh = system_path / "blockMeshDict"
    block_mesh.write_text(
        (
            "blocks\n"
            "(\n"
            "    hex (0 1 2 3 4 5 6 7) (10 10 10) simpleGrading (1 1 1)\n"
            "    hex (8 9 10 11 12 13 14 15) (10 10 10) simpleGrading (1 1 1)\n"
            ");\n"
        ),
        encoding="utf-8",
    )

    result = openfoam_generate_mesh(
        GenerateMeshInput(
            case_path=str(case_path),
            mesh_type="blockMesh",
            mesh_params={"n_cells_x": 20, "n_cells_y": 30},
        )
    )
    assert "网格配置已更新" in result
    updated = block_mesh.read_text(encoding="utf-8")
    assert updated.count("(20 30 10)") == 2


def test_mesh_exists_requires_points_and_faces(tmp_path: Path):
    """Mesh existence check should require both points and faces files."""
    case_path = tmp_path / "mesh_exists_case"
    poly_mesh = case_path / "constant" / "polyMesh"
    poly_mesh.mkdir(parents=True, exist_ok=True)
    (poly_mesh / "points").write_text("// points\n", encoding="utf-8")

    assert _mesh_exists(case_path) is False

    (poly_mesh / "faces").write_text("// faces\n", encoding="utf-8")
    assert _mesh_exists(case_path) is True


def test_generate_boundary_conditions_uses_atomic_write(tmp_path: Path, monkeypatch):
    """Boundary generator should write field file via atomic helper."""
    case_path = tmp_path / "bc_case"
    case_path.mkdir(parents=True, exist_ok=True)
    called = {}

    def fake_atomic_write(path: Path, content: str) -> None:
        called["path"] = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    monkeypatch.setattr("src.tools.case_tools.atomic_write_text", fake_atomic_write)

    result = openfoam_generate_boundary_conditions(
        GenerateBoundaryConditionsInput(
            case_path=str(case_path),
            field_name="U",
            boundary_definitions={"inlet": {"type": "fixedValue", "value": "(1 0 0)"}},
        )
    )
    assert "边界条件文件已生成" in result
    assert called.get("path") == case_path / "0" / "U"


def test_calculate_yplus_applies_truncation(monkeypatch):
    """y+ report should go through truncation guard for oversized content."""
    long_note = "x" * 30000

    monkeypatch.setattr(
        "src.tools.case_tools.calculate_yplus_first_layer",
        lambda **_kwargs: {
            "reynolds_number": 1e6,
            "target_yplus": 30,
            "skin_friction_Cf": 0.005,
            "friction_velocity_u_tau": 0.2,
            "first_cell_height_mm": 0.05,
            "first_cell_height_m": 5e-5,
            "recommended_approach": "wallFunction",
            "recommended_yplus_range": "30-300",
            "note": long_note,
        },
    )

    result = openfoam_calculate_yplus(
        CalculateYplusInput(
            velocity=5.0,
            length_scale=1.0,
            fluid="air",
            target_yplus=30.0,
        )
    )
    assert "输出已截断" in result


def test_get_run_status_handles_invalid_log_encoding(tmp_path: Path):
    """Run-status parser should tolerate non-UTF8 bytes in OpenFOAM logs."""
    case_path = tmp_path / "status_case"
    case_path.mkdir(parents=True, exist_ok=True)
    log_file = case_path / "log.simpleFoam"
    log_file.write_bytes(
        b"Time = 1\n"
        b"Solving for p, Initial residual = 0.1, Final residual = 0.01, No Iterations 1\n"
        b"\xff\xfe\n"
        b"End\n"
    )

    result = openfoam_get_run_status(GetRunStatusInput(case_path=str(case_path)))
    assert "求解器运行状态" in result
    assert "已完成" in result


def test_get_run_status_truncates_full_error_message(tmp_path: Path, monkeypatch):
    """Error fallback should pass the full message into _truncate."""
    case_path = tmp_path / "status_case_err"
    case_path.mkdir(parents=True, exist_ok=True)

    def raise_glob(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("src.tools.case_tools.Path.glob", raise_glob)
    monkeypatch.setattr("src.tools.case_tools._truncate", lambda text, limit=25000: f"TRUNC::{text}")

    result = openfoam_get_run_status(GetRunStatusInput(case_path=str(case_path)))
    assert result == "TRUNC::获取运行状态时发生错误: boom"


def test_scotch_processor_weights_match_subdomain_count():
    """Generated scotch processorWeights should match numberOfSubdomains."""
    content = _generate_decompose_par_dict_content(n_processors=8, method="scotch")
    match = re.search(r"processorWeights\s*\(([^)]+)\);", content)
    assert match is not None
    weights = [w for w in match.group(1).split() if w.strip()]
    assert len(weights) == 8


def test_simple_decompose_factors_exactly_match_processor_count():
    """simple method factors should multiply exactly to numberOfSubdomains."""
    n_processors = 10
    content = _generate_decompose_par_dict_content(n_processors=n_processors, method="simple")
    match = re.search(r"simpleCoeffs\s*\{[^}]*n\s*\((\d+)\s+(\d+)\s+(\d+)\)\s*;", content, re.DOTALL)
    assert match is not None
    n_x, n_y, n_z = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    assert n_x * n_y * n_z == n_processors


def test_hierarchical_decompose_factors_exactly_match_processor_count():
    """hierarchical method factors should multiply exactly to numberOfSubdomains."""
    n_processors = 18
    content = _generate_decompose_par_dict_content(n_processors=n_processors, method="hierarchical")
    match = re.search(r"hierarchicalCoeffs\s*\{[^}]*n\s*\((\d+)\s+(\d+)\s+(\d+)\)\s*;", content, re.DOTALL)
    assert match is not None
    n_x, n_y, n_z = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    assert n_x * n_y * n_z == n_processors


def test_decompose_case_switch_to_scotch_adds_scotch_coeffs(tmp_path: Path, monkeypatch):
    """Switching existing dict to scotch should add scotchCoeffs and drop simpleCoeffs."""
    case_path = tmp_path / "parallel_case"
    system_path = case_path / "system"
    system_path.mkdir(parents=True, exist_ok=True)
    decompose_dict = system_path / "decomposeParDict"
    decompose_dict.write_text(
        (
            "numberOfSubdomains 2;\n"
            "method simple;\n\n"
            "simpleCoeffs\n"
            "{\n"
            "    n               (2 1 1);\n"
            "    delta           0.001;\n"
            "}\n\n"
            "// ************************************************************************* //\n"
        ),
        encoding="utf-8",
    )

    def fake_run_command(self, command, args=None, timeout=None):
        return RunResult(
            status=RunStatus.COMPLETED,
            return_code=0,
            stdout="ok",
            stderr="",
            duration_seconds=0.01,
            final_residuals={},
            converged=False,
        )

    monkeypatch.setattr("src.core.parallel.SolverRunner.run_command", fake_run_command)

    result = decompose_case(str(case_path), n_processors=4, method="scotch")
    assert result.status == RunStatus.COMPLETED

    updated = decompose_dict.read_text(encoding="utf-8")
    assert "method scotch;" in updated
    assert "scotchCoeffs" in updated
    assert "processorWeights (1 1 1 1);" in updated
    assert "simpleCoeffs" not in updated


def test_decompose_case_switch_to_simple_removes_metis_coeffs(tmp_path: Path, monkeypatch):
    """Switching existing dict to simple should remove metis block leftovers."""
    case_path = tmp_path / "parallel_case_simple"
    system_path = case_path / "system"
    system_path.mkdir(parents=True, exist_ok=True)
    decompose_dict = system_path / "decomposeParDict"
    decompose_dict.write_text(
        (
            "numberOfSubdomains 4;\n"
            "method metis;\n\n"
            "metisCoeffs\n"
            "{\n"
            "    processorWeights (1 1 1 1);\n"
            "}\n\n"
            "// ************************************************************************* //\n"
        ),
        encoding="utf-8",
    )

    def fake_run_command(self, command, args=None, timeout=None):
        return RunResult(
            status=RunStatus.COMPLETED,
            return_code=0,
            stdout="ok",
            stderr="",
            duration_seconds=0.01,
            final_residuals={},
            converged=False,
        )

    monkeypatch.setattr("src.core.parallel.SolverRunner.run_command", fake_run_command)

    result = decompose_case(str(case_path), n_processors=4, method="simple")
    assert result.status == RunStatus.COMPLETED

    updated = decompose_dict.read_text(encoding="utf-8")
    assert "method simple;" in updated
    assert "simpleCoeffs" in updated
    assert "metisCoeffs" not in updated


def test_core_run_parallel_persists_solver_log(tmp_path: Path, monkeypatch) -> None:
    """run_parallel should persist log.simpleFoam for downstream status tools."""
    case_path = tmp_path / "parallel_log_case"
    _create_min_case(case_path)

    def fake_run_command(self, command, args=None, timeout=None, on_progress=None):
        return RunResult(
            status=RunStatus.COMPLETED,
            return_code=0,
            stdout="Time = 1\nEnd\n",
            stderr="",
            duration_seconds=0.2,
            final_residuals={"U": 1e-5},
            converged=True,
        )

    monkeypatch.setattr("src.core.parallel.SolverRunner.run_command", fake_run_command)

    result = run_parallel(
        case_path=str(case_path),
        solver="simpleFoam",
        n_processors=2,
    )

    assert result.status == RunStatus.COMPLETED
    log_file = case_path / "log.simpleFoam"
    assert log_file.exists()
    assert "Time = 1" in log_file.read_text(encoding="utf-8")


def test_core_run_parallel_rejects_invalid_solver_argument(tmp_path: Path) -> None:
    """run_parallel should block malformed solver arguments before mpirun execution."""
    case_path = tmp_path / "parallel_solver_guard"
    _create_min_case(case_path)

    with pytest.raises(ValueError, match="solver"):
        run_parallel(
            case_path=str(case_path),
            solver="-x",
            n_processors=2,
        )


def test_core_run_parallel_rejects_hostfile_outside_case(tmp_path: Path) -> None:
    """run_parallel hostfile path must stay within case directory."""
    case_path = tmp_path / "parallel_hostfile_guard"
    _create_min_case(case_path)

    outside_hostfile = tmp_path.parent / "hostfile.outside"
    outside_hostfile.write_text("localhost slots=4\n", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="案例目录内"):
            run_parallel(
                case_path=str(case_path),
                solver="simpleFoam",
                n_processors=2,
                hostfile=str(outside_hostfile),
            )
    finally:
        outside_hostfile.unlink(missing_ok=True)


def test_core_run_parallel_rejects_symlink_hostfile(tmp_path: Path) -> None:
    """Symlink hostfiles should be rejected even if placed under case directory."""
    case_path = tmp_path / "parallel_hostfile_symlink"
    _create_min_case(case_path)

    outside_hostfile = tmp_path / "outside.hosts"
    outside_hostfile.write_text("localhost slots=2\n", encoding="utf-8")
    symlink_path = case_path / "hosts.link"
    symlink_path.symlink_to(outside_hostfile)
    with pytest.raises(ValueError, match="符号链接"):
        run_parallel(
            case_path=str(case_path),
            solver="simpleFoam",
            n_processors=2,
            hostfile="hosts.link",
        )


def test_openfoam_generate_residual_plot_reuses_core_postprocess(tmp_path: Path, monkeypatch):
    """Tool residual plotting should rely on shared core postprocess helpers."""
    case_path = tmp_path / "plot_case"
    case_path.mkdir(parents=True, exist_ok=True)
    log_file = case_path / "log.simpleFoam"
    log_file.write_text("dummy\n", encoding="utf-8")

    class _Data:
        def __init__(self, values):
            self.final_residuals = values

    called = {}

    def fake_parse(path: str):
        called["parse"] = path
        return {"p": _Data([1e-2, 5e-4])}

    def fake_plot(residuals, output_path, plot_type, log_scale, title):
        called["plot"] = {
            "fields": list(residuals.keys()),
            "output_path": output_path,
            "plot_type": plot_type,
            "log_scale": log_scale,
            "title": title,
        }
        Path(output_path).write_text("png", encoding="utf-8")
        return output_path

    monkeypatch.setattr("src.tools.case_tools.parse_log_file", fake_parse)
    monkeypatch.setattr("src.tools.case_tools.core_generate_residual_plot", fake_plot)

    output_path = case_path / "residuals.png"
    result = openfoam_generate_residual_plot(
        GenerateResidualPlotInput(case_path=str(case_path), output_path=str(output_path))
    )

    assert "残差图已生成" in result
    assert called["parse"] == str(log_file)
    assert called["plot"]["fields"] == ["p"]
    assert called["plot"]["output_path"] == str(output_path)


def test_generate_residual_plot_rejects_path_escape(tmp_path: Path, monkeypatch) -> None:
    """Residual plot output_path must stay within case directory."""
    case_path = tmp_path / "plot_escape_case"
    case_path.mkdir(parents=True, exist_ok=True)
    (case_path / "log.simpleFoam").write_text("dummy\n", encoding="utf-8")
    monkeypatch.setattr("src.tools.case_tools.parse_log_file", lambda _path: {"p": object()})
    monkeypatch.setattr(
        "src.tools.case_tools.core_generate_residual_plot",
        lambda **_kwargs: None,
    )

    escaped = tmp_path.parent / "outside.png"

    result = openfoam_generate_residual_plot(
        GenerateResidualPlotInput(
            case_path=str(case_path),
            output_path="../../outside.png",
        )
    )

    assert "output_path" in result
    assert "案例目录内" in result
    assert not escaped.exists()


def test_preflight_check_reports_sections(tmp_path: Path):
    """Preflight check should include command and case checks."""
    case_path = tmp_path / "case"
    _create_min_case(case_path)

    result = openfoam_preflight_check(
        PreflightCheckInput(
            case_path=str(case_path),
            solver="simpleFoam",
            n_processors=4,
        )
    )
    assert "OpenFOAM 运行前检查" in result
    assert "命令检查" in result
    assert "案例结构检查" in result


def test_preflight_check_supports_json_response(tmp_path: Path):
    """Preflight check should provide structured JSON output for programmatic consumers."""
    case_path = tmp_path / "case_json"
    _create_min_case(case_path)

    result = openfoam_preflight_check(
        PreflightCheckInput(
            case_path=str(case_path),
            solver="simpleFoam",
            n_processors=4,
            response_format="json",
        )
    )
    payload = json.loads(result)

    assert payload["profile"] == "diagnostic"
    assert "summary" in payload
    assert isinstance(payload["summary"]["errors"], int)
    assert isinstance(payload["summary"]["warnings"], int)
    assert isinstance(payload["env_checks"], list)
    assert isinstance(payload["command_checks"], list)
    assert isinstance(payload["case_checks"], list)


def test_preflight_input_rejects_invalid_solver_name() -> None:
    """Preflight solver must be validated as a safe command token."""
    with pytest.raises(ValueError, match="非法求解器名称"):
        PreflightCheckInput(solver="simpleFoam;rm -rf /")


def test_update_dictionary_rejects_injection_separators(tmp_path: Path):
    """Dictionary updates should reject separators that can inject extra entries."""
    case_path = tmp_path / "case_update_separators"
    (case_path / "system").mkdir(parents=True, exist_ok=True)
    dict_path = case_path / "system" / "controlDict"
    dict_path.write_text("endTime 10;\n", encoding="utf-8")

    result = openfoam_update_dictionary(
        UpdateDictionaryInput(
            case_path=str(case_path),
            dict_path="system/controlDict",
            updates={"endTime": "20; writeInterval 1"},
        )
    )
    assert "非法字符" in result


def test_update_dictionary_uses_word_boundary_for_keys(tmp_path: Path):
    """Updating key 'p' should not accidentally match 'pressure'."""
    case_path = tmp_path / "case_update_word_boundary"
    (case_path / "system").mkdir(parents=True, exist_ok=True)
    dict_path = case_path / "system" / "controlDict"
    dict_path.write_text("pressure 100;\np 0;\n", encoding="utf-8")

    result = openfoam_update_dictionary(
        UpdateDictionaryInput(
            case_path=str(case_path),
            dict_path="system/controlDict",
            updates={"p": 5},
        )
    )

    assert "成功更新" in result
    content = dict_path.read_text(encoding="utf-8")
    assert "pressure 100;" in content
    assert "\np 5;\n" in content


def test_search_tutorials_respects_max_results(tmp_path: Path, monkeypatch):
    """Tutorial search should stop once max_results is reached."""
    tutorials_root = tmp_path / "tutorials"
    for i in range(5):
        case_path = tutorials_root / "incompressible" / "simpleFoam" / f"case_{i}" / "system"
        case_path.mkdir(parents=True, exist_ok=True)
        (case_path / "controlDict").write_text("application simpleFoam;\n", encoding="utf-8")

    monkeypatch.setenv("FOAM_TUTORIALS", str(tutorials_root))

    result = openfoam_search_tutorials(
        SearchTutorialsInput(
            keywords=["simplefoam"],
            max_results=2,
        )
    )
    assert "# OpenFOAM 教程搜索结果 (2)" in result


def test_validator_detects_misordered_braces():
    """Syntax checker should catch closing braces that appear before opening ones."""
    validator = CaseValidator("/tmp")
    errors = validator._check_dict_syntax("}\n{\nFoamFile\n{\n}\n")
    assert any("顺序错误" in err for err in errors)


def test_upsert_top_level_entry_respects_word_boundary() -> None:
    """Updating deltaT should not clobber maxDeltaT-like keys."""
    original = "maxDeltaT 1;\nadjustDeltaT no;\n"
    updated = _upsert_top_level_entry(original, "deltaT", "0.001")

    assert "maxDeltaT 1;" in updated
    assert "adjustDeltaT no;" in updated
    assert "\ndeltaT 0.001;\n" in updated or updated.startswith("deltaT 0.001;\n")


def test_assess_case_stability_flags_high_risk_settings(tmp_path: Path):
    """Stability assessment should flag unsafe time-step controls."""
    case_path = tmp_path / "case"
    _create_min_case(case_path)

    (case_path / "system" / "controlDict").write_text(
        (
            "application interFoam;\n"
            "deltaT 0.01;\n"
            "adjustTimeStep no;\n"
            "maxCo 2.0;\n"
        ),
        encoding="utf-8",
    )
    (case_path / "system" / "fvSolution").write_text(
        (
            "FoamFile{}\n"
            "PIMPLE\n"
            "{\n"
            "    nOuterCorrectors 1;\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    result = openfoam_assess_case_stability(
        AssessCaseStabilityInput(case_path=str(case_path))
    )
    assert "adjustTimeStep" in result
    assert "maxCo" in result
    assert "maxAlphaCo" in result
