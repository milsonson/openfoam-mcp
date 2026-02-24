"""Security-focused regression tests for generator and template registry."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure repo root is importable when running this test file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core import OpenFOAMGenerator, create_case_config_from_template
from src.templates import get_template


def _pipe_params() -> dict[str, object]:
    return {
        "diameter": 0.05,
        "length": 0.5,
        "inlet_velocity": 1.0,
        "fluid": "water",
    }


def test_generator_write_case_rejects_path_traversal(tmp_path: Path, monkeypatch) -> None:
    """write_case should refuse generated file paths escaping case root."""
    case_path = tmp_path / "case"
    config = create_case_config_from_template(
        template_id="pipe_flow",
        parameters=_pipe_params(),
        case_path=str(case_path),
    )
    generator = OpenFOAMGenerator(config)

    monkeypatch.setattr(generator, "generate_all", lambda **_kwargs: {"../evil": "pwned"})
    with pytest.raises(ValueError, match="越界"):
        generator.write_case()
    assert not (tmp_path / "evil").exists()


def test_registry_rejects_bool_for_numeric_parameter() -> None:
    """Template numeric validation should reject bool values."""
    template = get_template("pipe_flow")
    assert template is not None
    errors = template.validate_parameters(
        {
            "diameter": True,
            "length": 0.5,
            "inlet_velocity": 1.0,
            "fluid": "water",
        }
    )
    assert any("类型错误" in item for item in errors)


def test_generator_control_dict_uses_explicit_openfoam_library_names(tmp_path: Path) -> None:
    """controlDict function objects should use explicit .so library names."""
    case_path = tmp_path / "pipe_case"
    config = create_case_config_from_template(
        template_id="pipe_flow",
        parameters=_pipe_params(),
        case_path=str(case_path),
    )
    generator = OpenFOAMGenerator(config)
    files = generator.generate_all()
    control_dict = files["system/controlDict"]

    assert 'libs            ("libutilityFunctionObjects.so");' in control_dict
    assert 'libs            ("libforces.so");' in control_dict
    assert 'libs            ("libfieldFunctionObjects.so");' in control_dict
    assert "type            residuals;" in control_dict
    assert "type            solverInfo;" not in control_dict


def test_generator_emits_physical_properties_for_incompressible_cases(tmp_path: Path) -> None:
    """Incompressible templates should include physicalProperties for runtime compatibility."""
    case_path = tmp_path / "pipe_case_props"
    config = create_case_config_from_template(
        template_id="pipe_flow",
        parameters=_pipe_params(),
        case_path=str(case_path),
    )
    generator = OpenFOAMGenerator(config)
    files = generator.generate_all()

    assert "constant/transportProperties" in files
    assert "constant/physicalProperties" in files
    assert "viscosityModel  Newtonian;" in files["constant/physicalProperties"]


def test_generator_icofoam_uses_piso_block(tmp_path: Path) -> None:
    """Transient icoFoam templates should emit PISO instead of PIMPLE block."""
    case_path = tmp_path / "cavity_case"
    config = create_case_config_from_template(
        template_id="cavity_flow",
        parameters={
            "width": 0.1,
            "height": 0.1,
            "lid_velocity": 1.0,
            "fluid": "water",
        },
        case_path=str(case_path),
    )
    generator = OpenFOAMGenerator(config)
    fv_solution = generator.generate_all()["system/fvSolution"]

    assert "\nPISO\n{" in fv_solution
    assert "\nPIMPLE\n{" not in fv_solution


def test_channel_flow_block_mesh_preserves_neighbor_patch(tmp_path: Path) -> None:
    """channel_flow cyclic patches should keep neighbourPatch entries in blockMeshDict."""
    case_path = tmp_path / "channel_case"
    config = create_case_config_from_template(
        template_id="channel_flow",
        parameters={
            "half_height": 1.0,
            "length": 6.283,
            "span": 3.142,
            "re_tau": 395.0,
            "fluid": "water",
            "mesh_density": "medium",
        },
        case_path=str(case_path),
    )
    block_mesh = OpenFOAMGenerator(config).generate_all()["system/blockMeshDict"]

    assert "type cyclic;" in block_mesh
    assert "neighbourPatch outlet;" in block_mesh
    assert "neighbourPatch inlet;" in block_mesh


def test_laminar_control_dict_omits_turbulence_residual_fields(tmp_path: Path) -> None:
    """Laminar cases should not monitor k/epsilon/omega residual fields."""
    case_path = tmp_path / "laminar_pipe_case"
    config = create_case_config_from_template(
        template_id="pipe_flow",
        parameters={
            "diameter": 0.001,
            "length": 0.1,
            "inlet_velocity": 0.001,
            "fluid": "water",
        },
        case_path=str(case_path),
    )
    control_dict = OpenFOAMGenerator(config).generate_all()["system/controlDict"]

    assert "fields          (p U);" in control_dict
    assert "(p U k epsilon omega)" not in control_dict


def test_generate_allrun_rejects_unsafe_solver_token(tmp_path: Path) -> None:
    """Allrun generation should reject unsafe solver tokens."""
    case_path = tmp_path / "unsafe_solver_case"
    config = create_case_config_from_template(
        template_id="pipe_flow",
        parameters=_pipe_params(),
        case_path=str(case_path),
    )
    config.solver = "simpleFoam;rm -rf /"
    generator = OpenFOAMGenerator(config)

    with pytest.raises(ValueError, match="非法求解器名称"):
        generator._generate_allrun()


def test_create_case_config_does_not_mutate_input_parameters(tmp_path: Path) -> None:
    """create_case_config_from_template should not modify caller-provided dict."""
    params = _pipe_params()
    params_before = dict(params)

    _ = create_case_config_from_template(
        template_id="pipe_flow",
        parameters=params,
        case_path=str(tmp_path / "case"),
    )

    assert params == params_before
