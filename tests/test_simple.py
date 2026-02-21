"""Simple pytest tests for parallel and postprocess features."""

from pathlib import Path
import os
import tempfile

import pytest


def test_imports() -> None:
    """Test that all target functions can be imported."""
    from src.core import (  # noqa: F401
        decompose_case,
        run_parallel,
        reconstruct_case,
        ResidualData,
        ForceCoefficients,
        parse_log_file,
        generate_residual_plot,
        extract_force_coefficients,
    )


def test_residual_data() -> None:
    """Test ResidualData dataclass."""
    from src.core import ResidualData

    residual = ResidualData(field_name="Ux")
    residual.iterations = [1, 2, 3]
    residual.times = [0.0, 0.1, 0.2]
    residual.initial_residuals = [1.0, 0.1, 0.01]
    residual.final_residuals = [0.1, 0.01, 0.001]

    assert residual.field_name == "Ux"
    assert len(residual.iterations) == 3
    assert residual.final_residuals[-1] == 0.001


def test_force_coefficients() -> None:
    """Test ForceCoefficients dataclass."""
    from src.core import ForceCoefficients

    coeffs = ForceCoefficients()
    coeffs.times = [0.0, 0.1, 0.2]
    coeffs.cd = [1.2, 1.1, 1.0]
    coeffs.cl = [0.1, 0.2, 0.15]
    coeffs.cm = [0.05, 0.06, 0.05]

    assert len(coeffs.times) == 3
    assert coeffs.cd[0] == 1.2


def test_parse_log_file() -> None:
    """Test parsing a sample log file."""
    from src.core import parse_log_file

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
        log_path = f.name
        f.write(
            """
Time = 1
Solving for Ux, Initial residual = 1.0, Final residual = 0.1, No Iterations 5
Solving for Uy, Initial residual = 0.9, Final residual = 0.09, No Iterations 4

Time = 2
Solving for Ux, Initial residual = 0.1, Final residual = 0.01, No Iterations 3
Solving for Uy, Initial residual = 0.09, Final residual = 0.009, No Iterations 3
"""
        )

    try:
        residuals = parse_log_file(log_path)
    finally:
        os.unlink(log_path)

    assert "Ux" in residuals
    assert "Uy" in residuals

    ux_data = residuals["Ux"]
    assert len(ux_data.times) == 2
    assert ux_data.times[0] == 1.0
    assert ux_data.times[1] == 2.0
    assert ux_data.initial_residuals[0] == 1.0
    assert ux_data.final_residuals[1] == 0.01


def test_residual_plot() -> None:
    """Test generating residual plot."""
    pytest.importorskip("matplotlib")
    from src.core import ResidualData, generate_residual_plot

    residuals = {
        "Ux": ResidualData(
            field_name="Ux",
            iterations=[1, 2, 3, 4, 5],
            times=[0, 0.1, 0.2, 0.3, 0.4],
            initial_residuals=[1.0, 0.5, 0.2, 0.1, 0.05],
            final_residuals=[0.1, 0.05, 0.02, 0.01, 0.005],
        ),
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "residuals.png"
        result_path = generate_residual_plot(
            residuals=residuals,
            output_path=str(output_path),
            plot_type="final",
            log_scale=True,
            title="Test Residuals",
        )
        assert Path(result_path).exists()
        assert Path(result_path).stat().st_size > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
