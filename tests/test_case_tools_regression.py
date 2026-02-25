"""Regression tests for case tool generation paths."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure repo root is importable when running this test file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tools.case_tools import (
    AnalyzeProblemInput,
    CreateCaseInput,
    openfoam_analyze_problem,
    openfoam_create_case,
)


def _pipe_flow_params() -> dict[str, object]:
    return {
        "diameter": 0.05,
        "length": 0.5,
        "inlet_velocity": 1.0,
        "fluid": "water",
    }


def test_openfoam_create_case_returns_success_message(tmp_path: Path) -> None:
    """Creating a minimal pipe case should not raise internal tool errors."""
    case_path = tmp_path / "pipe_case"
    result = openfoam_create_case(
        CreateCaseInput(
            template_id="pipe_flow",
            case_path=str(case_path),
            parameters=_pipe_flow_params(),
            run_mesh=False,
            run_validation=False,
        )
    )

    assert "# 案例创建成功" in result
    assert "创建案例时发生错误" not in result


def test_generated_control_dict_functions_block_uses_single_braces(tmp_path: Path) -> None:
    """Generated controlDict should not contain doubled braces in functionObjects."""
    case_path = tmp_path / "pipe_case"
    result = openfoam_create_case(
        CreateCaseInput(
            template_id="pipe_flow",
            case_path=str(case_path),
            parameters=_pipe_flow_params(),
            run_mesh=False,
            run_validation=False,
        )
    )
    assert "# 案例创建成功" in result

    control_dict = (case_path / "system" / "controlDict").read_text(encoding="utf-8")
    assert "functions\n{" in control_dict
    assert "{{" not in control_dict
    assert "}}" not in control_dict


def test_create_case_rejects_disallowed_case_root() -> None:
    """CreateCaseInput should reject writes outside allowed roots."""
    with pytest.raises(ValueError):
        CreateCaseInput(
            template_id="pipe_flow",
            case_path="/etc/openfoam_mcp_case",
            parameters=_pipe_flow_params(),
            run_mesh=False,
            run_validation=False,
        )


def test_openfoam_create_case_rolls_back_new_case_on_generation_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """If creation fails after touching disk, new case directory should be rolled back."""
    case_path = tmp_path / "broken_case"

    def fake_create_case_config(*_args, **kwargs):
        return SimpleNamespace(case_path=kwargs.get("case_path") or str(case_path))

    class FailingGenerator:
        def __init__(self, config):
            self._config = config

        def write_case(self):
            target = Path(self._config.case_path)
            (target / "system").mkdir(parents=True, exist_ok=True)
            (target / "system" / "controlDict").write_text("application simpleFoam;\n", encoding="utf-8")
            raise RuntimeError("mock generator failure")

    monkeypatch.setattr("src.tools.case_tools.create_case_config_from_template", fake_create_case_config)
    monkeypatch.setattr("src.tools.case_tools.OpenFOAMGenerator", FailingGenerator)

    result = openfoam_create_case(
        CreateCaseInput(
            template_id="pipe_flow",
            case_path=str(case_path),
            parameters=_pipe_flow_params(),
            run_mesh=False,
            run_validation=False,
        )
    )

    assert "创建案例时发生错误" in result
    assert not case_path.exists()


def test_analyze_problem_does_not_misclassify_soil_as_oil() -> None:
    """English word boundaries should avoid matching soil -> oil."""
    result = openfoam_analyze_problem(
        AnalyzeProblemInput(
            description="Analyze airflow over soil surface with 2 m/s inlet velocity",
            response_format="json",
        )
    )
    assert '"fluid": "oil"' not in result


def test_shock_tube_case_uses_internal_energy_and_pimple_block(tmp_path: Path) -> None:
    """shock_tube template should generate rhoCentralFoam-compatible dictionaries."""
    case_path = tmp_path / "shock_tube_case"
    result = openfoam_create_case(
        CreateCaseInput(
            template_id="shock_tube",
            case_path=str(case_path),
            parameters={
                "tube_length": 1.0,
                "diaphragm_position": 0.5,
                "left_pressure": 100000.0,
                "right_pressure": 10000.0,
                "left_temperature": 348.432,
                "right_temperature": 278.746,
                "mesh_density": "coarse",
                "end_time": 0.0002,
            },
            run_mesh=False,
            run_validation=False,
        )
    )

    assert "# 案例创建成功" in result
    thermo = (case_path / "constant" / "thermophysicalProperties").read_text(encoding="utf-8")
    fv_solution = (case_path / "system" / "fvSolution").read_text(encoding="utf-8")
    assert "energy          sensibleInternalEnergy;" in thermo
    assert "PIMPLE" in fv_solution
