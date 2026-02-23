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
