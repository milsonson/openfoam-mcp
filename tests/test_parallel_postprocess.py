"""
Unit tests for parallel computing and post-processing features.
"""

import pytest
from pathlib import Path
import tempfile
import os

from src.core import (
    # Parallel
    decompose_case,
    run_parallel,
    reconstruct_case,
    # Post-process
    ResidualData,
    ForceCoefficients,
    parse_log_file,
    generate_residual_plot,
    extract_force_coefficients,
    generate_force_coefficients_plot,
)


def test_residual_data_structure():
    """Test ResidualData dataclass."""
    residual = ResidualData(field_name="Ux")
    residual.iterations = [1, 2, 3]
    residual.times = [0.0, 0.1, 0.2]
    residual.initial_residuals = [1.0, 0.1, 0.01]
    residual.final_residuals = [0.1, 0.01, 0.001]

    assert residual.field_name == "Ux"
    assert len(residual.iterations) == 3
    assert residual.final_residuals[-1] == 0.001


def test_force_coefficients_structure():
    """Test ForceCoefficients dataclass."""
    coeffs = ForceCoefficients()
    coeffs.times = [0.0, 0.1, 0.2]
    coeffs.cd = [1.2, 1.1, 1.0]
    coeffs.cl = [0.1, 0.2, 0.15]
    coeffs.cm = [0.05, 0.06, 0.05]

    assert len(coeffs.times) == 3
    assert coeffs.cd[0] == 1.2


def test_parse_log_file_with_sample_data():
    """Test parsing a sample log file."""
    # Create a temporary log file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log') as f:
        log_path = f.name
        f.write("""
Time = 1
Solving for Ux, Initial residual = 1.0, Final residual = 0.1, No Iterations 5
Solving for Uy, Initial residual = 0.9, Final residual = 0.09, No Iterations 4

Time = 2
Solving for Ux, Initial residual = 0.1, Final residual = 0.01, No Iterations 3
Solving for Uy, Initial residual = 0.09, Final residual = 0.009, No Iterations 3
""")

    try:
        # Parse the log file
        residuals = parse_log_file(log_path)

        # Check results
        assert 'Ux' in residuals
        assert 'Uy' in residuals

        ux_data = residuals['Ux']
        assert len(ux_data.times) == 2
        assert ux_data.times[0] == 1.0
        assert ux_data.times[1] == 2.0
        assert ux_data.initial_residuals[0] == 1.0
        assert ux_data.final_residuals[1] == 0.01

    finally:
        # Clean up
        os.unlink(log_path)


def test_parse_log_file_handles_invalid_utf8_bytes():
    """Parser should tolerate non-UTF8 bytes by replacing invalid sequences."""
    with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.log') as f:
        log_path = f.name
        f.write(
            b"Time = 1\n"
            b"Solving for Ux, Initial residual = 1.0, Final residual = 0.1, No Iterations 5\n"
            b"\xff\xfe\n"
            b"Time = 2\n"
            b"Solving for Ux, Initial residual = 0.1, Final residual = 0.01, No Iterations 3\n"
        )

    try:
        residuals = parse_log_file(log_path)
    finally:
        os.unlink(log_path)

    assert "Ux" in residuals
    assert residuals["Ux"].final_residuals[-1] == 0.01


def test_generate_residual_plot():
    """Test generating residual plot (requires matplotlib)."""
    try:
        import matplotlib
    except ImportError:
        pytest.skip("matplotlib not installed")

    # Create sample residual data
    residuals = {
        'Ux': ResidualData(
            field_name='Ux',
            iterations=[1, 2, 3, 4, 5],
            times=[0, 0.1, 0.2, 0.3, 0.4],
            initial_residuals=[1.0, 0.5, 0.2, 0.1, 0.05],
            final_residuals=[0.1, 0.05, 0.02, 0.01, 0.005]
        ),
        'p': ResidualData(
            field_name='p',
            iterations=[1, 2, 3, 4, 5],
            times=[0, 0.1, 0.2, 0.3, 0.4],
            initial_residuals=[1.0, 0.6, 0.3, 0.15, 0.08],
            final_residuals=[0.2, 0.1, 0.05, 0.02, 0.01]
        ),
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "residuals.png"

        # Generate plot
        result_path = generate_residual_plot(
            residuals=residuals,
            output_path=str(output_path),
            plot_type="final",
            log_scale=True,
            title="Test Residuals"
        )

        # Check that file was created
        assert Path(result_path).exists()
        assert Path(result_path).stat().st_size > 0


def test_force_coefficients_plot():
    """Test generating force coefficients plot (requires matplotlib)."""
    try:
        import matplotlib
    except ImportError:
        pytest.skip("matplotlib not installed")

    # Create sample force coefficient data
    coeffs = ForceCoefficients(
        times=[0.0, 0.1, 0.2, 0.3, 0.4],
        cd=[1.2, 1.15, 1.1, 1.08, 1.05],
        cl=[0.05, 0.08, 0.1, 0.09, 0.07],
        cm=[0.02, 0.03, 0.035, 0.03, 0.025]
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "force_coeffs.png"

        # Generate plot
        result_path = generate_force_coefficients_plot(
            coeffs=coeffs,
            output_path=str(output_path),
            title="Test Force Coefficients"
        )

        # Check that file was created
        assert Path(result_path).exists()
        assert Path(result_path).stat().st_size > 0


def test_import_parallel_functions():
    """Test that parallel functions can be imported."""
    # Just verify imports work
    assert callable(decompose_case)
    assert callable(run_parallel)
    assert callable(reconstruct_case)


def test_import_postprocess_functions():
    """Test that post-processing functions can be imported."""
    # Just verify imports work
    assert callable(parse_log_file)
    assert callable(generate_residual_plot)
    assert callable(extract_force_coefficients)
    assert callable(generate_force_coefficients_plot)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
