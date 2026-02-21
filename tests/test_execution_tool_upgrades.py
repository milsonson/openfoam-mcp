"""Regression tests for upgraded execution/degradation behavior."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure repo root is importable when running this test file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.runner import RunResult, RunStatus
from src.tools.case_tools import (
    RunParallelInput,
    RunSolverInput,
    openfoam_run_parallel,
    openfoam_run_solver,
)
from src.tools.stability_tools import PreflightCheckInput, openfoam_preflight_check
from src.tools.workflow_tools import (
    RunWorkflowFromPromptInput,
    openfoam_run_workflow_from_prompt,
)


def _create_case(
    case_path: Path,
    *,
    solver: str = "simpleFoam",
    with_mesh: bool = True,
    with_block_mesh_dict: bool = False,
) -> None:
    (case_path / "0").mkdir(parents=True, exist_ok=True)
    (case_path / "constant").mkdir(parents=True, exist_ok=True)
    (case_path / "system").mkdir(parents=True, exist_ok=True)

    (case_path / "0" / "U").write_text("FoamFile{}\n", encoding="utf-8")
    (case_path / "0" / "p").write_text("FoamFile{}\n", encoding="utf-8")
    (case_path / "constant" / "transportProperties").write_text("FoamFile{}\n", encoding="utf-8")
    (case_path / "system" / "controlDict").write_text(
        f"application {solver};\n",
        encoding="utf-8",
    )
    (case_path / "system" / "fvSchemes").write_text("FoamFile{}\n", encoding="utf-8")
    (case_path / "system" / "fvSolution").write_text("FoamFile{}\n", encoding="utf-8")

    if with_block_mesh_dict:
        (case_path / "system" / "blockMeshDict").write_text("FoamFile{}\n", encoding="utf-8")

    if with_mesh:
        (case_path / "constant" / "polyMesh").mkdir(parents=True, exist_ok=True)
        (case_path / "constant" / "polyMesh" / "points").write_text("// mesh\n", encoding="utf-8")
        (case_path / "constant" / "polyMesh" / "faces").write_text("// mesh\n", encoding="utf-8")


def _completed_result(
    *,
    duration: float = 0.1,
    residuals: dict[str, float] | None = None,
) -> RunResult:
    return RunResult(
        status=RunStatus.COMPLETED,
        return_code=0,
        stdout="End",
        stderr="",
        duration_seconds=duration,
        final_residuals=residuals or {"U": 1e-6},
        converged=True,
    )


def test_preflight_diagnostic_profile_downgrades_missing_commands(monkeypatch) -> None:
    for key in ["WM_PROJECT_DIR", "WM_PROJECT_VERSION", "FOAM_APPBIN", "FOAM_TUTORIALS"]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("src.tools.stability_tools.which", lambda _cmd: None)

    result = openfoam_preflight_check(PreflightCheckInput(profile="diagnostic"))
    assert "总体状态" in result
    assert "- 错误: 0" in result
    assert "⚠️ `blockMesh`" in result


def test_run_solver_returns_not_executed_when_solver_command_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    case_path = tmp_path / "case"
    _create_case(case_path)
    monkeypatch.setattr("src.tools.case_tools.resolve_openfoam_command", lambda _cmd: None)

    result = openfoam_run_solver(
        RunSolverInput(
            case_path=str(case_path),
            solver="simpleFoam",
            timeout=120,
        )
    )
    assert "未执行" in result
    assert "simpleFoam" in result


def test_run_solver_rejects_invalid_solver_name(tmp_path: Path) -> None:
    case_path = tmp_path / "case_invalid_solver"
    _create_case(case_path)

    result = openfoam_run_solver(
        RunSolverInput(
            case_path=str(case_path),
            solver="simpleFoam;rm -rf /",
            timeout=120,
        )
    )
    assert "非法求解器名称" in result


def test_run_parallel_auto_mesh_and_fallback_to_serial_when_parallel_deps_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    case_path = tmp_path / "case_parallel"
    _create_case(case_path, with_mesh=False, with_block_mesh_dict=True)

    def fake_resolve_command(cmd: str) -> str | None:
        return {
            "simpleFoam": "/opt/openfoam/bin/simpleFoam",
            "blockMesh": "/opt/openfoam/bin/blockMesh",
        }.get(cmd)

    called = {"mesh": 0, "serial": 0}

    def fake_run_mesh_generation(*_args, **_kwargs) -> RunResult:
        called["mesh"] += 1
        return _completed_result()

    def fake_run_solver(*_args, **_kwargs) -> RunResult:
        called["serial"] += 1
        return _completed_result(residuals={"U": 1e-7, "p": 1e-5})

    def fail_parallel(*_args, **_kwargs) -> RunResult:
        raise AssertionError("parallel path should fallback to serial when mpirun is missing")

    monkeypatch.setattr("src.tools.case_tools.resolve_openfoam_command", fake_resolve_command)
    monkeypatch.setattr("src.tools.case_tools.run_mesh_generation", fake_run_mesh_generation)
    monkeypatch.setattr("src.tools.case_tools.run_solver", fake_run_solver)
    monkeypatch.setattr("src.tools.case_tools.run_parallel", fail_parallel)

    result = openfoam_run_parallel(
        RunParallelInput(
            case_path=str(case_path),
            n_processors=4,
            solver="simpleFoam",
            timeout=120,
        )
    )

    assert called["mesh"] == 1
    assert called["serial"] == 1
    assert "自动运行 blockMesh" in result
    assert "回退到串行" in result
    assert "✅ 串行求解完成" in result


def test_workflow_without_execution_has_non_blocking_preflight(tmp_path: Path) -> None:
    case_path = tmp_path / "workflow_case"
    result = openfoam_run_workflow_from_prompt(
        RunWorkflowFromPromptInput(
            description="模拟水在直径5cm、长度50cm的管道中以1m/s流动",
            case_path=str(case_path),
            run_mesh=False,
            run_solver=False,
            auto_apply_stability_fixes=True,
            response_format="json",
        )
    )

    payload = json.loads(result)
    assert payload["preflight"]["errors"] == 0
    assert payload["preflight"]["profile"] == "diagnostic"
    assert payload["status"] in {"completed", "completed_with_warnings"}


def test_workflow_solver_requested_without_solver_command_sets_warning_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    case_path = tmp_path / "workflow_solver_case"
    monkeypatch.setattr("src.tools.workflow_tools.resolve_openfoam_command", lambda _cmd: None)

    result = openfoam_run_workflow_from_prompt(
        RunWorkflowFromPromptInput(
            description="模拟水在直径5cm、长度50cm的管道中以1m/s流动",
            case_path=str(case_path),
            run_mesh=False,
            run_solver=True,
            run_parallel=False,
            auto_apply_stability_fixes=True,
            response_format="json",
        )
    )

    payload = json.loads(result)
    assert payload["preflight"]["profile"] == "solver"
    assert payload["solver_run"]["status"] == "skipped"
    assert payload["status"] == "completed_with_warnings"
