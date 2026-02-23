"""Tests for end-to-end workflow and stability auto-fix tools."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure repo root is importable when running this test file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tools.stability_tools import (
    ApplyStabilityFixesInput,
    openfoam_apply_stability_fixes,
)
from src.tools.workflow_tools import (
    GenerateModelingPlanInput,
    RunWorkflowFromPromptInput,
    _to_meters,
    openfoam_generate_modeling_plan,
    openfoam_run_workflow_from_prompt,
)


def _write_min_case(case_path: Path, solver: str = "simpleFoam") -> None:
    (case_path / "0").mkdir(parents=True, exist_ok=True)
    (case_path / "constant").mkdir(parents=True, exist_ok=True)
    (case_path / "system").mkdir(parents=True, exist_ok=True)

    (case_path / "0" / "U").write_text("FoamFile{}\n", encoding="utf-8")
    (case_path / "0" / "p").write_text("FoamFile{}\n", encoding="utf-8")
    (case_path / "constant" / "transportProperties").write_text("FoamFile{}\n", encoding="utf-8")
    (case_path / "system" / "controlDict").write_text(
        f"application {solver};\n"
        "deltaT 1;\n",
        encoding="utf-8",
    )
    (case_path / "system" / "fvSchemes").write_text("FoamFile{}\n", encoding="utf-8")
    (case_path / "system" / "fvSolution").write_text("FoamFile{}\n", encoding="utf-8")


def test_generate_modeling_plan_builds_ready_pipe_flow_plan() -> None:
    result = openfoam_generate_modeling_plan(
        GenerateModelingPlanInput(
            description="模拟水在直径5cm、长度50cm的管道中以1m/s流动",
            response_format="json",
        )
    )

    data = json.loads(result)
    assert data["status"] == "ready"
    assert data["template_id"] == "pipe_flow"
    assert data["missing_required"] == []

    params = data["parameters"]
    assert abs(params["diameter"] - 0.05) < 1e-9
    assert abs(params["length"] - 0.5) < 1e-9
    assert abs(params["inlet_velocity"] - 1.0) < 1e-9
    assert params["fluid"] == "water"


def test_to_meters_rejects_unknown_unit() -> None:
    with pytest.raises(ValueError, match="不支持的长度单位"):
        _to_meters(1.0, "inch")


def test_generate_modeling_plan_identifies_shock_tube_prompt() -> None:
    result = openfoam_generate_modeling_plan(
        GenerateModelingPlanInput(
            description="模拟激波管问题，左高压右低压，观察激波传播",
            response_format="json",
        )
    )

    data = json.loads(result)
    assert data["status"] == "ready"
    assert data["template_id"] == "shock_tube"
    assert data["confidence"] >= 0.6


def test_generate_modeling_plan_identifies_supersonic_nozzle_prompt() -> None:
    result = openfoam_generate_modeling_plan(
        GenerateModelingPlanInput(
            description="模拟超音速喷管（拉瓦尔喷管）流动，关注喉部和出口压力",
            response_format="json",
        )
    )

    data = json.loads(result)
    assert data["status"] == "ready"
    assert data["template_id"] == "supersonic_nozzle"
    assert data["confidence"] >= 0.6


def test_generate_modeling_plan_returns_needs_input_for_ambiguous_prompt() -> None:
    result = openfoam_generate_modeling_plan(
        GenerateModelingPlanInput(
            description="想做一个管道和方腔耦合流动概念验证，先给个模板",
            response_format="json",
        )
    )

    data = json.loads(result)
    assert data["status"] == "needs_input"
    assert data["template_id"] in {"pipe_flow", "cavity_flow"}
    assert data["classification_status"] == "low_confidence"
    assert data["confidence"] < 0.6


def test_apply_stability_fixes_transient_case_updates_control_and_algorithm(tmp_path: Path) -> None:
    case_path = tmp_path / "transient_case"
    _write_min_case(case_path, solver="interFoam")

    (case_path / "system" / "controlDict").write_text(
        "application interFoam;\n"
        "deltaT 0.01;\n"
        "adjustTimeStep no;\n"
        "maxCo 2.0;\n",
        encoding="utf-8",
    )
    (case_path / "system" / "fvSolution").write_text(
        "FoamFile{}\n"
        "PIMPLE\n"
        "{\n"
        "    nOuterCorrectors 1;\n"
        "}\n",
        encoding="utf-8",
    )

    result = openfoam_apply_stability_fixes(
        ApplyStabilityFixesInput(
            case_path=str(case_path),
            strategy="conservative",
            response_format="json",
        )
    )
    payload = json.loads(result)
    assert payload["updated"] is True
    assert payload["changes"]

    control_text = (case_path / "system" / "controlDict").read_text(encoding="utf-8")
    assert "adjustTimeStep yes;" in control_text
    assert "maxCo 0.5;" in control_text
    assert "maxAlphaCo 0.5;" in control_text

    fv_solution_text = (case_path / "system" / "fvSolution").read_text(encoding="utf-8")
    assert "nNonOrthogonalCorrectors 1;" in fv_solution_text


def test_apply_stability_fixes_steady_case_adds_relaxation_factors(tmp_path: Path) -> None:
    case_path = tmp_path / "steady_case"
    _write_min_case(case_path, solver="simpleFoam")

    (case_path / "system" / "fvSolution").write_text(
        "FoamFile{}\n"
        "SIMPLE\n"
        "{\n"
        "    nNonOrthogonalCorrectors -1;\n"
        "}\n",
        encoding="utf-8",
    )

    result = openfoam_apply_stability_fixes(
        ApplyStabilityFixesInput(
            case_path=str(case_path),
            strategy="balanced",
            response_format="json",
        )
    )
    payload = json.loads(result)
    assert payload["updated"] is True

    fv_solution_text = (case_path / "system" / "fvSolution").read_text(encoding="utf-8")
    assert "nNonOrthogonalCorrectors 0;" in fv_solution_text
    assert "relaxationFactors" in fv_solution_text


def test_run_workflow_from_prompt_creates_case_and_summary(tmp_path: Path) -> None:
    case_path = tmp_path / "nl_pipe_case"

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
    assert payload["status"] in {"completed", "completed_with_warnings"}
    assert payload["template_id"] == "pipe_flow"
    assert payload["case_path"] == str(case_path)

    assert (case_path / "0" / "U").exists()
    assert (case_path / "system" / "controlDict").exists()
    assert payload["stability"]["after"] is not None


def test_run_workflow_from_prompt_blocks_case_creation_on_ambiguous_prompt(tmp_path: Path) -> None:
    case_path = tmp_path / "nl_ambiguous_case"

    result = openfoam_run_workflow_from_prompt(
        RunWorkflowFromPromptInput(
            description="想做一个管道和方腔耦合流动概念验证，先给个模板",
            case_path=str(case_path),
            run_mesh=False,
            run_solver=False,
            auto_apply_stability_fixes=True,
            response_format="json",
        )
    )

    payload = json.loads(result)
    assert payload["status"] == "needs_input"
    assert payload["stage"] == "planning"
    assert payload["plan"]["classification_status"] == "low_confidence"
    assert payload["plan"]["confidence"] < 0.6
    assert not case_path.exists()


def test_run_workflow_from_prompt_consumes_structured_preflight_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    case_path = tmp_path / "nl_structured_preflight_case"

    def fake_preflight(params):
        assert params.response_format == "json"
        return json.dumps(
            {
                "profile": "solver",
                "summary": {
                    "overall": "blocked",
                    "errors": 2,
                    "warnings": 3,
                    "passed": 1,
                },
                "env_checks": [],
                "command_checks": [],
                "case_checks": [],
                "checks": [],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("src.tools.workflow_tools.openfoam_preflight_check", fake_preflight)
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
    assert payload["preflight"]["errors"] == 2
    assert payload["preflight"]["warnings"] == 3
    assert payload["preflight"]["overall"] == "blocked"
    assert payload["preflight"]["profile"] == "solver"
    assert "preflight_errors" in payload["warnings"]
    assert "preflight_warnings" in payload["warnings"]


def test_workflow_json_response_is_truncated_when_payload_is_too_large(
    tmp_path: Path,
    monkeypatch,
) -> None:
    case_path = tmp_path / "workflow_large_payload_case"
    huge_report = "preflight-report:" + ("x" * 80000)

    monkeypatch.setattr(
        "src.tools.workflow_tools.openfoam_preflight_check",
        lambda _params: huge_report,
    )

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
    assert payload["response_truncated"] is True
    assert len(result) <= 25000
